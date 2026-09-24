from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE = """
<!doctype html>
<html>
<head><meta charset="utf-8"><title>Mock queue</title></head>
<body>
  <h1>Task queue</h1>
  <p>You're in line for the next available task. Queue order can vary because
  task eligibility differs by expert. Queue timing can change quickly, so stay nearby.</p>
  <div id="banner"></div>
  <button id="ctl">Join queue</button>
  <div id="modal" style="display:none"><button id="modal-leave">Leave queue</button></div>
<script>
const q = new URLSearchParams(location.search);
const CF = q.has('cf');
const FAST = q.has('fast');
const READY = q.has('ready');
const KICK = q.has('kick');
const JOIN_AFTER = parseInt(q.get('join') || '5', 10);
const START_POS = parseInt(q.get('pos') || '600', 10);
const banner = document.getElementById('banner');
const ctl = document.getElementById('ctl');
const modal = document.getElementById('modal');
let mode = 'idle';
let clicks = 0;
let pos = START_POS;
let kicked = false;
function setCtl(t) { ctl.textContent = t; }
function bannerText() {
  return 'Position ' + pos + ' of 700 \u00b7 Estimated 45\u201375 min to front';
}
function readyText() { return 'Your task is ready'; }
function update() {
  if (mode === 'queue') {
    if (kicked) { banner.textContent = ''; setCtl('Join queue'); }
    else if (READY && pos === 0) { banner.textContent = readyText(); }
    else { banner.textContent = bannerText(); }
  } else {
    banner.textContent = '';
  }
}
function tick() {
  if (mode !== 'queue' || kicked) return;
  pos = Math.max(0, pos - (FAST ? 30 : 3));
  update();
}
if (CF) {
  document.body.innerHTML =
    '<h1>Verifying you are human</h1><p>Just a moment, checking your browser\u2026</p>';
} else {
  ctl.onclick = function () {
    if (mode === 'idle') {
      clicks += 1;
      if (clicks >= JOIN_AFTER) {
        mode = 'queue';
        setCtl('Leave queue');
        modal.style.display = 'none';
        update();
        if (KICK) setTimeout(function () {
          kicked = true; mode = 'idle';
          update();
        }, 12000);
      }
    } else if (mode === 'queue') {
      modal.style.display = 'block';
    }
  };
  document.getElementById('modal-leave').onclick = function () {
    mode = 'idle';
    clicks = 0;
    pos = START_POS;
    modal.style.display = 'none';
    setCtl('Join queue');
    update();
  };
  setInterval(tick, 1000);
}
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # noqa: ARG002
        pass


class MockServer:
    def __init__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(
            target=self.server.serve_forever, name="mock-http", daemon=True
        )

    def start(self) -> str:
        self.thread.start()
        port = self.server.server_address[1]
        return f"http://127.0.0.1:{port}/"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()