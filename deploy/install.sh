#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="${SUDO_USER:-$(whoami)}"
SERVICE_NAME="queue-bot"
CONFIG_PATH="$PROJECT_DIR/config.json"

echo "==> Installing system packages (chromium, python venv support)"
if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y chromium python3-venv python3-pip curl
fi

echo "==> Creating venv"
python3 -m venv "$PROJECT_DIR/.venv"
"$PROJECT_DIR/.venv/bin/pip" install --upgrade pip
"$PROJECT_DIR/.venv/bin/pip" install -r "$PROJECT_DIR/requirements.txt"

if [[ ! -f "$CONFIG_PATH" ]]; then
    cp "$PROJECT_DIR/config.example.json" "$CONFIG_PATH"
    echo "==> Created $CONFIG_PATH — EDIT IT: set telegram_token and paths, then restart the service."
    echo "    Secret token is required: create a bot with @BotFather and paste the token."
fi

chown -R "$RUN_USER" "$PROJECT_DIR"
chmod 600 "$CONFIG_PATH" 2>/dev/null || true

echo "==> Installing systemd unit"
if command -v systemctl >/dev/null 2>&1; then
    sudo cp "$PROJECT_DIR/deploy/queue-bot.service" "/etc/systemd/system/$SERVICE_NAME.service"
    sudo systemctl daemon-reload
    sudo systemctl enable "$SERVICE_NAME"
    sudo systemctl restart "$SERVICE_NAME" || true
    echo "==> Service started. Check: journalctl -u $SERVICE_NAME -f"
else
    echo "==> systemd not found; run manually: .venv/bin/python -m queue_bot"
fi

echo "==> Done."
echo "    First run: text the bot; the first sender is auto-registered as controller."
echo "    Then: start / status / leave / stop / screenshot"