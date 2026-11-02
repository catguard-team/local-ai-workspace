"""Validated, local-only response limits loaded when the backend starts."""
import json
from pathlib import Path

DEFAULTS = {"max_response_minutes": 60, "max_queue_wait_minutes": 120}
SETTINGS_PATH = Path(__file__).resolve().parent / ".runtime" / "inference-settings.json"


def load_limits(path=SETTINGS_PATH):
    values = dict(DEFAULTS)
    if path.exists():
        try:
            supplied = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise ValueError("Cannot read .runtime/inference-settings.json as JSON") from exc
        if not isinstance(supplied, dict) or set(supplied) - set(DEFAULTS):
            raise ValueError("Unknown inference settings; use max_response_minutes and max_queue_wait_minutes")
        values.update(supplied)
    for name, value in values.items():
        if type(value) is not int or not 1 <= value <= 1440:
            raise ValueError(f"{name} must be an integer from 1 to 1440 minutes")
    if values["max_queue_wait_minutes"] < values["max_response_minutes"]:
        raise ValueError("max_queue_wait_minutes must be at least max_response_minutes")
    return {"max_runtime_seconds": values["max_response_minutes"] * 60,
            "max_wait_seconds": values["max_queue_wait_minutes"] * 60}
