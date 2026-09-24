from __future__ import annotations

import base64
import json
import os
import shutil
import signal
import subprocess
import threading
import time
from typing import Any

import requests
from websocket import create_connection

DEFAULT_PORT = 9222
DEFAULT_EXECUTABLE = "chromium-browser"
DEBUGGER_POLL_INTERVAL = 0.4

JS_READY = "document.readyState === 'complete'"
JS_INNER_TEXT = "document.body ? document.body.innerText : ''"
JS_VIEWPORT = "({w: window.innerWidth, h: window.innerHeight})"

JS_FIND = """
(needle, first) => {
  const norm = s => String(s == null ? '' : s).replace(/\\s+/g, ' ').trim();
  const want = norm(needle).toLowerCase();
  let els = Array.from(document.querySelectorAll(
    'button, [role="button"], a, input[type="submit"], [onclick]'
  ));
  if (first) {
    const el = els.find(e => { const t = norm(e.textContent || e.value).toLowerCase();
      return t === want || t.startsWith(want) && t.length <= want.length + 6; });
    return el ? { found: true } : { found: false };
  }
  els = els.reverse();
  const el = els.find(e => { const t = norm(e.textContent || e.value).toLowerCase();
    return t === want || (t.startsWith(want) && t.length <= want.length + 6); });
  return el ? { found: true } : { found: false };
}
"""

JS_BUTTON_BOX = """
(needle) => {
  const norm = s => String(s == null ? '' : s).replace(/\\s+/g, ' ').trim();
  const want = norm(needle).toLowerCase();
  const els = Array.from(document.querySelectorAll(
    'button, [role="button"], a, input[type="submit"]'
  ));
  const el = els.find(e => { const t = norm(e.textContent || e.value).toLowerCase();
    return t === want || (t.startsWith(want) && t.length <= want.length + 6); })
    || els.reverse().find(e => { const t = norm(e.textContent || e.value).toLowerCase();
      return t === want || (t.startsWith(want) && t.length <= want.length + 6); });
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return { x: r.x, y: r.y, w: r.width, h: r.height };
}
"""


class CdpError(RuntimeError):
    pass


def pick_executable(candidate: str | None, headless: bool = True) -> str:
    candidates = []
    if candidate:
        candidates.append(candidate)
    candidates.extend(
        ["chromium-browser", "chromium", "google-chrome", "google-chrome-stable"]
    )
    if not headless:
        candidates.insert(0, "google-chrome")
    for name in candidates:
        path = shutil.which(name)
        if path:
            return path
    raise RuntimeError(
        "No Chrome/Chromium executable found. Set QUEUE_BOT_BROWSER_EXECUTABLE."
    )


class BrowserProcess:
    def __init__(
        self,
        debug_port: int = DEFAULT_PORT,
        executable: str | None = None,
        user_data_dir: str = "~/.cache/queue-bot-chrome",
        headless: bool = True,
        disable_sandbox: bool = False,
        startup_timeout: float = 60.0,
    ):
        self.debug_port = debug_port
        self.executable = pick_executable(executable, headless)
        self.user_data_dir = user_data_dir
        self.headless = headless
        self.disable_sandbox = disable_sandbox
        self.startup_timeout = startup_timeout
        self.proc: subprocess.Popen | None = None
        self._ws: Any = None
        self._send_lock = threading.Lock()
        self._next_id = 0
        self._pending: dict[int, tuple[threading.Event, dict]] = {}
        self._reader: threading.Thread | None = None
        self._reader_alive = False

    # ---------- lifecycle ----------

    @staticmethod
    def _find_stale(debug_port: int) -> list[str]:
        base = f"--remote-debugging-port={debug_port}"
        try:
            out = subprocess.check_output(
                ["ps", "-axo", "pid=,command="], text=True, timeout=5
            )
        except subprocess.TimeoutExpired:
            return []
        pids = []
        for line in out.splitlines():
            if base in line and "ps -axo" not in line:
                try:
                    pids.append(line.split(None, 1)[0])
                except (ValueError, IndexError):
                    continue
        return pids

    def start(self) -> None:
        for pid in self._find_stale(self.debug_port):
            try:
                os_kill(pid)
            except Exception:
                pass

        args = [
            self.executable,
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-gpu",
            "--mute-audio",
            "--disable-dev-shm-usage",
            f"--window-size=1280,1000",
            f"--remote-debugging-address=127.0.0.1",
            f"--remote-debugging-port={self.debug_port}",
            "--remote-allow-origins=*",
            f"--user-data-dir={self.user_data_dir}",
        ]
        if self.headless:
            args.append("--headless=new")
        if self.disable_sandbox:
            args.append("--no-sandbox")
        args.append("about:blank")

        self.proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        ws_url = self._wait_for_debugger(self.startup_timeout)
        if not ws_url:
            raise CdpError("Chrome started but the debugger endpoint never appeared.")
        self._connect(ws_url)

    def _wait_for_debugger(self, timeout: float) -> str | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                r = requests.get(
                    f"http://127.0.0.1:{self.debug_port}/json", timeout=3
                )
                if r.status_code == 200:
                    for target in r.json():
                        if target.get("type") == "page" and target.get(
                            "webSocketDebuggerUrl"
                        ):
                            return target["webSocketDebuggerUrl"]
            except requests.RequestException:
                pass
            time.sleep(DEBUGGER_POLL_INTERVAL)
        return None

    def _connect(self, ws_url: str) -> None:
        self._ws = create_connection(ws_url, timeout=15, enable_multithread=True)
        self._reader_alive = True
        self._reader = threading.Thread(target=self._read_loop, name="cdp-reader", daemon=True)
        self._reader.start()
        self.call("Page.enable")
        self.call("Runtime.enable")

    def _read_loop(self) -> None:
        while self._reader_alive:
            try:
                raw = self._ws.recv()
            except Exception:
                break
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            msg_id = msg.get("id")
            if msg_id is not None:
                entry = self._pending.get(msg_id)
                if entry:
                    event, holder = entry
                    holder["message"] = msg
                    event.set()

    def call(self, method: str, params: dict | None = None) -> dict:
        with self._send_lock:
            self._next_id += 1
            msg_id = self._next_id
            self._ws.send(
                json.dumps(
                    {"id": msg_id, "method": method, "params": params or {}}
                )
            )
        event = threading.Event()
        holder: dict = {}
        self._pending[msg_id] = (event, holder)
        if not event.wait(timeout=30):
            self._pending.pop(msg_id, None)
            raise CdpError(f"Timed out waiting for CDP {method}")
        message = holder.get("message", {})
        if "error" in message:
            raise CdpError(
                f"CDP error on {method}: {message['error'].get('message', message['error'])}"
            )
        return message.get("result", {})

    def evaluate(self, expression: str, timeout: float = 10.0) -> Any:
        params = {"expression": expression, "returnByValue": True, "awaitPromise": True}
        result = self.call("Runtime.evaluate", params)
        exception = result.get("exceptionDetails")
        if exception:
            raise CdpError(f"Evaluation failed: {exception.get('text', exception)}")
        remote = result.get("result", {})
        if remote.get("type") == "undefined":
            return None
        return remote.get("value")

    def navigate(self, url: str, wait_ready: bool = True) -> None:
        self.call("Page.navigate", {"url": url})
        if wait_ready:
            self.wait_until(lambda: self.evaluate(JS_READY), "page load", timeout=60)

    def wait_until(self, predicate, what: str, timeout: float = 30.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if predicate():
                    return True
            except CdpError:
                pass
            time.sleep(0.5)
        return False

    # ---------- page helpers ----------

    def inner_text(self) -> str:
        value = self.evaluate(JS_INNER_TEXT)
        return str(value or "")

    def viewport(self) -> tuple[int, int]:
        value = self.evaluate(JS_VIEWPORT) or {}
        return int(value.get("w", 1280)), int(value.get("h", 1000))

    def has_button(self, needle: str, first: bool = True) -> bool:
        value = self.evaluate(f"({JS_FIND})({json.dumps(needle)}, {json.dumps(first)})")
        return bool(value and value.get("found"))

    def click_button(self, needle: str, first: bool = True) -> bool:
        value = self.evaluate(f"({JS_CLICK})({json.dumps(needle)}, {json.dumps(first)})")
        return bool(value and value.get("clicked"))

    def button_box(self, needle: str) -> dict | None:
        value = self.evaluate(f"({JS_BUTTON_BOX})({json.dumps(needle)})")
        return value if isinstance(value, dict) else None

    def count_button(self, needle: str) -> int:
        value = self.evaluate(f"({JS_COUNT})({json.dumps(needle)})")
        return int(value or 0)

    def screenshot(self) -> bytes:
        result = self.call(
            "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False}
        )
        data = result.get("data")
        if not data:
            raise CdpError("No screenshot data returned")
        return base64.b64decode(data)

    def close(self) -> None:
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
        self._reader_alive = False
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None


JS_CLICK = """
(needle, first) => {
  const norm = s => String(s == null ? '' : s).replace(/\\s+/g, ' ').trim();
  const want = norm(needle).toLowerCase();
  let els = Array.from(document.querySelectorAll(
    'button, [role="button"], a, input[type="submit"]'
  ));
  if (!first) els = els.reverse();
  const el = els.find(e => { const t = norm(e.textContent || e.value).toLowerCase();
    return t === want || (t.startsWith(want) && t.length <= want.length + 6); });
  if (!el) return { clicked: false };
  el.scrollIntoView({ block: 'center' });
  el.click();
  return { clicked: true };
}
"""

JS_COUNT = """
(needle) => {
  const norm = s => String(s == null ? '' : s).replace(/\\s+/g, ' ').trim();
  const want = norm(needle).toLowerCase();
  return Array.from(document.querySelectorAll(
    'button, [role="button"], a, input[type="submit"]'
  )).filter(e => { const t = norm(e.textContent || e.value).toLowerCase();
    return t === want || (t.startsWith(want) && t.length <= want.length + 6); }).length;
}
"""


def os_kill(pid: str | int) -> None:
    try:
        os.kill(int(pid), signal.SIGKILL)
    except (OSError, ValueError):
        pass