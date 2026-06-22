# WindBorne System Design

This document covers assumptions, packet journey, concurrency model, and tradeoffs for the
downlink pipeline demo. Intended as a reference for the follow-up interview.

---

## 1. Assumptions

| # | Assumption | Why |
|---|---|---|
| 1 | One flat FIFO queue per WindBorne account at satellite provider | Assignment says "per-account" — not per balloon, not per region |
| 2 | Provider holds packet until we ACK — if no ACK, it resends | This is why idempotency exists — same packet can arrive twice |
| 3 | `packet_id` is globally unique, assigned by provider before we see it | We use it as DB primary key |
| 4 | Wire format is base64-encoded JSON | Real format is likely binary/protobuf — simplified for demo |
| 5 | One packet → zero or one observation (not multiple) | `save_observation` called once per packet |
| 6 | $1 revenue is per observation point | `earned_revenue = 1 if latency <= 300 else 0` |
| 7 | 5-minute window is from balloon's downlink time, not satellite receipt time | `latency = customer_visible_time - downlink_time` |
| 8 | Provider pushes to us (webhook model) | We built `POST /downlink_packet` — real design would be us polling their API |
| 9 | Packets can arrive out of order relative to downlink_time | ±5 min variation, no ordering enforcement in DB |
| 10 | Downlink time from balloon is trusted as-is | No validation that it's within a reasonable range |
| 11 | Processing order across balloons doesn't matter | B-00001 and B-00002 can be decoded in any order |

---

## 2. Packet Journey

```
Balloon
  │
  │  transmits telemetry over radio
  ▼
Satellite
  │
  │  stores in per-account FIFO queue
  ▼
FIFO Queue (at satellite provider)
  │
  │  provider pushes → POST /downlink_packet
  ▼
Our Server — persist_packet_before_ack()
  │
  │  BEGIN transaction
  │  INSERT INTO packets (status = RECEIVED)
  │  COMMIT
  │
  ▼
ACK returned to provider (202) ← provider removes packet from queue
  │
  │  background_tasks.add_task(process_packet)
  ▼
Background: process_packet()
  │
  ├─ decode_payload()
  │    base64 decode → JSON parse → validate required fields
  │
  ├─ if no_observation flag → mark PROCESSED, stop
  │
  ├─ if decode fails → mark DECODE_FAILED, store error, stop
  │
  └─ save_observation()
       INSERT INTO observations
       UPDATE packets status = PROCESSED
       calculate latency = customer_visible_time - downlink_time
       assign earned_revenue (1 if latency <= 300s, else 0)
  │
  ▼
GET /observations ← customer sees it here
```

---

## 3. What Is Parallel vs Synchronous

### Intake (synchronous, blocking)
```
receive request → persist to DB → return ACK
```
This **must** be synchronous. We cannot ACK before persisting — if we crash between ACK and
persist, the provider removes the packet from the queue and it is gone forever.

FastAPI handles multiple simultaneous HTTP requests concurrently, so 100 packets can all be
in the persist stage at the same time — each in its own thread.

### Processing (asynchronous, non-blocking)
```
BackgroundTasks runs decode after ACK is returned.
```
Does not block intake of the next packet. All decode jobs run in FastAPI's background thread
pool concurrently.

### Customer reads (independent, always available)
```
GET /observations
GET /metrics
GET /packets/{id}
```
These never block. They read directly from the DB at any time, independent of intake or processing.

### Visually
```
Packet A:  [persist]──[ACK]  [decode]──[save_obs]
Packet B:      [persist]──[ACK]  [decode]──[save_obs]
Packet C:          [persist]──[ACK]  [decode]──[save_obs]
              ──────────────────────────────────────────► time
              ↑ concurrent intake     ↑ concurrent decode
```

---

## 4. Tradeoffs

### Tradeoff 1: Persist before ACK

| | Demo (our choice)           | Alternative              |
|---|---|---|
| How     | ACK after DB commit         | ACK on receipt           |
| Speed   | ~5ms slower per packet      | Immediate ACK            |
| Risk    | Near-zero packet loss       | Crash = packet gone forever |
| Why     | "Must not lose any packet"  | Unacceptable             |

> **Rationale:** 5ms write cost is acceptable at 167 packets/sec. ACK-on-receipt is valid
> only if the team accepts a small loss window during crashes.

---

### Tradeoff 2: Push model vs polling

| | Demo (our choice)           | Production               |
|---|---|---|
| How      | Provider pushes to us      | We poll provider's API   |
| Benefit  | Simple, no SDK needed      | We control drain rate    |
| Drawback | Provider may not support push | Requires provider SDK |

> **Rationale:** Push model demonstrates the intake logic correctly. In production, parallel
> pollers drain the queue faster than packets arrive (>167 packets/sec).

---

### Tradeoff 3: In-memory queue vs durable queue

| | Demo (our choice)           | Production               |
|---|---|---|
| What       | FastAPI BackgroundTasks   | SQS / Pub-Sub / Kafka    |
| Durability | Lost on server crash      | Survives crash           |
| Recovery   | Startup reprocesses RECEIVED | Queue retries automatically |

> **Rationale:** No infrastructure dependency for the demo. Startup crash recovery closes
> the gap. Production replaces this with a durable queue entirely.

---

### Tradeoff 4: SQLite vs Postgres

| | Demo (our choice)           | Production               |
|---|---|---|
| What       | SQLite + WAL mode         | Postgres / CockroachDB   |
| Scale      | Single node               | Replicated, scalable     |
| Throughput | ~460–740 packets/sec      | Thousands/sec            |
| Why        | Zero infra, correct semantics | Swap the connection string |

> **Rationale:** Schema and queries are identical in Postgres. SQLite handles demo load
> easily and deploys anywhere with no infrastructure.

---

### Tradeoff 5: Serial vs parallel intake

| | What happens                                          |
|---|---|
| Provider side    | Fills FIFO at ~167 packets/sec             |
| Demo intake      | FastAPI concurrent POSTs — parallel        |
| Production poll  | Need parallel pollers or queue grows       |
| Risk             | Backlog → late observations → $0 revenue  |

> **Rationale:** FIFO guarantees arrival order at the provider — we don't need to process
> in that order, only guarantee no packet is dropped. Parallel pollers drain faster than
> packets arrive while still persisting each one before ACK.

