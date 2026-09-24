from __future__ import annotations

import json
import os
import threading
from pathlib import Path

IDLE = "idle"
WAITING_FOR_BUTTON = "waiting_for_button"
CLICKING = "clicking"
IN_QUEUE = "in_queue"
READY = "ready"
LEAVING = "leaving"
PROBLEM = "problem"

STATES = (IDLE, WAITING_FOR_BUTTON, CLICKING, IN_QUEUE, READY, LEAVING, PROBLEM)


class BotState:
    def __init__(self, path: str):
        self._path = Path(path)
        self._lock = threading.RLock()
        self._data: dict = {}
        self.load()

    def load(self) -> None:
        try:
            if self._path.is_file():
                self._data = json.loads(self._path.read_text())
        except (OSError, ValueError):
            self._data = {}
        self._data.setdefault("state", IDLE)
        self._data.setdefault("allowed_user_ids", [])
        self._data.setdefault("last_update_id", 0)
        self._data.setdefault("last_position", None)
        self._data.setdefault("final_notified", False)
        self._data.setdefault("last_heartbeat_date", "")
        self._data.setdefault("chat_id", None)

    @property
    def state(self) -> str:
        with self._lock:
            return self._data.get("state", IDLE)

    @state.setter
    def state(self, value: str) -> None:
        with self._lock:
            self._data["state"] = value
        self.save()

    @property
    def chat_id(self) -> int | None:
        with self._lock:
            return self._data.get("chat_id")

    @chat_id.setter
    def chat_id(self, value: int | None) -> None:
        with self._lock:
            self._data["chat_id"] = value
        self.save()

    @property
    def last_update_id(self) -> int:
        with self._lock:
            return int(self._data.get("last_update_id", 0))

    @last_update_id.setter
    def last_update_id(self, value: int) -> None:
        with self._lock:
            self._data["last_update_id"] = int(value)
        self.save()

    @property
    def last_position(self) -> int | None:
        with self._lock:
            return self._data.get("last_position")

    @last_position.setter
    def last_position(self, value: int | None) -> None:
        with self._lock:
            self._data["last_position"] = value
        self.save()

    @property
    def final_notified(self) -> bool:
        with self._lock:
            return bool(self._data.get("final_notified", False))

    @final_notified.setter
    def final_notified(self, value: bool) -> None:
        with self._lock:
            self._data["final_notified"] = bool(value)
        self.save()

    @property
    def last_heartbeat_date(self) -> str:
        with self._lock:
            return str(self._data.get("last_heartbeat_date", ""))

    @last_heartbeat_date.setter
    def last_heartbeat_date(self, value: str) -> None:
        with self._lock:
            self._data["last_heartbeat_date"] = value
        self.save()

    @property
    def allowed_user_ids(self) -> list[int]:
        with self._lock:
            return list(self._data.get("allowed_user_ids", []))

    def add_allowed_user(self, user_id: int) -> None:
        with self._lock:
            ids = list(self._data.get("allowed_user_ids", []))
            if user_id not in ids:
                ids.append(user_id)
                self._data["allowed_user_ids"] = ids
        self.save()

    def save(self) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=2))
            os.replace(tmp, self._path)