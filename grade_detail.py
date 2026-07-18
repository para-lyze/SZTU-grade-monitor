"""课程明細監控。

從成績頁面讀取各科課程成績，比對變化，生成郵件內容。
"""

from __future__ import annotations

import html
import json
import os
import tempfile
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


class CourseError(RuntimeError):
    """课程明细查询失败。"""

COURSES_STATE_SUFFIX = ".courses.json"


@dataclass(frozen=True)
class Course:
    """一门课程的成绩记录。"""

    name: str
    semester: str
    score: str
    credit: str
    gpa: str


KEYWORDS = ["课程", "成绩", "学分", "绩点"]


def _find_course_table(driver: WebDriver) -> Any:
    """在當前 frame 中找課程成績表格，先找表頭含有關鍵詞的，找不到則取行數最多的。"""
    tables = driver.find_elements(By.CSS_SELECTOR, "table")
    for table in tables:
        try:
            header_text = table.find_element(By.TAG_NAME, "tr").text
            if all(kw in header_text for kw in KEYWORDS):
                return table
        except WebDriverException:
            continue
    # 备用：取行数最多的
    tables_with_rows = sorted(
        tables,
        key=lambda t: len(t.find_elements(By.TAG_NAME, "tr")),
        reverse=True,
    )
    return tables_with_rows[0] if tables_with_rows else None


def _build_column_map(table) -> dict[str, int]:
    """讀取表頭，返回欄位名到列索引的映射。"""
    header_row = table.find_element(By.TAG_NAME, "tr")
    headers = [cell.text.strip() for cell in header_row.find_elements(By.TAG_NAME, "th")]
    if not headers:
        headers = [cell.text.strip() for cell in header_row.find_elements(By.TAG_NAME, "td")]

    mapping: dict[str, int] = {}
    for i, h in enumerate(headers):
        if not h:
            continue
        if "课程代码" in h or "课程编号" in h:
            mapping["代码"] = i
        elif "课程名称" in h:
            mapping["名称"] = i
        elif "课程" in h and "名称" not in mapping and "代码" not in mapping:
            mapping["名称"] = i
        elif "开课学期" in h or "学年" in h:
            mapping["学期"] = i
        elif "总评成绩" in h or "总评" in h:
            mapping["总评成绩"] = i
        elif "成绩" in h and "总评" not in h and "成绩" not in mapping:
            mapping["成绩"] = i
        elif "学分" in h and "学分" not in mapping:
            mapping["学分"] = i
        elif "绩点" in h and "绩点" not in mapping:
            mapping["绩点"] = i
    return mapping


def _is_summary_row(texts: list[str]) -> bool:
    joined = "".join(texts)
    return any(kw in joined for kw in ("所修", "平均", "专业", "合计", "总", "小计"))


def _parse_course_row(cells, col_map: dict[str, int]) -> dict[str, str] | None:
    """根据列映射解析一行课程数据。"""
    texts = [cell.text.strip() for cell in cells]
    if not any(texts) or _is_summary_row(texts):
        return None

    code = ""
    name = ""
    if "代码" in col_map:
        code = texts[col_map["代码"]] if col_map["代码"] < len(texts) else ""
    if "名称" in col_map:
        name = texts[col_map["名称"]] if col_map["名称"] < len(texts) else ""
    if not name:
        name = code

    score = texts[col_map["成绩"]] if "成绩" in col_map and col_map["成绩"] < len(texts) else ""
    overall = texts[col_map["总评成绩"]] if "总评成绩" in col_map and col_map["总评成绩"] < len(texts) else ""
    semester = texts[col_map["学期"]] if "学期" in col_map and col_map["学期"] < len(texts) else ""
    credit = texts[col_map["学分"]] if "学分" in col_map and col_map["学分"] < len(texts) else ""
    gpa = texts[col_map["绩点"]] if "绩点" in col_map and col_map["绩点"] < len(texts) else ""

    if not name or not (score or overall):
        return None
    return {"code": code, "name": name, "semester": semester,
            "score": score, "overall": overall, "credit": credit, "gpa": gpa}


def read_courses(driver: WebDriver, wait: WebDriverWait) -> list[dict[str, str]]:
    """從成績結果 iframe 中讀取課程明細。"""
    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "table")))
    except TimeoutException:
        raise CourseError("成绩结果中没有找到表格") from None

    table = _find_course_table(driver)
    if table is None:
        raise CourseError("找不到课程成绩表格") from None

    col_map = _build_column_map(table)
    if "名称" not in col_map or "成绩" not in col_map:
        # 嘗試按常見列序猜測：名称、成绩、学分
        rows = table.find_elements(By.TAG_NAME, "tr")
        if len(rows) >= 2:
            sample_cells = rows[1].find_elements(By.TAG_NAME, "td")
            texts = [c.text.strip() for c in sample_cells]
            # 假設最少有 3 列：名称、…、成绩
            if len(texts) >= 3 and texts[0] and texts[-2].replace(".", "").isdigit():
                col_map = {"名称": 0, "成绩": -2, "学分": -1}
                if len(texts) >= 4 and texts[-3].replace(".", "").isdigit():
                    col_map = {"名称": 0, "成绩": -3, "学分": -2, "绩点": -1}

    rows = table.find_elements(By.TAG_NAME, "tr")[1:]  # 跳过表头
    courses: list[dict[str, str]] = []
    for row in rows:
        cells = row.find_elements(By.TAG_NAME, "td")
        if not cells:
            continue
        parsed = _parse_course_row(cells, col_map)
        if parsed:
            courses.append(parsed)

    return courses


def _course_key(course: dict[str, str]) -> str:
    """以课程代码+名称+学期作为唯一标识。"""
    return f"{course.get('code', '')}::{course.get('name', '')}::{course.get('semester', '')}"


def compare_courses(
    old: list[dict[str, str]] | None,
    new: list[dict[str, str]],
) -> list[dict[str, str]]:
    """比對新舊課程資料，返回变化列表。

    每项包含：code, name, semester, old_score, new_score, credit, gpa
    """
    if not old:
        # 首次读取，全部视为新增
        return [
            {
                "code": c.get("code", ""),
                "name": c["name"],
                "semester": c.get("semester", ""),
                "old_score": "",
                "new_score": c["score"],
                "overall": c.get("overall", ""),
                "credit": c.get("credit", ""),
                "gpa": c.get("gpa", ""),
            }
            for c in new
        ]

    old_map = {_course_key(c): c for c in old}
    changes: list[dict[str, str]] = []

    for course in new:
        key = _course_key(course)
        old_course = old_map.get(key)
        old_score = old_course["score"] if old_course else ""

        if old_score != course["score"]:
            changes.append(
                {
                    "code": course.get("code", ""),
                    "name": course["name"],
                    "semester": course.get("semester", ""),
                    "old_score": old_score,
                    "new_score": course["score"],
                    "overall": course.get("overall", ""),
                    "credit": course.get("credit", ""),
                    "gpa": course.get("gpa", ""),
                }
            )

    return changes


def build_courses_section(
    changes: list[dict[str, str]],
) -> str:
    """生成课程变化的 HTML 片段（显示新成绩，不显示旧成绩）。"""
    if not changes:
        return ""

    overall_col = any(c.get("overall", "") for c in changes)
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(c['semester'])}</td>"
        f"<td>{html.escape(c['code'])}</td>"
        f"<td>{html.escape(c['name'])}</td>"
        f"<td>{html.escape(c['new_score'])}</td>"
        + (f"<td>{html.escape(c['overall'])}</td>" if overall_col else "")
        + f"<td>{html.escape(c['credit'])}</td>"
        + f"<td>{html.escape(c['gpa'])}</td>"
        + "</tr>"
        for c in changes
    )

    overall_header = "<th>总评成绩</th>" if overall_col else ""
    return (
        "<h3>课程明细变化</h3>"
        "<table border='1' cellpadding='6' cellspacing='0'>"
        "<thead><tr>"
        "<th>开课学期</th><th>课程编号</th><th>课程名称</th><th>成绩</th>"
        + overall_header
        + "<th>学分</th><th>绩点</th>"
        + "</tr></thead><tbody>"
        + rows
        + "</tbody></table>"
        + "<p><small>仅列出有变化的课程。</small></p>"
    )


def _courses_state_path(state_path: Path) -> Path:
    """根据 grade_history.json 的路径，推导课程状态文件路径。"""
    return state_path.with_suffix(COURSES_STATE_SUFFIX)


def load_courses(state_path: Path) -> list[dict[str, str]] | None:
    """读取上次保存的课程数据。"""
    path = _courses_state_path(state_path)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        return None
    except (OSError, json.JSONDecodeError):
        return None


def save_courses(state_path: Path, courses: list[dict[str, str]]) -> None:
    """原子写入课程状态。"""
    path = _courses_state_path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(courses, ensure_ascii=False, indent=2) + "\n"
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as tmp:
            tmp.write(payload)
            temp_name = tmp.name
        os.replace(temp_name, path)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)
