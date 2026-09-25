import time

import queue_bot.orchestrator as orch_mod
from queue_bot.config import Config
from queue_bot.orchestrator import Orchestrator
from queue_bot.state import BotState


class FakeDateTime:
    hour = 9

    @classmethod
    def now(cls):
        return cls


class FakeTelegram:
    def __init__(self):
        self.texts: list[str] = []

    def send_text(self, chat_id, text):
        self.texts.append(text)
        return True


def _make_orchestrator(tmp_path, heartbeat_hour):
    cfg = Config()
    cfg.telegram_token = "fake-token"
    cfg.heartbeat_enabled = True
    cfg.heartbeat_hour = heartbeat_hour
    cfg.state_file = str(tmp_path / "state.json")
    state = BotState(cfg.state_file)
    orch = Orchestrator(cfg, state)
    orch.telegram = FakeTelegram()
    orch.state.chat_id = 123
    orch._last_beat_check = time.monotonic() - 60
    return orch


def test_beat_check_sends_heartbeat_when_hour_matches(tmp_path, monkeypatch):
    monkeypatch.setattr(orch_mod, "datetime", FakeDateTime)
    orch = _make_orchestrator(tmp_path, heartbeat_hour=9)
    orch._beat_check()
    assert orch.telegram.texts, "should send a heartbeat at the configured hour"
    assert "Pi alive" in orch.telegram.texts[0]


def test_beat_check_silent_when_hour_mismatch(tmp_path, monkeypatch):
    monkeypatch.setattr(orch_mod, "datetime", FakeDateTime)
    orch = _make_orchestrator(tmp_path, heartbeat_hour=7)
    orch._beat_check()
    assert orch.telegram.texts == []