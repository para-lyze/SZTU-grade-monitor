"""深圳技术大学成绩监控。

凭据只从环境变量读取。脚本在查询或通知失败时以非零状态退出，只有邮件
发送成功后才更新本地状态，避免 GitHub Actions 出现“假成功”。
"""

from __future__ import annotations

import html
import json
import os
import re
import smtplib
import sys
import tempfile
import time
from dataclasses import dataclass
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr
from pathlib import Path
from typing import Mapping

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


AUTH_URL = "https://auth.sztu.edu.cn/idp/authcenter/ActionAuthChain?entityId=jiaowu"
GRADES_URL = "https://jwxt.sztu.edu.cn/jsxsd/kscj/cjcx_frm"
FIELD_PATTERNS = {
    "所修门数": r"所修门数[:：]?\s*(\d+)",
    "所修总学分": r"所修总学分[:：]?\s*([\d.]+)",
    "平均学分绩点": r"平均学分绩点[:：]?\s*([\d.]+)",
    "排名": r"专业绩点排名/专业总人数[:：]?\s*([\d/]+)",
}
STARTUP_NOTIFIED_KEY = "_startup_notified"


class GradeMonitorError(RuntimeError):
    """查询或通知无法可靠完成。"""


@dataclass(frozen=True)
class Config:
    username: str
    password: str
    mail_user: str
    mail_password: str
    mail_receiver: str
    state_path: Path
    debug_dir: Path

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Config":
        values = os.environ if env is None else env
        required = ("STU_ID", "STU_PWD", "MAIL_USER", "MAIL_PASS", "MAIL_RECEIVER")
        missing = [name for name in required if not values.get(name, "").strip()]
        if missing:
            raise GradeMonitorError(f"缺少必要环境变量：{', '.join(missing)}")

        return cls(
            username=values["STU_ID"].strip(),
            password=values["STU_PWD"],
            mail_user=values["MAIL_USER"].strip(),
            mail_password=values["MAIL_PASS"],
            mail_receiver=values["MAIL_RECEIVER"].strip(),
            state_path=Path(values.get("GRADE_STATE_PATH", ".local/grade_history.json")),
            debug_dir=Path(values.get("DEBUG_DIR", ".local/debug")),
        )


def parse_summary(content: str) -> dict[str, str]:
    """从成绩页面文本中提取完整的汇总数据。"""
    data: dict[str, str] = {}
    for name, pattern in FIELD_PATTERNS.items():
        match = re.search(pattern, content)
        if match:
            data[name] = match.group(1)

    missing = [name for name in FIELD_PATTERNS if name not in data]
    if missing:
        raise GradeMonitorError(f"成绩页面缺少字段：{', '.join(missing)}")
    return data


def load_state(path: Path) -> dict[str, str] | None:
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GradeMonitorError(f"无法读取状态文件 {path}: {exc}") from exc
    if not isinstance(state, dict):
        raise GradeMonitorError(f"状态文件 {path} 格式不正确")
    return {str(key): str(value) for key, value in state.items()}


def save_state(path: Path, data: Mapping[str, str]) -> None:
    """原子写入状态，避免中途退出留下损坏的 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(dict(data), ensure_ascii=False, indent=2) + "\n"
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as temp_file:
            temp_file.write(payload)
            temp_name = temp_file.name
        os.replace(temp_name, path)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def changed_fields(
    previous: Mapping[str, str] | None, current: Mapping[str, str]
) -> list[str]:
    if previous is None:
        return list(current)
    return [name for name, value in current.items() if previous.get(name) != value]


def startup_notified(state: Mapping[str, str] | None) -> bool:
    return bool(state and state.get(STARTUP_NOTIFIED_KEY) == "true")


def state_with_startup_marker(current: Mapping[str, str]) -> dict[str, str]:
    return {**current, STARTUP_NOTIFIED_KEY: "true"}


def estimate_incremental_gpa(
    previous: Mapping[str, str] | None, current: Mapping[str, str]
) -> str | None:
    """根据累计值估算新增课程的加权绩点；累计值有四舍五入误差。"""
    if not previous:
        return None
    try:
        old_credits = float(previous["所修总学分"])
        new_credits = float(current["所修总学分"])
        delta = new_credits - old_credits
        if delta <= 0:
            return None
        value = (
            new_credits * float(current["平均学分绩点"])
            - old_credits * float(previous["平均学分绩点"])
        ) / delta
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None
    return f"{value:.2f}"


def build_email(
    previous: Mapping[str, str] | None,
    current: Mapping[str, str],
    fields: list[str],
) -> str:
    rows = []
    for name in fields:
        old_value = "首次记录" if previous is None else previous.get(name, "未知")
        rows.append(
            "<tr>"
            f"<td>{html.escape(name)}</td>"
            f"<td>{html.escape(str(old_value))}</td>"
            f"<td>{html.escape(str(current[name]))}</td>"
            "</tr>"
        )
    estimate = estimate_incremental_gpa(previous, current)
    estimate_html = (
        f"<p>新增课程加权绩点估算：<strong>{html.escape(estimate)}</strong></p>"
        if estimate
        else ""
    )
    return (
        "<h3>成绩汇总发生变化</h3>"
        + estimate_html
        + "<table border='1' cellpadding='6' cellspacing='0'>"
        + "<thead><tr><th>字段</th><th>之前</th><th>现在</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
        + "<p><small>增量绩点由四舍五入后的累计数据推算，仅供参考。</small></p>"
    )


def build_startup_email(current: Mapping[str, str]) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(name)}</td>"
        f"<td>{html.escape(str(value))}</td>"
        "</tr>"
        for name, value in current.items()
    )
    return (
        "<h3>成绩监控启动成功</h3>"
        "<p>脚本已成功登录教务系统并读取成绩汇总，后续检测到变化时会再次通知。</p>"
        "<table border='1' cellpadding='6' cellspacing='0'>"
        "<thead><tr><th>字段</th><th>当前值</th></tr></thead><tbody>"
        + rows
        + "</tbody></table>"
    )


def send_email(config: Config, title: str, body: str) -> None:
    message = MIMEText(body, "html", "utf-8")
    message["From"] = formataddr(("GPA 监控助手", config.mail_user))
    message["To"] = formataddr(("同学", config.mail_receiver))
    message["Subject"] = Header(title, "utf-8")

    try:
        with smtplib.SMTP_SSL("smtp.qq.com", 465, timeout=30) as smtp:
            smtp.login(config.mail_user, config.mail_password)
            smtp.sendmail(
                config.mail_user, [config.mail_receiver], message.as_string()
            )
    except (OSError, smtplib.SMTPException) as exc:
        raise GradeMonitorError(f"邮件发送失败：{exc}") from exc


def create_driver() -> WebDriver:
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    try:
        driver = webdriver.Chrome(options=options)
        driver.set_page_load_timeout(45)
        return driver
    except WebDriverException as exc:
        raise GradeMonitorError(f"浏览器启动失败：{exc}") from exc


def login(driver: WebDriver, config: Config, wait: WebDriverWait) -> None:
    print("1. 登录统一认证……")
    driver.get(AUTH_URL)
    username = wait.until(EC.visibility_of_element_located((By.ID, "j_username")))
    password = driver.find_element(By.ID, "j_password")
    username.send_keys(config.username)
    password.send_keys(config.password)
    driver.find_element(By.ID, "loginButton").click()

    try:
        wait.until(
            lambda current_driver: not current_driver.find_elements(By.ID, "loginButton")
        )
    except TimeoutException as exc:
        raise GradeMonitorError("登录未完成，可能是密码错误、验证码或认证页面已变化") from exc


def click_query(driver: WebDriver) -> None:
    def click_in_current_context() -> bool:
        buttons = driver.find_elements(By.ID, "btn_query")
        if not buttons:
            return False
        driver.execute_script(
            "const e=document.getElementById('kksj');"
            "if(e){e.value='';e.dispatchEvent(new Event('change',{bubbles:true}));}"
        )
        driver.execute_script("arguments[0].click();", buttons[0])
        return True

    driver.switch_to.default_content()
    if click_in_current_context():
        return

    frames = driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
    for frame in frames:
        try:
            driver.switch_to.default_content()
            driver.switch_to.frame(frame)
            if click_in_current_context():
                return
        except WebDriverException:
            continue
    raise GradeMonitorError("找不到成绩查询按钮，教务系统页面结构可能已变化")


def read_summary(driver: WebDriver, wait: WebDriverWait) -> dict[str, str]:
    driver.switch_to.default_content()
    try:
        result_frame = wait.until(
            EC.presence_of_element_located((By.ID, "cjcx_list_frm"))
        )
        driver.switch_to.frame(result_frame)
    except TimeoutException as exc:
        raise GradeMonitorError("找不到成绩结果窗口") from exc

    try:
        wait.until(
            lambda current_driver: "所修门数"
            in current_driver.find_element(By.TAG_NAME, "body").text
        )
    except TimeoutException as exc:
        raise GradeMonitorError("成绩结果在等待时间内没有加载完成") from exc

    content = driver.find_element(By.TAG_NAME, "body").text
    return parse_summary(content)


def capture_debug(driver: WebDriver, debug_dir: Path) -> None:
    """保存私有诊断文件；不要把该目录上传到公开仓库。"""
    try:
        debug_dir.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        driver.save_screenshot(str(debug_dir / f"failure-{timestamp}.png"))
        (debug_dir / f"failure-{timestamp}.html").write_text(
            driver.page_source, encoding="utf-8"
        )
        print(f"诊断文件已保存到 {debug_dir}")
    except (OSError, WebDriverException):
        print("警告：无法保存诊断文件", file=sys.stderr)


def run(config: Config) -> bool:
    driver: WebDriver | None = None
    try:
        driver = create_driver()
        wait = WebDriverWait(driver, 30)
        login(driver, config, wait)

        print("2. 打开成绩页面……")
        driver.get(GRADES_URL)
        print("3. 查询全部学期……")
        click_query(driver)
        print("4. 读取成绩汇总……")
        current = read_summary(driver, wait)
        previous = load_state(config.state_path)

        if not startup_notified(previous):
            print("首次成功运行，发送启动通知……")
            send_email(
                config,
                "成绩监控启动成功",
                build_startup_email(current),
            )
            print("启动通知邮件发送成功")
            save_state(config.state_path, state_with_startup_marker(current))
            print("状态保存成功")
            return True

        fields = changed_fields(previous, current)

        if not fields:
            print("成绩汇总无变化")
            return False

        print(f"检测到变化字段：{', '.join(fields)}")
        send_email(config, "成绩单更新提醒", build_email(previous, current, fields))
        print("邮件发送成功")
        save_state(config.state_path, state_with_startup_marker(current))
        print("状态保存成功")
        return True
    except Exception:
        if driver is not None:
            capture_debug(driver, config.debug_dir)
        raise
    finally:
        if driver is not None:
            driver.quit()


def main() -> int:
    try:
        config = Config.from_env()
        run(config)
        return 0
    except GradeMonitorError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"未预期错误：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
