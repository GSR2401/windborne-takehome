import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from models import PacketStatus

DB_PATH = os.getenv("WINDBORNE_DB_PATH", "windborne.db")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso(ts: str) -> datetime:
    # Accept trailing Z for UTC.
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts)


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS packets (
                packet_id TEXT PRIMARY KEY,
                balloon_id TEXT NOT NULL,
                downlink_time TEXT NOT NULL,
                raw_payload TEXT NOT NULL,
                status TEXT NOT NULL,
                received_at TEXT NOT NULL,
                processed_at TEXT,
                error TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS observations (
                observation_id TEXT PRIMARY KEY,
                packet_id TEXT NOT NULL UNIQUE,
                balloon_id TEXT NOT NULL,
                downlink_time TEXT NOT NULL,
                customer_visible_time TEXT NOT NULL,
                latency_seconds REAL NOT NULL,
                earned_revenue INTEGER NOT NULL,
                data_json TEXT NOT NULL,
                FOREIGN KEY(packet_id) REFERENCES packets(packet_id)
            )
            """
        )
        conn.commit()


def persist_packet_before_ack(packet_id: str, balloon_id: str, downlink_time: str, raw_payload: str) -> str:
    """
    Critical ACK rule:
    - Begin transaction
    - Insert raw packet durably
    - Commit
    - Only then caller returns ACK

    Duplicate packet IDs are idempotent: retries return the existing status.
    """
    received_at = utc_now_iso()
    with get_conn() as conn:
        try:
            conn.execute("BEGIN")
            conn.execute(
                """
                INSERT INTO packets(packet_id, balloon_id, downlink_time, raw_payload, status, received_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(packet_id) DO NOTHING
                """,
                (packet_id, balloon_id, downlink_time, raw_payload, PacketStatus.RECEIVED.value, received_at),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        row = conn.execute("SELECT status FROM packets WHERE packet_id = ?", (packet_id,)).fetchone()
        return row["status"]


def get_packet(packet_id: str) -> Optional[dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM packets WHERE packet_id = ?", (packet_id,)).fetchone()
        return dict(row) if row else None


def list_packets() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM packets ORDER BY received_at DESC").fetchall()
        return [dict(r) for r in rows]


def mark_packet_failed(packet_id: str, error: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE packets SET status = ?, processed_at = ?, error = ? WHERE packet_id = ?",
            (PacketStatus.DECODE_FAILED.value, utc_now_iso(), error, packet_id),
        )
        conn.commit()


def save_observation(packet_id: str, balloon_id: str, downlink_time: str, data: dict[str, Any]) -> None:
    visible_time = utc_now_iso()
    latency_seconds = (parse_iso(visible_time) - parse_iso(downlink_time)).total_seconds()
    earned_revenue = 1 if latency_seconds <= 300 else 0
    observation_id = f"obs-{packet_id}"

    with get_conn() as conn:
        conn.execute("BEGIN")
        conn.execute(
            """
            INSERT INTO observations(
                observation_id, packet_id, balloon_id, downlink_time,
                customer_visible_time, latency_seconds, earned_revenue, data_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(packet_id) DO NOTHING
            """,
            (
                observation_id,
                packet_id,
                balloon_id,
                downlink_time,
                visible_time,
                latency_seconds,
                earned_revenue,
                json.dumps(data),
            ),
        )
        conn.execute(
            "UPDATE packets SET status = ?, processed_at = ?, error = NULL WHERE packet_id = ?",
            (PacketStatus.PROCESSED.value, visible_time, packet_id),
        )
        conn.commit()


def list_observations(balloon_id: Optional[str] = None, limit: int = 100) -> list[dict[str, Any]]:
    with get_conn() as conn:
        if balloon_id:
            rows = conn.execute(
                "SELECT * FROM observations WHERE balloon_id = ? ORDER BY customer_visible_time DESC LIMIT ?",
                (balloon_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM observations ORDER BY customer_visible_time DESC LIMIT ?", (limit,)
            ).fetchall()

    observations = []
    for row in rows:
        item = dict(row)
        item["data"] = json.loads(item.pop("data_json"))
        observations.append(item)
    return observations


def metrics() -> dict[str, Any]:
    with get_conn() as conn:
        packets_received = conn.execute("SELECT COUNT(*) AS c FROM packets").fetchone()["c"]
        packets_processed = conn.execute(
            "SELECT COUNT(*) AS c FROM packets WHERE status = ?", (PacketStatus.PROCESSED.value,)
        ).fetchone()["c"]
        decode_failed = conn.execute(
            "SELECT COUNT(*) AS c FROM packets WHERE status = ?", (PacketStatus.DECODE_FAILED.value,)
        ).fetchone()["c"]
        observations_created = conn.execute("SELECT COUNT(*) AS c FROM observations").fetchone()["c"]
        observations_within_5_min = conn.execute(
            "SELECT COUNT(*) AS c FROM observations WHERE earned_revenue = 1"
        ).fetchone()["c"]
        total_revenue = conn.execute("SELECT COALESCE(SUM(earned_revenue), 0) AS s FROM observations").fetchone()["s"]

    return {
        "packets_received": packets_received,
        "packets_processed": packets_processed,
        "decode_failed": decode_failed,
        "observations_created": observations_created,
        "observations_within_5_min": observations_within_5_min,
        "total_revenue": total_revenue,
    }
