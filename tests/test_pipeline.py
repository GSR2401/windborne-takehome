import os
import tempfile
from datetime import datetime, timezone, timedelta

# Set DB path before importing app/storage.
tmp = tempfile.NamedTemporaryFile(delete=False)
os.environ["WINDBORNE_DB_PATH"] = tmp.name

from fastapi.testclient import TestClient  # noqa: E402

from app import app  # noqa: E402
from fake_packets import encode_payload, make_fake_packet  # noqa: E402
from storage import init_db  # noqa: E402

client = TestClient(app)


def setup_function():
    if os.path.exists(os.environ["WINDBORNE_DB_PATH"]):
        os.remove(os.environ["WINDBORNE_DB_PATH"])
    init_db()


def test_valid_packet_is_persisted_acked_and_observed():
    packet = make_fake_packet(1, base_time=datetime.now(timezone.utc))

    response = client.post("/downlink_packet", json=packet)

    assert response.status_code == 202
    assert response.json()["acked"] is True

    state = client.get(f"/packets/{packet['packet_id']}").json()
    assert state["packet_id"] == packet["packet_id"]
    assert state["status"] in ["RECEIVED", "PROCESSED"]

    observations = client.get("/observations").json()["observations"]
    assert len(observations) == 1
    assert observations[0]["packet_id"] == packet["packet_id"]


def test_duplicate_packet_is_idempotent():
    packet = make_fake_packet(2, base_time=datetime.now(timezone.utc))

    assert client.post("/downlink_packet", json=packet).status_code == 202
    assert client.post("/downlink_packet", json=packet).status_code == 202

    metrics = client.get("/metrics").json()
    assert metrics["packets_received"] == 1
    assert metrics["observations_created"] == 1


def test_invalid_packet_is_stored_but_marked_failed():
    packet = {
        "packet_id": "bad-1",
        "balloon_id": "B-BAD",
        "downlink_time": datetime.now(timezone.utc).isoformat(),
        "encoded_payload": "not-base64",
    }

    response = client.post("/downlink_packet", json=packet)
    assert response.status_code == 202

    state = client.get("/packets/bad-1").json()
    assert state["status"] == "DECODE_FAILED"
    assert "Invalid encoded payload" in state["error"]


def test_revenue_is_zero_after_five_minutes():
    old_time = datetime.now(timezone.utc) - timedelta(minutes=6)
    payload = {
        "lat": 10,
        "lon": 20,
        "altitude_m": 20000,
        "temperature_c": -40,
        "pressure_hpa": 80,
    }
    packet = {
        "packet_id": "late-1",
        "balloon_id": "B-LATE",
        "downlink_time": old_time.isoformat(),
        "encoded_payload": encode_payload(payload),
    }

    assert client.post("/downlink_packet", json=packet).status_code == 202
    observations = client.get("/observations").json()["observations"]
    assert observations[0]["earned_revenue"] == 0


def test_no_observation_packet_is_processed_but_skipped():
    payload = {
        "lat": 10,
        "lon": 20,
        "altitude_m": 20000,
        "temperature_c": -40,
        "pressure_hpa": 80,
        "no_observation": True,
    }
    packet = {
        "packet_id": "no-obs-1",
        "balloon_id": "B-NOOBS",
        "downlink_time": datetime.now(timezone.utc).isoformat(),
        "encoded_payload": encode_payload(payload),
    }

    assert client.post("/downlink_packet", json=packet).status_code == 202

    state = client.get("/packets/no-obs-1").json()
    assert state["status"] == "PROCESSED"

    observations = client.get("/observations").json()["observations"]
    assert all(o["packet_id"] != "no-obs-1" for o in observations)


def test_burst_of_packets_all_persisted():
    for i in range(100):
        packet = make_fake_packet(i, base_time=datetime.now(timezone.utc))
        response = client.post("/downlink_packet", json=packet)
        assert response.status_code == 202

    metrics = client.get("/metrics").json()
    assert metrics["packets_received"] == 100
    assert metrics["observations_created"] == 100
