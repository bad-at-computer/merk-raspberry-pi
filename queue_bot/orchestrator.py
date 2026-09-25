from __future__ import annotations

import logging
import queue
import random
import threading
import time
from datetime import date, datetime
from typing import Callable

from .cdp_browser import BrowserProcess, CdpError
from .config import Config
from .queue_monitor import (
    FINAL_MESSAGE,
    has_join_button,
    has_leave_button,
    has_task_ready,
    is_cloudflare_challenge,
    monitor_position,
    parse_queue_state,
)
from .redaction import redact_screenshot
from .state import (
    CLICKING,
    IDLE,
    IN_QUEUE,
    LEAVING,
    PROBLEM,
    READY,
    WAITING_FOR_BUTTON,
    BotState,
)
from .telegram_client import (
    HELP,
    LEAVE,
    SCREENSHOT,
    START,
    STATUS,
    STOP,
    Message,
    TelegramClient,
    parse_command,
)

logger = logging.getLogger("queue_bot")


class SessionControl:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.stop_event = threading.Event()
        self.leave_event = threading.Event()
        self.browser: BrowserProcess | None = None
        self.active = False
        self.snapshot: dict = {}

    def request_stop(self) -> None:
        self.stop_event.set()

    def request_leave(self) -> None:
        self.leave_event.set()

    def clear(self) -> None:
        self.stop_event.clear()
        self.leave_event.clear()
        self.browser = None
        self.snapshot = {}

    def update(self, **fields) -> None:
        with self._lock:
            self.snapshot.update(fields)


class Orchestrator:
    def __init__(self, config: Config, state: BotState):
        self.config = config
        self.state = state
        self.telegram = TelegramClient(
            token=config.telegram_token,
            poll_timeout=config.poll_timeout,
            allowed_user_ids=config.allowed_user_ids or state.allowed_user_ids,
        )
        self.control = SessionControl()
        self.received: queue.Queue[Message] = queue.Queue()
        self._session_thread: threading.Thread | None = None
        self._last_beat_check = 0.0
        self._session_started_at: float | None = None

    # ---------- public entry ----------

    def run(self) -> None:
        known_ids = self.state.allowed_user_ids
        if known_ids:
            self.telegram.allowed_user_ids.update(known_ids)

        self.telegram.start(
            on_message=self._on_message,
            on_unknown_sender=self._on_unknown_sender,
            last_update_id=self.state.last_update_id,
        )

        if self.state.chat_id:
            self.telegram.send_text(
                self.state.chat_id,
                f"Queue bot restarted. State: {self.state.state}.",
            )
            if self.state.state in (IN_QUEUE, READY):
                self._start_session(self.state.chat_id, restore=True)

        while True:
            self._beat_check()
            try:
                msg = self.received.get(timeout=30)
            except queue.Empty:
                continue
            self._dispatch(msg)

    def shutdown(self) -> None:
        self.control.request_stop()
        self.telegram.stop()
        if self.control.browser:
            try:
                self.control.browser.close()
            except Exception:
                pass

    # ---------- telegram callbacks ----------

    def _on_message(self, msg: Message) -> None:
        self.received.put(msg)

    def _on_unknown_sender(self, msg: Message) -> None:
        if self.state.allowed_user_ids:
            return
        self.state.add_allowed_user(msg.user_id)
        self.telegram.allowed_user_ids.add(msg.user_id)
        self.state.chat_id = msg.chat_id
        self.telegram.send_text(
            msg.chat_id, "You are now registered as the controller of this bot."
        )

    # ---------- command dispatch ----------

    def _dispatch(self, msg: Message) -> None:
        self.state.chat_id = msg.chat_id
        command = parse_command(msg.text)
        handler: dict[str, Callable[[Message], None]] = {
            START: self._cmd_start,
            LEAVE: self._cmd_leave,
            STATUS: self._cmd_status,
            STOP: self._cmd_stop,
            SCREENSHOT: self._cmd_screenshot,
            HELP: self._cmd_help,
        }
        logger.info("command=%s user=%s", command, msg.user_id)
        handler.get(command, self._cmd_unknown)(msg)

    def _cmd_start(self, msg: Message) -> None:
        if self.control.active:
            self.telegram.send_text(msg.chat_id, "Session already active.")
            return
        self._start_session(msg.chat_id, restore=False)

    def _start_session(self, chat_id: int, restore: bool) -> None:
        self.control.clear()
        self.control.active = True
        self._session_started_at = time.time()
        self._session_thread = threading.Thread(
            target=self._session_worker,
            args=(chat_id, restore),
            name="session",
            daemon=True,
        )
        self._session_thread.start()

    def _cmd_leave(self, msg: Message) -> None:
        if not self.control.active:
            self.telegram.send_text(msg.chat_id, "No active session to leave.")
            return
        self.control.request_leave()
        self.telegram.send_text(msg.chat_id, "Leaving queue…")

    def _cmd_stop(self, msg: Message) -> None:
        if not self.control.active:
            self.telegram.send_text(msg.chat_id, "Nothing to stop.")
            return
        self.control.request_stop()

    def _cmd_status(self, msg: Message) -> None:
        self.telegram.send_text(msg.chat_id, self._status_text())

    def _status_text(self) -> str:
        snap = self.control.snapshot
        lines = [
            f"State: {self.state.state}",
            f"Position: {snap.get('position', 'n/a')}",
            f"Total: {snap.get('total', 'n/a')}",
        ]
        if snap.get("estimate"):
            lines.append(f"Estimate: {snap['estimate']}")
        if snap.get("note"):
            lines.append(snap["note"])
        return "\n".join(lines)

    def _cmd_screenshot(self, msg: Message) -> None:
        browser = self.control.browser
        if browser is None:
            self.telegram.send_text(msg.chat_id, "No active browser to capture.")
            return
        self._send_redacted_screenshot(msg.chat_id, "Screenshot (redacted)")

    def _cmd_help(self, msg: Message) -> None:
        self.telegram.send_text(
            msg.chat_id,
            "Commands: start, leave, status, stop, screenshot, help",
        )

    def _cmd_unknown(self, msg: Message) -> None:
        self.telegram.send_text(
            msg.chat_id, "Unknown command. Try: start, status, leave"
        )

    # ---------- heartbeat ----------

    def _beat_check(self) -> None:
        if not self.config.heartbeat_enabled:
            return
        now = time.monotonic()
        if now - self._last_beat_check < 30:
            return
        self._last_beat_check = now
        today = date.today().isoformat()
        if self.state.last_heartbeat_date == today:
            return
        if not self.state.chat_id:
            return
        hb_hour = self.config.heartbeat_hour
        if datetime.now().hour != hb_hour:
            return
        self.state.last_heartbeat_date = today
        position = self.state.last_position
        self.telegram.send_text(
            self.state.chat_id,
            f"Pi alive. State: {self.state.state}. Position: {position if position is not None else 'n/a'}",
        )

    # ---------- session worker ----------

    def _session_worker(self, chat_id: int, restore: bool) -> None:
        browser: BrowserProcess | None = None
        try:
            browser = self._launch_browser()
            self.control.browser = browser
            browser.start()
            browser.navigate(self.config.queue_url)

            text = self._page_text(browser)
            if has_leave_button(text) and not has_join_button(text):
                self.state.state = IN_QUEUE
                self.telegram.send_text(chat_id, "Already in queue. Resuming monitoring.")
                self._monitor_and_wait(browser, chat_id)
                return

            if restore:
                self.state.state = IDLE
                self._safe_send(
                    chat_id,
                    "Restored, but join button is present — not currently in queue.",
                )
                return

            if not self._wait_for_join(browser, chat_id):
                return

            if not self._click_until_joined(browser, chat_id):
                return

            text = self._page_text(browser)
            if not (has_leave_button(text) and not has_join_button(text)):
                self._safe_send(chat_id, "Did not confirm joining the queue.")
                self._send_redacted_screenshot(chat_id, "Page after clicking")
                return

            self.state.state = IN_QUEUE
            self._send_first_queue_notification(chat_id, text)
            self._monitor_and_wait(browser, chat_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("session worker crashed")
            self.state.state = PROBLEM
            self._safe_send(chat_id, f"Session problem: {exc}")
            self._send_redacted_screenshot(chat_id, "Error screenshot")
        finally:
            if browser:
                browser.close()
            self.control.browser = None
            self.control.active = False
            if self.state.state not in (IDLE, IN_QUEUE, READY):
                self.state.state = IDLE

    def _launch_browser(self) -> BrowserProcess:
        return BrowserProcess(
            debug_port=self.config.debug_port,
            executable=self.config.browser_executable,
            user_data_dir=self.config.user_data_dir,
            headless=self.config.headless,
            disable_sandbox=self.config.disable_sandbox,
            startup_timeout=self.config.browser_startup_timeout_s,
        )

    def _page_text(self, browser: BrowserProcess) -> str:
        try:
            return browser.inner_text()
        except CdpError:
            return ""

    def _wait_for_join(self, browser: BrowserProcess, chat_id: int) -> bool:
        self.state.state = WAITING_FOR_BUTTON
        deadline = time.monotonic() + self.config.join_button_wait_min * 60
        started = time.monotonic()
        alerted_problem = False
        while time.monotonic() < deadline:
            if self._aborted():
                return False
            text = self._page_text(browser)
            if has_join_button(text) and not has_leave_button(text):
                return True
            if has_leave_button(text) and not has_join_button(text):
                return True
            challenge = is_cloudflare_challenge(text) or not text.strip()
            appeared = time.monotonic() - started
            if challenge and not alerted_problem and appeared > self.config.cf_alert_after_s:
                alerted_problem = True
                self.state.state = PROBLEM
                self._safe_send(
                    chat_id,
                    "Join button not found — page looks like a challenge or needs login. "
                    "Open Pi Connect and pass the captcha / log in.",
                )
                self._send_redacted_screenshot(chat_id, "Wait screen")
            time.sleep(2)
        self.state.state = PROBLEM
        self._safe_send(
            chat_id,
            "Could not find the “Join queue” button within "
            f"{self.config.join_button_wait_min} min. Check the browser via Pi Connect.",
        )
        self._send_redacted_screenshot(chat_id, "After waiting for Join queue")
        return False

    def _click_until_joined(self, browser: BrowserProcess, chat_id: int) -> bool:
        self.state.state = CLICKING
        cap = self.config.click_cap_min * 60
        started = time.monotonic()
        clicks = 0
        alerted_problem = False
        while time.monotonic() - started < cap:
            if self._aborted():
                return False
            text = self._page_text(browser)
            if has_leave_button(text) and not has_join_button(text):
                if self._settle(browser):
                    return True
                continue
            if not has_join_button(text) and not has_leave_button(text):
                if not alerted_problem:
                    alerted_problem = True
                    challenge = is_cloudflare_challenge(text) or not text.strip()
                    self._safe_send(
                        chat_id,
                        "Join button disappeared mid-clicking "
                        + ("(challenge/login appeared)." if challenge else "(page changed)."),
                    )
                    self._send_redacted_screenshot(chat_id, "Join button lost")
                if not text.strip():
                    time.sleep(5)
                    continue
            joined = False
            try:
                joined = browser.click_button("Join queue", first=True)
            except CdpError:
                pass
            if joined:
                clicks += 1
            time.sleep(random.uniform(self.config.click_min, self.config.click_max))
        self.state.state = PROBLEM
        self._safe_send(
            chat_id,
            f"Tried to join for {self.config.click_cap_min} min without success. Stopping.",
        )
        self._send_redacted_screenshot(chat_id, "After clicking cap")
        return False

    def _settle(self, browser: BrowserProcess) -> bool:
        time.sleep(2)
        text = self._page_text(browser)
        return has_leave_button(text) and not has_join_button(text)

    def _send_first_queue_notification(self, chat_id: int, text: str) -> None:
        qs = parse_queue_state(text)
        if qs:
            self.telegram.send_text(
                chat_id,
                f"Queue position {qs.position} of {qs.total}"
                + self._estimate_text(qs.est_low, qs.est_high)
                + " — success, you are in the queue.",
            )
        else:
            self.telegram.send_text(chat_id, "In the queue. (Could not parse position.)")

    def _estimate_text(self, low: int | None, high: int | None) -> str:
        if low is None or high is None:
            return ""
        return f", estimated {low}-{high} min to front"

    def _monitor_and_wait(self, browser: BrowserProcess, chat_id: int) -> None:
        prev_position: int | None = None
        final_sent = False
        self.control.update(
            state="in_queue", position=prev_position, total=None, estimate=""
        )

        while not self.control.stop_event.is_set():
            if self.control.leave_event.is_set():
                self._leave_flow(browser, chat_id)
                return
            time.sleep(self.config.monitor_interval_s)
            text = self._page_text(browser)
            if has_task_ready(text):
                self.telegram.send_text(chat_id, "Ready")
                self.state.state = READY
                self.control.update(state="ready", note="task ready, waiting")
                self._ready_loop(browser, chat_id)
                return
            if has_join_button(text) and not has_leave_button(text):
                self._safe_send(chat_id, "You are no longer in the queue.")
                self._send_redacted_screenshot(chat_id, "Kicked / left queue")
                return
            qs = parse_queue_state(text)
            if qs is None:
                self.control.update(
                    position=self.state.last_position or prev_position or None
                )
                continue
            current = qs.position
            notify, final = monitor_position(
                prev_position,
                current,
                large_drop=self.config.drop_large,
                small_drop=self.config.drop_small,
                final_position=self.config.final_position,
            )
            if current >= self.config.final_position and final_sent:
                final_sent = False
                self.state.final_notified = False
            if final and not final_sent:
                final_sent = True
                self.state.final_notified = True
                self.telegram.send_text(
                    chat_id,
                    f"Position {current} of {qs.total}"
                    + self._estimate_text(qs.est_low, qs.est_high)
                    + f". {FINAL_MESSAGE}",
                )
            elif notify and not final_sent:
                self.telegram.send_text(
                    chat_id,
                    f"Position {current} of {qs.total}"
                    + self._estimate_text(qs.est_low, qs.est_high),
                )
            prev_position = current
            self.state.last_position = current
            self.control.update(
                position=current,
                total=qs.total,
                estimate=self._estimate_text(qs.est_low, qs.est_high).strip(", "),
            )

    def _ready_loop(self, browser: BrowserProcess, chat_id: int) -> None:
        while not self.control.stop_event.is_set():
            if self.control.leave_event.is_set():
                self._leave_flow(browser, chat_id)
                return
            time.sleep(5)

    # ---------- leave flow ----------

    def _leave_flow(self, browser: BrowserProcess, chat_id: int) -> None:
        self.state.state = LEAVING
        for attempt in range(self.config.leave_max_retries):
            if self.control.stop_event.is_set():
                return
            text = self._page_text(browser)
            if has_join_button(text) and not has_leave_button(text):
                self.telegram.send_text(chat_id, "Left the queue successfully")
                return
            if not has_leave_button(text):
                self._safe_send(chat_id, "Leave button not found on the page.")
                return
            try:
                browser.click_button("Leave queue", first=True)
            except CdpError as exc:
                self._safe_send(chat_id, f"Could not click Leave queue: {exc}")
                return
            modal_clicked = self._click_modal_leave(browser)
            if not modal_clicked:
                self._safe_send(
                    chat_id,
                    "Leave modal did not appear; retrying.",
                )
                continue
            time.sleep(self.config.leave_confirm_wait_s)
        self.state.state = PROBLEM
        self._safe_send(
            chat_id,
            "Could not confirm leaving the queue. Please check manually.",
        )
        self._send_redacted_screenshot(chat_id, "Leave retries exhausted")

    def _click_modal_leave(self, browser: BrowserProcess) -> bool:
        deadline = time.monotonic() + self.config.leave_modal_timeout_s
        while time.monotonic() < deadline:
            try:
                count = browser.count_button("Leave queue")
            except CdpError:
                count = 0
            if count >= 2:
                return browser.click_button("Leave queue", first=False)
            time.sleep(1)
        return False

    # ---------- helpers ----------

    def _aborted(self) -> bool:
        return self.control.stop_event.is_set() or self.control.leave_event.is_set()

    def _safe_send(self, chat_id: int, text: str) -> bool:
        try:
            return self.telegram.send_text(chat_id, text)
        except Exception:  # noqa: BLE001
            return False

    def _send_redacted_screenshot(self, chat_id: int, caption: str) -> None:
        browser = self.control.browser
        if browser is None:
            return
        try:
            box = None
            if self.config.reveal_button_region:
                box = browser.button_box("Join queue") or browser.button_box("Leave queue")
            page_w, page_h = browser.viewport()
            png = browser.screenshot()
            rects = None
            if box:
                rects = [(box["x"], box["y"], box["w"], box["h"])]
            redacted = redact_screenshot(
                png,
                reveal_rects=rects,
                page_size=(page_w, page_h),
                mosaic_width=self.config.mosaic_width,
            )
            self.telegram.send_photo(chat_id, redacted, caption=caption)
        except Exception as exc:  # noqa: BLE001
            logger.warning("screenshot failed: %s", exc)
            self._safe_send(chat_id, caption + " — (screenshot unavailable)")