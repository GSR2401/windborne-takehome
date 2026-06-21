import base64
import json
from typing import Any


REQUIRED_FIELDS = {"lat", "lon", "altitude_m", "temperature_c", "pressure_hpa"}


def decode_payload(encoded_payload: str) -> dict[str, Any]:
    """Decode base64(JSON) telemetry into a Python dictionary."""
    try:
        raw = base64.b64decode(encoded_payload.encode("utf-8"), validate=True)
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # intentionally converted to ValueError for caller
        raise ValueError(f"Invalid encoded payload: {exc}") from exc

    missing = REQUIRED_FIELDS - set(data.keys())
    if missing:
        raise ValueError(f"Decoded payload missing required fields: {sorted(missing)}")

    return data
