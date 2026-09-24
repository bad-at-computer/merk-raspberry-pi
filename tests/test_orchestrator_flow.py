import os
import shutil
import threading
import time

import pytest

from queue_bot.config import Config
from queue_bot.mock_page import MockServer
from queue_bot.orchestrator import Orchestrator
from queue_bot.state import BotState

MAC_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

EXECUTABLE = (
    shutil.which("chromium-browser")
    or shutil.which("chromium")
    or shutil.which("google-chrome")
    or shutil.which(MAC_CHROME)
    or (MAC_CHROME if os.path.exists(MAC_CHROME) else None)
)

pytestmark = pytest.mark.skipif(
    not EXECUTABLE,
    reason="No Chrome/Chromium binary found; flow tests need a real browser",
)


class FakeTelegram:
    def __init__(self):
        self.texts: list[str] = []
        self.photos: int = 0

    def send_text(self, chat_id, text):
        self.texts.append(text)
        return True

    def send_photo(self, chat_id, photo, caption="", filename="screen.jpg"):
        self.photos += 1
        self.texts.append(f"[photo] {caption}")
        return True


@pytest.fixture(scope="module")
def server():
    mock = MockServer()
    base = mock.start()
    yield base
    mock.stop()


def _make_config(tmp_path, base, debug_port):
    cfg = Config()
    cfg.telegram_token = "fake-token"
    cfg.queue_url = base + "?fast=1&ready=1&join=3&pos=200"
    cfg.browser_executable = EXECUTABLE
    cfg.user_data_dir = str(tmp_path / "profile")
    cfg.debug_port = debug_port
    cfg.click_min = 0.05
    cfg.click_max = 0.1
    cfg.monitor_interval_s = 0.5
    cfg.join_button_wait_min = 1
    cfg.click_cap_min = 1
    cfg.leave_modal_timeout_s = 5
    cfg.leave_confirm_wait_s = 1
    cfg.leave_max_retries = 3
    cfg.heartbeat_enabled = False
    cfg.state_file = str(tmp_path / "state.json")
    return cfg


def _wait_for(fn, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if fn():
            return True
        time.sleep(0.5)
    return False


def test_full_join_monitor_ready_leave_flow(tmp_path, server):
    cfg = _make_config(tmp_path, server, 9341)
    state = BotState(cfg.state_file)
    orch = Orchestrator(cfg, state)
    fake = FakeTelegram()
    orch.telegram = fake
    orch.control.active = True

    thread = threading.Thread(
        target=orch._session_worker, args=(1, False), name="test-session", daemon=True
    )
    thread.start()

    assert _wait_for(lambda: any("Ready" in t for t in fake.texts)), fake.texts

    joined = [t for t in fake.texts if t.startswith("Queue position") and "in the queue" in t]
    assert joined, fake.texts
    assert "of 700" in joined[0]

    orch.control.request_leave()
    assert _wait_for(lambda: not thread.is_alive(), timeout=60)
    assert any("Left the queue successfully" in t for t in fake.texts), fake.texts
    assert state.state == "idle"


def test_stop_interrupts_clicking(tmp_path, server):
    cfg = _make_config(tmp_path, server, 9342)
    cfg.queue_url = server + "?join=999&pos=600"
    state = BotState(cfg.state_file)
    orch = Orchestrator(cfg, state)
    fake = FakeTelegram()
    orch.telegram = fake
    orch.control.active = True

    thread = threading.Thread(
        target=orch._session_worker, args=(1, False), name="test-stop", daemon=True
    )
    thread.start()
    time.sleep(2)
    orch.control.request_stop()
    assert _wait_for(lambda: not thread.is_alive(), timeout=30)
    assert state.state == "idle"