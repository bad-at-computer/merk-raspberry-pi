from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import requests

API_BASE = "https://api.telegram.org"

START = "start"
LEAVE = "leave"
STATUS = "status"
STOP = "stop"
SCREENSHOT = "screenshot"
HELP = "help"
UNKNOWN = "unknown"

START_WORDS = {"start", "go", "begin", "join", "start now", "lets go", "let's go"}
LEAVE_WORDS = {"leave", "exit", "quit", "get out"}
STATUS_WORDS = {"status", "report", "position", "where", "where am i", "state"}
STOP_WORDS = {"stop", "abort", "halt", "cancel", "kill"}
SCREENSHOT_WORDS = {"screenshot", "pic", "photo", "print"}
HELP_WORDS = {"help", "?"}


@dataclass(frozen=True)
class Message:
    update_id: int
    chat_id: int
    user_id: int
    text: str
    date: int


def parse_command(text: str) -> str:
    cleaned = " ".join((text or "").strip().lower().split())
    for punct in ("!", "?", "."):
        cleaned = cleaned.rstrip(punct)
    cleaned = cleaned.strip()
    if cleaned in START_WORDS:
        return START
    if cleaned in LEAVE_WORDS:
        return LEAVE
    if cleaned in STATUS_WORDS:
        return STATUS
    if cleaned in STOP_WORDS:
        return STOP
    if cleaned in SCREENSHOT_WORDS:
        return SCREENSHOT
    if cleaned in HELP_WORDS:
        return HELP
    return UNKNOWN


class TelegramClient:
    def __init__(
        self,
        token: str,
        poll_timeout: int = 25,
        allowed_user_ids: list[int] | None = None,
    ):
        if not token:
            raise ValueError("Telegram bot token is required")
        self.token = token
        self.poll_timeout = max(0, min(poll_timeout, 50))
        self.allowed_user_ids = set(allowed_user_ids or [])
        self.session = requests.Session()
        self.api = f"{API_BASE}/bot{token}"
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_update_id: int = 0
        self.on_message: callable | None = None
        self.on_unknown_sender: callable | None = None

    # ---------- senders ----------

    def send_text(self, chat_id: int, text: str) -> bool:
        try:
            r = self.session.post(
                f"{self.api}/sendMessage",
                json={"chat_id": chat_id, "text": text},
                timeout=20,
            )
            return r.ok
        except requests.RequestException:
            return False

    def send_photo(
        self, chat_id: int, photo: bytes, caption: str = "", filename: str = "screen.jpg"
    ) -> bool:
        try:
            r = self.session.post(
                f"{self.api}/sendPhoto",
                data={"chat_id": chat_id, "caption": caption},
                files={"photo": (filename, photo, "image/jpeg")},
                timeout=30,
            )
            return r.ok
        except requests.RequestException:
            return False

    # ---------- message pump ----------

    def start(self, on_message, on_unknown_sender=None, last_update_id: int = 0) -> None:
        self._last_update_id = int(last_update_id)
        self.on_message = on_message
        self.on_unknown_sender = on_unknown_sender
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._pump, name="telegram-poll", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def _pump(self) -> None:
        while not self._stop_event.is_set():
            try:
                updates = self._fetch()
            except requests.RequestException:
                time.sleep(5)
                continue
            except Exception:
                time.sleep(5)
                continue
            if not updates:
                continue
            for update in updates:
                self._handle(update)

    def _offset(self) -> int:
        return self._last_update_id

    def _fetch(self) -> list[dict]:
        params = {
            "offset": self._offset() + 1,
            "timeout": self.poll_timeout,
            "allowed_updates": '["message"]',
        }
        r = self.session.get(
            f"{self.api}/getUpdates", params=params, timeout=self.poll_timeout + 15
        )
        r.raise_for_status()
        return r.json().get("result", [])

    def _handle(self, update: dict) -> None:
        update_id = int(update.get("update_id", 0))
        self._last_update_id = max(self._last_update_id, update_id)
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        if chat.get("type") not in {None, "private", "group", "supergroup"}:
            return
        from_user = message.get("from") or {}
        msg = Message(
            update_id=update_id,
            chat_id=int(chat.get("id", 0)),
            user_id=int(from_user.get("id", 0)),
            text=message.get("text", ""),
            date=int(message.get("date", 0)),
        )
        if not msg.text:
            return
        if msg.user_id in self.allowed_user_ids:
            if self.on_message:
                self.on_message(msg)
            return
        if self.on_unknown_sender:
            self.on_unknown_sender(msg)