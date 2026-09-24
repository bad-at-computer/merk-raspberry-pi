from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_QUEUE_URL = (
    "https://feather.openai.com/campaigns/e27f8b7b-7b84-408b-801d"
    "-7437f012e1c1?tab=tasks&task-batch=25b45d5f-afe3-4655-af5b"
    "-e74cc5804613&is_admin_view=false"
)

ENV_PREFIX = "QUEUE_BOT_"


@dataclass
class Config:
    telegram_token: str = ""
    allowed_user_ids: list[int] = field(default_factory=list)
    poll_timeout: int = 25

    queue_url: str = DEFAULT_QUEUE_URL
    browser_executable: str = "chromium-browser"
    user_data_dir: str = "~/.cache/queue-bot-chrome"
    debug_port: int = 9222
    headless: bool = True
    disable_sandbox: bool = False
    browser_startup_timeout_s: float = 60.0

    click_min: float = 1.0
    click_max: float = 3.0

    join_button_wait_min: int = 10
    cf_alert_after_s: int = 120
    click_cap_min: int = 60

    monitor_interval_s: float = 5.0
    drop_large: int = 50
    drop_small: int = 10
    final_position: int = 10

    in_queue_notify_once: bool = True
    parse_fallback_raw: bool = True

    leave_modal_timeout_s: int = 15
    leave_confirm_wait_s: int = 10
    leave_max_retries: int = 3

    heartbeat_enabled: bool = True
    heartbeat_hour: int = 9

    state_file: str = "~/.local/state/queue-bot/state.json"
    log_file: str = "~/.local/state/queue-bot/queue-bot.log"

    reveal_button_region: bool = True
    mosaic_width: int = 160


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _assign(cfg: Config, key: str, raw: str) -> None:
    if not hasattr(cfg, key):
        return
    current = getattr(cfg, key)
    if isinstance(current, bool):
        setattr(cfg, key, _truthy(raw))
    elif isinstance(current, int):
        setattr(cfg, key, int(raw))
    elif isinstance(current, float):
        setattr(cfg, key, float(raw))
    elif isinstance(current, list):
        setattr(cfg, key, [int(x) for x in raw.split(",") if x.strip()])
    else:
        setattr(cfg, key, raw)


def load_config(path: str | None = None) -> Config:
    cfg = Config()

    file_path = path or os.environ.get(f"{ENV_PREFIX}CONFIG")
    if file_path and Path(file_path).is_file():
        data = json.loads(Path(file_path).read_text())
        for key, value in data.items():
            if key == "allowed_user_ids":
                cfg.allowed_user_ids = [int(x) for x in value]
                continue
            if hasattr(cfg, key):
                setattr(cfg, key, value)

    for key in cfg.__dataclass_fields__:
        env_name = ENV_PREFIX + key.upper()
        if env_name in os.environ:
            _assign(cfg, key, os.environ[env_name])

    cfg.user_data_dir = str(Path(cfg.user_data_dir).expanduser())
    cfg.state_file = str(Path(cfg.state_file).expanduser())
    cfg.log_file = str(Path(cfg.log_file).expanduser())
    return cfg