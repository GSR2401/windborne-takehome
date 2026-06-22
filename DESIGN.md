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

### Tradeoff 1: Persist before ACK (durability over speed)

| | Our choice | Alternative |
|---|---|---|
| What | ACK only after DB commit | ACK on receipt, persist async |
| Speed | Slower — adds ~5ms per packet | Faster — ACK immediately |
| Risk | Near-zero packet loss | Crash between ACK and persist = packet gone forever |
| Why | Assignment says "must not lose any packet" | Unacceptable given hard requirement |

> **Rationale:** "We chose durability over speed. The 5ms write cost is acceptable at
> 167 packets/sec. If the team prefers speed, ACK-on-receipt is valid — but requires accepting
> a small loss window during crashes."

---

### Tradeoff 2: Push model instead of polling

| | Our choice | Production |
|---|---|---|
| What | Provider pushes → `POST /downlink_packet` | We poll provider's dequeue API in a loop |
| Demo | Simple, shows intake logic clearly | Requires satellite provider SDK/API |
| Production | Provider may not support push | Correct model — we control drain rate |
| Why | Sufficient to demonstrate the pipeline correctly | |

> **Rationale:** "Push model simulates intake correctly for the demo. In production
> we'd poll their FIFO and run parallel pollers so drain rate exceeds 167 packets/second."

---

### Tradeoff 3: In-memory queue instead of durable queue

| | Our choice | Production |
|---|---|---|
| What | FastAPI `BackgroundTasks` | SQS / Pub-Sub / Kafka |
| Durability | Job lost if server crashes after persist | Job survives crash — stays in queue |
| Recovery | Startup scan reprocesses `RECEIVED` packets | Queue retries automatically |
| Why | No infrastructure dependency, sufficient for demo | |

> **Rationale:** "BackgroundTasks is not durable. We mitigate this with startup crash
> recovery — on boot we reprocess all RECEIVED packets. In production, a durable queue makes
> this automatic and removes the need for the startup scan."

---

### Tradeoff 4: SQLite instead of Postgres

| | Our choice | Production |
|---|---|---|
| What | SQLite + WAL mode | Postgres / CockroachDB |
| Scale | Single file, single node | Replicated, horizontally scalable |
| Throughput | ~460–740 packets/sec locally | Thousands/sec with connection pooling |
| Why | Zero infrastructure, deploy anywhere, correct semantics | |

> **Rationale:** "SQLite with WAL mode gives correct transactional semantics and handles
> the demo load easily. Schema and queries are identical in Postgres — swap the connection string."

---

### Tradeoff 5: FIFO throughput vs serial processing

| | What happens |
|---|---|
| Satellite provider | Fills FIFO at ~167 packets/sec asynchronously |
| Our intake (demo) | FastAPI handles concurrent POSTs — effectively parallel |
| Our intake (production polling) | Need multiple parallel pollers or we fall behind |
| Risk if too slow | Queue backlog grows → observations arrive late → $0 revenue |

The 5-minute SLA is fundamentally a **throughput problem**. If intake falls behind, every packet
misses the SLA regardless of how fast decode is. One synchronous poller can process ~20–50
packets/sec — not enough. Production fix: parallel pollers + auto-scale on queue depth.

> **Rationale:** "The FIFO guarantees arrival order at the provider. We don't need to
> process in that order — only guarantee no packet is dropped. Running parallel pollers lets
> us drain faster than packets arrive while still persisting each one before ACK."

