import shutil
import time

import pytest

from queue_bot.cdp_browser import BrowserProcess
from queue_bot.mock_page import MockServer
from queue_bot.queue_monitor import (
    has_join_button,
    has_leave_button,
    has_task_ready,
    is_cloudflare_challenge,
    parse_queue_state,
)

MAC_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

EXECUTABLE = (
    shutil.which("chromium-browser")
    or shutil.which("chromium")
    or shutil.which("google-chrome")
    or shutil.which(MAC_CHROME)
    or (MAC_CHROME if __import__("os").path.exists(MAC_CHROME) else None)
)

pytestmark = pytest.mark.skipif(
    not EXECUTABLE,
    reason="No Chrome/Chromium binary found; integration tests need a real browser",
)


@pytest.fixture(scope="module")
def server():
    mock = MockServer()
    base = mock.start()
    yield base
    mock.stop()


@pytest.fixture()
def browser(tmp_path):
    proc = BrowserProcess(
        debug_port=0,
        executable=EXECUTABLE,
        user_data_dir=str(tmp_path / "profile"),
        headless=True,
        startup_timeout=60,
    )
    yield proc
    proc.close()


@pytest.mark.parametrize("debug_port", [9333, 9334])
def test_launch_navigate_and_find_button(server, browser, debug_port):
    browser.debug_port = debug_port
    browser.start()
    browser.navigate(server + "?pos=500")
    text = browser.inner_text()
    assert "Task queue" in text
    assert has_join_button(text)
    assert browser.has_button("Join queue")


def test_click_until_joined(server, browser):
    browser.debug_port = 9335
    browser.start()
    browser.navigate(server + "?fast=1&join=5&pos=600")

    for _ in range(20):
        try:
            browser.click_button("Join queue")
        except Exception:  # noqa: BLE001
            pass
        text = browser.inner_text()
        if has_leave_button(text) and not has_join_button(text):
            break
        time.sleep(0.3)

    text = browser.inner_text()
    assert has_leave_button(text)
    qs = parse_queue_state(text)
    assert qs is not None
    assert qs.position == 600
    assert qs.total == 700

    body = browser.evaluate("document.getElementById('ctl').textContent")
    assert body.strip() == "Leave queue"


def test_leave_flow_with_modal(server, browser):
    browser.debug_port = 9336
    browser.start()
    browser.navigate(server + "?fast=1&join=1&pos=50")
    for _ in range(5):
        browser.click_button("Join queue")
        if has_leave_button(browser.inner_text()):
            break
        time.sleep(0.2)

    assert browser.click_button("Leave queue")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if browser.count_button("Leave queue") >= 2:
            break
        time.sleep(0.3)
    assert browser.count_button("Leave queue") >= 2
    assert browser.click_button("Leave queue", first=False)

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if has_join_button(browser.inner_text()):
            break
        time.sleep(0.3)
    assert has_join_button(browser.inner_text())


def test_task_ready_detection(server, browser):
    browser.debug_port = 9337
    browser.start()
    browser.navigate(server + "?fast=1&ready=1&join=1&pos=30")
    for _ in range(5):
        browser.click_button("Join queue")
        time.sleep(0.2)

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if has_task_ready(browser.inner_text()):
            break
        time.sleep(1)
    assert has_task_ready(browser.inner_text())


def test_cloudflare_sentinel(server, browser):
    browser.debug_port = 9338
    browser.start()
    browser.navigate(server + "?cf=1")
    text = browser.inner_text()
    assert "Verifying you are human" in text
    assert is_cloudflare_challenge(text)
    assert not has_join_button(text)


def test_redacted_screenshot_pipeline(server, browser):
    browser.debug_port = 9339
    browser.start()
    browser.navigate(server)
    box = browser.button_box("Join queue")
    assert box is not None
    png = browser.screenshot()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"

    from queue_bot.redaction import redact_screenshot

    shot = redact_screenshot(
        png,
        reveal_rects=[(box["x"], box["y"], box["w"], box["h"])],
        page_size=browser.viewport(),
        mosaic_width=160,
    )
    assert shot[:4] == b"\xff\xd8\xff\xe0"