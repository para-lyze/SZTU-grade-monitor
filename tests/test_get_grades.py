import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from get_grades import (
    STARTUP_NOTIFIED_KEY,
    Config,
    GradeMonitorError,
    build_email,
    build_startup_email,
    changed_fields,
    estimate_incremental_gpa,
    load_state,
    parse_summary,
    run,
    save_state,
    state_with_startup_marker,
)


SAMPLE_PAGE = """
成绩汇总
所修门数：12
所修总学分: 31.5
平均学分绩点：3.42
专业绩点排名/专业总人数：8/120
"""


class ParseSummaryTests(unittest.TestCase):
    def test_extracts_complete_summary(self):
        self.assertEqual(
            parse_summary(SAMPLE_PAGE),
            {
                "所修门数": "12",
                "所修总学分": "31.5",
                "平均学分绩点": "3.42",
                "排名": "8/120",
            },
        )

    def test_rejects_partial_page(self):
        with self.assertRaisesRegex(GradeMonitorError, "缺少字段"):
            parse_summary("所修门数：12")


class ChangeTests(unittest.TestCase):
    def test_detects_ranking_only_change(self):
        previous = parse_summary(SAMPLE_PAGE)
        current = dict(previous, 排名="7/120")
        self.assertEqual(changed_fields(previous, current), ["排名"])

    def test_estimates_incremental_gpa(self):
        previous = {"所修总学分": "30", "平均学分绩点": "3.00"}
        current = {"所修总学分": "33", "平均学分绩点": "3.09"}
        self.assertEqual(estimate_incremental_gpa(previous, current), "3.99")

    def test_builds_change_and_startup_emails(self):
        current = parse_summary(SAMPLE_PAGE)
        change_body = build_email(None, current, ["所修门数"])
        startup_body = build_startup_email(current)

        self.assertIn("成绩汇总发生变化", change_body)
        self.assertIn("所修门数", change_body)
        self.assertIn("成绩监控启动成功", startup_body)


class StateTests(unittest.TestCase):
    def test_state_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            expected = parse_summary(SAMPLE_PAGE)
            save_state(path, expected)
            self.assertEqual(load_state(path), expected)


class ConfigTests(unittest.TestCase):
    def test_missing_secret_is_an_error(self):
        with self.assertRaisesRegex(GradeMonitorError, "STU_PWD"):
            Config.from_env(
                {
                    "STU_ID": "example",
                    "MAIL_USER": "sender@example.com",
                    "MAIL_PASS": "example-token",
                    "MAIL_RECEIVER": "receiver@example.com",
                }
            )


class RunTransactionTests(unittest.TestCase):
    def make_config(self, directory: str) -> Config:
        return Config(
            username="student",
            password="password",
            mail_user="sender@example.com",
            mail_password="token",
            mail_receiver="receiver@example.com",
            state_path=Path(directory) / "state.json",
            debug_dir=Path(directory) / "debug",
        )

    @patch("get_grades.grade_detail.save_courses")
    @patch("get_grades.grade_detail.compare_courses", return_value=[])
    @patch("get_grades.grade_detail.load_courses", return_value=None)
    @patch("get_grades.grade_detail.read_courses", return_value=[])
    @patch("get_grades.capture_debug")
    @patch("get_grades.send_email", side_effect=GradeMonitorError("SMTP failed"))
    @patch("get_grades.read_summary", return_value=parse_summary(SAMPLE_PAGE))
    @patch("get_grades.click_query")
    @patch("get_grades.login")
    @patch("get_grades.create_driver")
    def test_email_failure_does_not_advance_state(
        self, create_driver, _login, _click, _read, _send, _capture,
        _read_courses, _load_courses, _compare_courses, _save_courses,
    ):
        create_driver.return_value = Mock()
        with tempfile.TemporaryDirectory() as directory:
            config = self.make_config(directory)
            with self.assertRaisesRegex(GradeMonitorError, "SMTP failed"):
                run(config)
            self.assertFalse(config.state_path.exists())

    @patch("get_grades.grade_detail.save_courses")
    @patch("get_grades.grade_detail.compare_courses", return_value=[])
    @patch("get_grades.grade_detail.load_courses", return_value=None)
    @patch("get_grades.grade_detail.read_courses", return_value=[])
    @patch("get_grades.send_email")
    @patch("get_grades.read_summary", return_value=parse_summary(SAMPLE_PAGE))
    @patch("get_grades.click_query")
    @patch("get_grades.login")
    @patch("get_grades.create_driver")
    def test_email_success_advances_state(
        self, create_driver, _login, _click, _read, send, _capture=None,
        _read_courses=None, _load_courses=None, _compare_courses=None,
        _save_courses=None,
    ):
        create_driver.return_value = Mock()
        with tempfile.TemporaryDirectory() as directory:
            config = self.make_config(directory)
            self.assertTrue(run(config))
            send.assert_called_once()
            self.assertEqual(
                load_state(config.state_path),
                state_with_startup_marker(parse_summary(SAMPLE_PAGE)),
            )
            self.assertEqual(send.call_args.args[1], "成绩监控启动成功")

    @patch("get_grades.grade_detail.save_courses")
    @patch("get_grades.grade_detail.compare_courses", return_value=[])
    @patch("get_grades.grade_detail.load_courses", return_value=None)
    @patch("get_grades.grade_detail.read_courses", return_value=[])
    @patch("get_grades.send_email")
    @patch("get_grades.read_summary", return_value=parse_summary(SAMPLE_PAGE))
    @patch("get_grades.click_query")
    @patch("get_grades.login")
    @patch("get_grades.create_driver")
    def test_legacy_state_gets_one_startup_email(
        self, create_driver, _login, _click, _read, send,
        _read_courses=None, _load_courses=None, _compare_courses=None,
        _save_courses=None,
    ):
        create_driver.return_value = Mock()
        with tempfile.TemporaryDirectory() as directory:
            config = self.make_config(directory)
            save_state(config.state_path, parse_summary(SAMPLE_PAGE))

            self.assertTrue(run(config))
            send.assert_called_once()
            self.assertEqual(
                load_state(config.state_path)[STARTUP_NOTIFIED_KEY], "true"
            )

    @patch("get_grades.grade_detail.save_courses")
    @patch("get_grades.grade_detail.compare_courses", return_value=[])
    @patch("get_grades.grade_detail.load_courses", return_value=None)
    @patch("get_grades.grade_detail.read_courses", return_value=[])
    @patch("get_grades.send_email")
    @patch("get_grades.read_summary", return_value=parse_summary(SAMPLE_PAGE))
    @patch("get_grades.click_query")
    @patch("get_grades.login")
    @patch("get_grades.create_driver")
    def test_startup_email_is_not_repeated(
        self, create_driver, _login, _click, _read, send,
        _read_courses=None, _load_courses=None, _compare_courses=None,
        _save_courses=None,
    ):
        create_driver.return_value = Mock()
        with tempfile.TemporaryDirectory() as directory:
            config = self.make_config(directory)
            save_state(
                config.state_path,
                state_with_startup_marker(parse_summary(SAMPLE_PAGE)),
            )

            self.assertFalse(run(config))
            send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
