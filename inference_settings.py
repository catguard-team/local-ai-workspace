"""Validated response limits with atomic persistence for the web settings UI."""
import json
import os
from pathlib import Path
import tempfile

DEFAULTS = {"max_response_minutes": 60, "max_queue_wait_minutes": 120}
SETTINGS_PATH = Path(__file__).resolve().parent / ".runtime" / "inference-settings.json"


def validate_settings(supplied):
    if not isinstance(supplied, dict) or set(supplied) - set(DEFAULTS):
        raise ValueError("Unknown inference settings; use max_response_minutes and max_queue_wait_minutes")
    values = {**DEFAULTS, **supplied}
    for name, value in values.items():
        if type(value) is not int or not 1 <= value <= 1440:
            raise ValueError(f"{name} must be an integer from 1 to 1440 minutes")
    if values["max_queue_wait_minutes"] < values["max_response_minutes"]:
        raise ValueError("max_queue_wait_minutes must be at least max_response_minutes")
    return values


def as_limits(values):
    return {"max_runtime_seconds": values["max_response_minutes"] * 60,
            "max_wait_seconds": values["max_queue_wait_minutes"] * 60}


def load_limits(path=SETTINGS_PATH):
    supplied = {}
    if path.exists():
        try:
            supplied = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise ValueError("Cannot read .runtime/inference-settings.json as JSON") from exc
    return as_limits(validate_settings(supplied))


def save_settings(values, path=SETTINGS_PATH):
    values = validate_settings(values)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".inference-", suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            json.dump(values, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return as_limits(values)
