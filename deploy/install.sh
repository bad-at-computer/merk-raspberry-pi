#!/usr/bin/env bash
set -euo pipefail

SCRIPT_SOURCE="$(readlink -f "${BASH_SOURCE[0]}")"
PROJECT_DIR="$(dirname "$(dirname "$SCRIPT_SOURCE")")"
RUN_USER="${SUDO_USER:-$(id -un)}"
SERVICE_NAME="queue-bot"
TEMPLATE="$PROJECT_DIR/deploy/queue-bot.service.template"
UNIT_PATH="/etc/systemd/system/$SERVICE_NAME.service"
CONFIG_PATH="$PROJECT_DIR/config.json"
VENV_PY="$PROJECT_DIR/.venv/bin/python"

# Run a command with sudo only when we are not already root.
maybe_sudo() {
    if [[ $EUID -eq 0 ]]; then
        "$@"
    else
        sudo "$@"
    fi
}

# Run a command as the target user (when root); otherwise as ourselves.
as_user() {
    if [[ $EUID -eq 0 ]]; then
        runuser -u "$RUN_USER" -- "$@"
    else
        "$@"
    fi
}

USER_HOME="$(getent passwd "$RUN_USER" | cut -d: -f6)"
if [[ -z "$USER_HOME" ]]; then
    USER_HOME="$HOME"
fi

if [[ $EUID -ne 0 && "$RUN_USER" == "$(id -un)" ]]; then
    echo "==> Note: not running as root; sudo will be used for system steps."
    echo "    (Alternatively: sudo ./deploy/install.sh)"
fi

echo "==> Project dir:   $PROJECT_DIR"
echo "==> Service user:  $RUN_USER (home: $USER_HOME)"

echo "==> Installing system packages"
if command -v apt-get >/dev/null 2>&1; then
    maybe_sudo apt-get update
    candidate=""
    for pkg in chromium-browser chromium; do
        if [[ "$(apt-cache policy "$pkg" 2>/dev/null | awk '/Candidate:/{print $2}')" != "(none)" ]] \
           && [[ -n "$(apt-cache policy "$pkg" 2>/dev/null | awk '/Candidate:/{print $2}')" ]]; then
            candidate="$pkg"
            break
        fi
    done
    if [[ -z "$candidate" ]]; then
        echo "==> ERROR: could not find a 'chromium' or 'chromium-browser' apt candidate."
        echo "    Install Chromium manually, then adjust browser_executable in config.json."
        exit 1
    fi
    echo "==> Using package: $candidate"
    maybe_sudo apt-get install -y "$candidate" python3-venv python3-pip curl
else
    echo "==> apt-get not found; assuming Chromium is already installed."
fi

echo "==> Creating cache/state directories for $RUN_USER"
as_user mkdir -p "$USER_HOME/.cache" "$USER_HOME/.local/state/queue-bot"

echo "==> Creating venv"
as_user python3 -m venv "$PROJECT_DIR/.venv"
as_user "$VENV_PY" -m pip install --upgrade pip
as_user "$VENV_PY" -m pip install -r "$PROJECT_DIR/requirements.txt"

if [[ ! -f "$CONFIG_PATH" ]]; then
    cp "$PROJECT_DIR/config.example.json" "$CONFIG_PATH"
    echo "==> Created $CONFIG_PATH — EDIT IT: set telegram_token (create a bot with @BotFather)."
    echo "    Paths use '~' and resolve to $USER_HOME automatically."
else
    python3 - "$CONFIG_PATH" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text())
stale_keys = ("user_data_dir", "state_file", "log_file")
changed = []
for key in stale_keys:
    value = data.get(key)
    if isinstance(value, str) and value.startswith("/home/"):
        data[key] = "~/" + value.split("/", 3)[3]
        changed.append(key)
if changed:
    path.write_text(json.dumps(data, indent=2))
    print(f"==> Rewrote stale /home/... paths in {path.name}: {', '.join(changed)}")
PY
    echo "==> Using existing $CONFIG_PATH (telegram_token must be set)."
fi

if [[ $EUID -eq 0 ]]; then
    chown -R "$RUN_USER":"$(id -gn "$RUN_USER")" "$PROJECT_DIR"
fi
chmod 600 "$CONFIG_PATH" 2>/dev/null || true

echo "==> Rendering systemd unit"
if [[ ! -f "$TEMPLATE" ]]; then
    echo "==> ERROR: template not found: $TEMPLATE"
    exit 1
fi
sed -e "s|__USER__|${RUN_USER}|g" \
    -e "s|__PROJECT_DIR__|${PROJECT_DIR}|g" \
    -e "s|__VENV_PY__|${VENV_PY}|g" \
    -e "s|__CONFIG__|${CONFIG_PATH}|g" \
    "$TEMPLATE" | tee /tmp/queue-bot.service.rendered >/dev/null

if command -v systemctl >/dev/null 2>&1; then
    maybe_sudo cp /tmp/queue-bot.service.rendered "$UNIT_PATH"
    maybe_sudo systemctl daemon-reload
    maybe_sudo systemctl enable "$SERVICE_NAME"
    maybe_sudo systemctl restart "$SERVICE_NAME" || true
    echo "==> Service installed:"
    grep -E '^(User|WorkingDirectory|ExecStart)=' "$UNIT_PATH"
    echo "==> Service started. Check: journalctl -u $SERVICE_NAME -f"
else
    echo "==> systemd not found; run manually: $VENV_PY -m queue_bot"
fi

echo "==> Done."
echo "    First run: text the bot; the first sender is auto-registered as controller."
echo "    Commands: start / status / leave / stop / screenshot / help"