from queue_bot.telegram_client import (
    HELP,
    LEAVE,
    SCREENSHOT,
    START,
    STATUS,
    STOP,
    UNKNOWN,
    parse_command,
)


def test_start_variants():
    for text in ("start", "Start", "GO!", "Go!!", "begin", "join", "Start now"):
        assert parse_command(text) == START


def test_leave_variants():
    for text in ("leave", "Leave", "exit", "quit", "get out"):
        assert parse_command(text) == LEAVE


def test_status_screenshot_help():
    assert parse_command("status") == STATUS
    assert parse_command("where am i") == STATUS
    assert parse_command("report?") == STATUS
    assert parse_command("screenshot") == SCREENSHOT
    assert parse_command("pic") == SCREENSHOT
    assert parse_command("help") == HELP


def test_stop_variants():
    for text in ("stop", "abort", "halt", "cancel", "kill"):
        assert parse_command(text) == STOP


def test_unknown():
    assert parse_command("hello there") == UNKNOWN
    assert parse_command("") == UNKNOWN
    assert parse_command(None) == UNKNOWN