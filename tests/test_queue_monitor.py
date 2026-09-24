from queue_bot.queue_monitor import (
    has_join_button,
    has_leave_button,
    has_task_ready,
    is_cloudflare_challenge,
    monitor_position,
    parse_queue_state,
)

SAMPLE = (
    "Task queue You're in line for the next available task. Queue order can vary "
    "because task eligibility differs by expert. Queue timing can change quickly, "
    "so stay nearby. Position 510 of 654 \u00b7 Estimated 45\u201375 min to front"
)


def test_parse_sample():
    qs = parse_queue_state(SAMPLE)
    assert qs is not None
    assert (qs.position, qs.total, qs.est_low, qs.est_high) == (510, 654, 45, 75)


def test_parse_em_dash_and_to():
    text = "Position 12 of 500 \u00b7 Estimated 5 to 15 min to front"
    qs = parse_queue_state(text)
    assert (qs.position, qs.total, qs.est_low, qs.est_high) == (12, 500, 5, 15)


def test_parse_no_estimate():
    qs = parse_queue_state("Position 3 of 90")
    assert (qs.position, qs.total) == (3, 90)
    assert qs.est_low is None


def test_parse_garbage():
    assert parse_queue_state("Welcome back") is None
    assert parse_queue_state("") is None
    assert parse_queue_state(None) is None


def test_button_text_detection():
    assert has_join_button("please click Join queue now")
    assert not has_join_button("Click Leave queue")
    assert has_leave_button("You are queued. Leave queue")
    assert not has_join_button("Join queue and Leave queue both")
    assert not has_leave_button("Join queue")


def test_task_ready():
    assert has_task_ready("Congratulations, Your task is ready.")
    assert not has_task_ready("Your position is ready")


def test_cloudflare_markers():
    assert is_cloudflare_challenge("Verifying you are human")
    assert is_cloudflare_challenge("Just a moment...")
    assert is_cloudflare_challenge("Attention Required")
    assert not is_cloudflare_challenge("Position 5 of 100")


def test_monitor_large_band():
    assert monitor_position(600, 549) == (True, False)
    assert monitor_position(600, 560) == (False, False)
    assert monitor_position(100, 97) == (False, False)
    assert monitor_position(151, 100) == (True, False)


def test_monitor_small_band():
    assert monitor_position(110, 99) == (True, False)
    assert monitor_position(60, 49) == (True, False)
    assert monitor_position(25, 14) == (True, False)
    assert monitor_position(25, 15) == (False, False)
    assert monitor_position(50, 45) == (False, False)


def test_monitor_final_band():
    assert monitor_position(15, 9) == (True, True)
    assert monitor_position(9, 5) == (True, True)


def test_monitor_never_fires_on_rise():
    assert monitor_position(100, 150) == (False, False)
    assert monitor_position(10, 11) == (False, False)


def test_monitor_missing_values():
    assert monitor_position(None, 100) == (False, False)
    assert monitor_position(100, None) == (False, False)
    assert monitor_position(None, None) == (False, False)