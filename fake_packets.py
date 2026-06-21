import base64
import json
import random
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4


def encode_payload(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(raw).decode("utf-8")


def make_fake_packet(balloon_number: int, base_time: datetime | None = None) -> dict[str, str]:
    if base_time is None:
        base_time = datetime.now(timezone.utc)

    # Simulates roughly once-per-minute transmissions with a safe window that keeps latency under 300s.
    downlink_time = base_time + timedelta(seconds=random.randint(-240, 60))
    balloon_id = f"B-{balloon_number:05d}"

    telemetry = {
        "lat": round(random.uniform(-70, 70), 5),
        "lon": round(random.uniform(-180, 180), 5),
        "altitude_m": round(random.uniform(15000, 25000), 2),
        "temperature_c": round(random.uniform(-65, -25), 2),
        "pressure_hpa": round(random.uniform(40, 150), 2),
        "humidity": round(random.uniform(0, 1), 3),
    }

    return {
        "packet_id": f"pkt-{balloon_id}-{int(downlink_time.timestamp())}-{uuid4().hex[:8]}",
        "balloon_id": balloon_id,
        "downlink_time": downlink_time.isoformat(),
        "encoded_payload": encode_payload(telemetry),
    }


if __name__ == "__main__":
    print(json.dumps(make_fake_packet(1), indent=2))
