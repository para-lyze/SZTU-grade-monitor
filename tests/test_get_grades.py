import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from selenium.common.exceptions import TimeoutException, WebDriverException

from get_grades import (
    STARTUP_NOTIFIED_KEY,
    Config,
    GradeMonitorError,
    build_email,
    build_startup_email,
    changed_fields,
    create_driver,
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


class DriverConfigTests(unittest.TestCase):
    @patch("get_grades.webdriver.Firefox")
    def test_prefers_headless_firefox(self, firefox):
        driver = Mock()
        firefox.return_value = driver

        self.assertIs(create_driver(), driver)

        options = firefox.call_args.kwargs["options"]
        self.assertIn("-headless", options.arguments)
        driver.set_page_load_timeout.assert_called_once_with(45)

    @patch("get_grades.webdriver.Chrome")
    @patch(
        "get_grades.webdriver.Firefox",
        side_effect=WebDriverException("Firefox unavailable"),
    )
    def test_falls_back_to_nonblocking_chrome(self, _firefox, chrome):
        driver = Mock()
        chrome.return_value = driver

        self.assertIs(create_driver(), driver)

        options = chrome.call_args.kwargs["options"]
        self.assertEqual(options.page_load_strategy, "none")
        driver.set_page_load_timeout.assert_called_once_with(45)


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

    @patch("get_grades.capture_debug")
    @patch("get_grades.send_email", side_effect=GradeMonitorError("SMTP failed"))
    @patch("get_grades.read_summary", return_value=parse_summary(SAMPLE_PAGE))
    @patch("get_grades.click_query")
    @patch("get_grades.login")
    @patch("get_grades.create_driver")
    def test_email_failure_does_not_advance_state(
        self, create_driver, _login, _click, _read, _send, _capture
    ):
        create_driver.return_value = Mock()
        with tempfile.TemporaryDirectory() as directory:
            config = self.make_config(directory)
            with self.assertRaisesRegex(GradeMonitorError, "SMTP failed"):
                run(config)
            self.assertFalse(config.state_path.exists())

    @patch("get_grades.send_email")
    @patch("get_grades.read_summary", return_value=parse_summary(SAMPLE_PAGE))
    @patch("get_grades.click_query")
    @patch("get_grades.login")
    @patch("get_grades.create_driver")
    def test_email_success_advances_state(
        self, create_driver, _login, _click, _read, send, _capture=None
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

    @patch("get_grades.send_email")
    @patch("get_grades.read_summary", return_value=parse_summary(SAMPLE_PAGE))
    @patch("get_grades.click_query")
    @patch("get_grades.login")
    @patch("get_grades.create_driver")
    def test_legacy_state_gets_one_startup_email(
        self, create_driver, _login, _click, _read, send
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

    @patch("get_grades.send_email")
    @patch("get_grades.read_summary", return_value=parse_summary(SAMPLE_PAGE))
    @patch("get_grades.click_query")
    @patch("get_grades.login")
    @patch("get_grades.create_driver")
    def test_startup_email_is_not_repeated(
        self, create_driver, _login, _click, _read, send
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

    def test_transient_timeout_retries_before_sending_email(self):
        first_driver = Mock()
        second_driver = Mock()
        current = parse_summary(SAMPLE_PAGE)

        with tempfile.TemporaryDirectory() as directory:
            config = self.make_config(directory)
            with (
                patch(
                    "get_grades.create_driver",
                    side_effect=[first_driver, second_driver],
                ) as create_driver,
                patch(
                    "get_grades.login",
                    side_effect=[TimeoutException("temporary"), None],
                ),
                patch("get_grades.click_query"),
                patch("get_grades.read_summary", return_value=current),
                patch("get_grades.capture_debug") as capture_debug,
                patch("get_grades.time.sleep") as sleep,
                patch("get_grades.send_email") as send,
            ):
                self.assertTrue(run(config))

            self.assertEqual(create_driver.call_count, 2)
            capture_debug.assert_called_once_with(first_driver, config.debug_dir)
            sleep.assert_called_once_with(10)
            send.assert_called_once()
            first_driver.quit.assert_called_once()
            second_driver.quit.assert_called_once()

    def test_repeated_timeout_never_sends_email_or_updates_state(self):
        first_driver = Mock()
        second_driver = Mock()

        with tempfile.TemporaryDirectory() as directory:
            config = self.make_config(directory)
            with (
                patch(
                    "get_grades.create_driver",
                    side_effect=[first_driver, second_driver],
                ),
                patch(
                    "get_grades.login",
                    side_effect=TimeoutException("temporary"),
                ),
                patch("get_grades.capture_debug"),
                patch("get_grades.time.sleep"),
                patch("get_grades.send_email") as send,
            ):
                with self.assertRaisesRegex(GradeMonitorError, "连续 2 次加载超时"):
                    run(config)

            send.assert_not_called()
            self.assertFalse(config.state_path.exists())
            first_driver.quit.assert_called_once()
            second_driver.quit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
