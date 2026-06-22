# WindBorne Take-Home Demo

**Deployed URL:** https://web-production-f540e.up.railway.app
**Swagger UI:** https://web-production-f540e.up.railway.app/docs
**GitHub:** https://github.com/GSR2401/windborne-takehome

---

## What I Chose to Build and Why

The most important rule in the assignment is: **"we must not lose any packet a balloon has sent."**
So I built the intake guarantee first — the server only ACKs after the raw packet is committed to
the database in a transaction. Processing happens after persistence so intake stays fast under bursts.

The full pipeline:
```
receive → persist (transaction) → ACK → decode (background) → observation → revenue
```

I also built startup crash recovery: on restart, any packet stuck in `RECEIVED` status is
automatically reprocessed, closing the gap between an in-memory task queue and a durable one.

---

## System Design

### Scale
- ~10,000 balloons aloft, each transmitting ~once per minute (±5 min variation)
- Sustained load: **~167 packets/second, 24/7**
- Revenue rule: observation must reach the customer within **5 minutes** of balloon downlink time

### Production Architecture

```
Satellite provider FIFO queue
        |
        v
  [Intake pollers]  <-- multiple parallel workers poll provider API
        |
        | persist raw packet (BEGIN / INSERT / COMMIT)
        v
  Durable DB (Postgres / CockroachDB)
        |
        | publish packet_id
        v
  Durable queue (SQS / Pub-Sub / Kafka)
        |
        v
  [Decoder workers]  <-- stateless, horizontally scalable
        |
        v
  Observations table  -->  Customer API
```

### Key Design Questions

**How do you receive from the satellite provider's FIFO queue?**
The provider holds packets until we ACK them. In production a poller calls the provider's dequeue
API in a loop: fetch → persist → ACK. Multiple parallel pollers drain the queue faster than packets
arrive. This demo simulates intake with `POST /downlink_packet` — the caller plays the provider role.

**What queue sits between intake and decoders?**
Production: SQS/Pub-Sub/Kafka. Intake publishes `packet_id` after persisting. Decoder workers
consume independently — a crash loses no work because the message stays in the queue.
Demo: FastAPI `BackgroundTasks` (in-memory). Mitigated by startup crash recovery.

**How many decoder workers, how do they scale?**
The decoder is a pure stateless function (base64 → validate → write). Scale worker count
independently of intake. Auto-scale when queue depth rises. Demo: one background thread pool,
sufficient for 460–740 packets/sec locally.

**What happens if a worker crashes mid-decode — is the packet safe?**
The raw packet is committed to DB before ACK, so it is always safe. If the server crashes before
decode completes, the packet stays in `RECEIVED` status. On next startup, the app automatically
reprocesses all `RECEIVED` packets.

**What's the retry policy when processing fails?**
Demo: one attempt; failure marks packet `DECODE_FAILED` with error stored for inspection.
Production: exponential backoff (3–5 retries), then dead-letter queue with alerting. Malformed
packets (bad base64, missing fields) go straight to dead-letter — retrying them always fails.

**How do you ensure the 5-minute SLA at scale?**
The 5-minute SLA is a throughput problem — if intake falls behind, every packet misses it.
1. Keep backlog near zero by auto-scaling workers when queue depth grows.
2. Alert when p99 processing latency exceeds 4 minutes (one minute headroom).
3. Prioritize packets whose downlink time is approaching the 5-minute window if queue falls behind.

### Assumptions
- One flat FIFO queue per WindBorne account (not per balloon or region)
- Provider retries until ACK — packet stays in queue if we don't ACK
- `packet_id` is globally unique, assigned by the provider
- Wire format is base64-encoded JSON (real format likely binary/protobuf)
- One packet → zero or one observation (not multiple)
- 5-minute window measured from balloon's downlink time, not satellite receipt time
- Processing order across different balloons does not matter

### Tradeoffs

| Decision | Choice | Tradeoff |
|---|---|---|
| ACK timing | Persist first, then ACK | ~5ms slower per packet but zero packet loss |
| Intake model | Push (POST endpoint) | Simpler demo; production would poll provider API |
| Task queue | FastAPI BackgroundTasks | No infra dependency; not durable across crashes |
| Crash recovery | Startup reprocess of RECEIVED | Closes durability gap without a real queue |
| Storage | SQLite + WAL | Zero infra, correct semantics; not horizontally scalable |

---

## Demo Architecture

```
POST /downlink_packet
        |
        v
SQLite packets table  <-- raw packet persisted in transaction
        |
        v
ACK 202 returned to caller
        |
        v
Background: decode base64(JSON) → validate fields
        |
        ├── no_observation flag → mark PROCESSED, stop
        ├── decode error → mark DECODE_FAILED, store error
        └── success → SQLite observations table
                              |
                              v
                    GET /observations  (customer API)
```

---

## Endpoints

```
POST /downlink_packet           Submit/simulate encoded balloon packet
POST /uplink_command            Queue a command for uplink to a balloon
GET  /observations              Customer-visible observations + revenue
GET  /packets/{packet_id}       Inspect a packet's full lifecycle state
GET  /packets                   List all packets
GET  /metrics                   System metrics and revenue totals
GET  /health                    Health check
```

---

## Example Requests

**Submit a packet:**
```bash
curl -X POST https://web-production-f540e.up.railway.app/downlink_packet \
  -H "Content-Type: application/json" \
  -d '{
    "packet_id": "pkt-B001-demo-001",
    "balloon_id": "B001",
    "downlink_time": "2026-06-21T10:00:00+00:00",
    "encoded_payload": "eyJsYXQiOjM3Ljc3NDksImxvbiI6LTEyMi40MTk0LCJhbHRpdHVkZV9tIjoxODIwMCwidGVtcGVyYXR1cmVfYyI6LTQ4LjUsInByZXNzdXJlX2hwYSI6NzIuMSwiaHVtaWRpdHkiOjAuMTJ9"
  }'
```

**View customer observations:**
```bash
curl https://web-production-f540e.up.railway.app/observations
```

**Inspect packet lifecycle:**
```bash
curl https://web-production-f540e.up.railway.app/packets/pkt-B001-demo-001
```

**System metrics:**
```bash
curl https://web-production-f540e.up.railway.app/metrics
```

**Send an uplink command:**
```bash
curl -X POST https://web-production-f540e.up.railway.app/uplink_command \
  -H "Content-Type: application/json" \
  -d '{
    "command_id": "cmd-B001-ping-001",
    "balloon_id": "B001",
    "command_type": "ping",
    "parameters": {}
  }'
```

---

## Running Locally

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3.11 -m uvicorn app:app --reload
```

Open: http://127.0.0.1:8000/docs

**Generate a fake encoded packet:**
```bash
python3.11 fake_packets.py
```

**Run burst demo (shows before/after metrics):**
```bash
python3.11 burst_demo.py 100     # 100 packets
python3.11 burst_demo.py 1000    # 1000 packets
```

---

## Tests

```bash
python3.11 -m pytest -v tests/test_pipeline.py
```

| Test | What it proves |
|---|---|
| `test_valid_packet_is_persisted_acked_and_observed` | Full happy path end to end |
| `test_duplicate_packet_is_idempotent` | Provider retry safety — same packet twice = one row |
| `test_invalid_packet_is_stored_but_marked_failed` | Bad data is stored and traceable, not dropped |
| `test_revenue_is_zero_after_five_minutes` | SLA revenue rule enforced correctly |
| `test_no_observation_packet_is_processed_but_skipped` | Zero-observation transmissions handled |
| `test_burst_of_packets_all_persisted` | 100 packets under burst all persist and are observed |

---

## Files

```
app.py              FastAPI routes and startup crash recovery
models.py           Request/response models including uplink
storage.py          SQLite persistence, metrics, status helpers
decoder.py          Base64 JSON decoder with field validation
pipeline.py         Packet → observation processing logic
fake_packets.py     Fake encoded packet generator for testing
burst_demo.py       Burst load demo with before/after metrics
tests/              Pytest test suite
requirements.txt    Python dependencies
Procfile            Railway deployment start command
```

---

## What I Would Change Before Production

1. **Replace SQLite with Postgres/CockroachDB** — replicated, horizontally scalable
2. **Replace BackgroundTasks with a durable queue** (SQS/Pub-Sub) — processing jobs survive crashes automatically
3. **Replace push endpoint with a poller** — poll satellite provider's FIFO API with parallel workers to drain at >167 packets/sec
4. **Add retry with exponential backoff** — currently DECODE_FAILED is terminal; production needs retries + dead-letter queue
5. **Add observability** — latency histograms, queue depth alerts, SLA breach alerting when p99 approaches 4 minutes
6. **Add authentication and rate limiting** on all endpoints
7. **Scale decoder workers independently** of intake — decoders are stateless and trivially parallelizable

---

## AI Usage

Claude Code was used throughout: to structure the architecture, generate FastAPI boilerplate,
write tests, debug WAL mode behavior, and reason through design tradeoffs. All design decisions —
what to build, what to skip, and the tradeoffs taken — were made based on the assignment requirements.
