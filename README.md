# WindBorne Take-Home Demo

## System Design

### Scale assumptions
- ~10,000 balloons aloft, each transmitting ~once per minute with ±5 min variation
- Sustained load: ~167 packets/second, 24/7
- Revenue rule: observation must reach customer within 5 minutes of downlink time

### Production architecture

```text
Satellite provider FIFO queue
        |
        v
  [Intake poller]  <-- polls provider API in a loop, one packet at a time
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

### Answers to the key design questions

**How do you receive from the satellite provider's FIFO queue?**
The provider holds packets until we ACK them. In production a poller service calls the provider's dequeue API in a loop: fetch one packet, persist it to our DB, then send the provider ACK. This demo simulates that with `POST /downlink_packet` — the caller plays the role of the provider pushing to us.

**What queue sits between intake and decoders?**
In production: SQS, Pub-Sub, or Kafka. The intake poller publishes `packet_id` after persisting. Decoder workers consume from the queue independently. This means a decoder crash loses no work — the message stays in the queue until a worker ACKs it.
In this demo: FastAPI `BackgroundTasks` (in-process, in-memory). Crash-safe for the raw packet (persisted before ACK), but the processing job would be lost. Mitigated here by startup crash recovery (see below).

**How many decoder workers, how do they scale?**
The decoder is a pure stateless function: base64 decode → validate → write observation. In production, scale worker count independently of intake. Add workers when queue depth grows. In this demo there is one worker (the FastAPI background thread pool), sufficient for the demo throughput of 460–740 packets/sec.

**What happens if a worker crashes mid-decode — is the packet safe?**
Yes. The raw packet is committed to the DB *before* the ACK is returned. If the server crashes after persist but before decode finishes, the packet stays in `RECEIVED` status. On next startup, the app scans for all `RECEIVED` packets and reprocesses them — no packet is permanently lost.

**What's the retry policy when processing fails?**
In this demo: one attempt; on failure the packet is marked `DECODE_FAILED` with the error message stored for inspection.
In production: exponential backoff with 3–5 retries, then move to a dead-letter queue. Dead-letter packets trigger an alert for manual inspection. Malformed packets (bad base64, missing fields) go straight to dead-letter — retrying them would always fail.

**How do you ensure the 5-minute SLA at scale?**
The system measures latency per observation (`customer_visible_time - downlink_time`) and records whether it earned revenue. To *guarantee* the SLA in production:
1. Keep the processing backlog near zero by auto-scaling decoder workers when queue depth rises.
2. Alert when p99 processing latency exceeds 4 minutes (one minute headroom).
3. Prioritize packets whose downlink time is approaching the 5-minute window if the queue ever falls behind.



This project demonstrates the important part of the assignment:

```text
Downlinked balloon packet
  -> persist raw packet before ACK
  -> decode payload
  -> create customer-visible observation
  -> calculate whether it earned $1 within the 5-minute SLA
```

## Why this architecture?

The assignment says packets stay in the satellite provider's FIFO buffer until we acknowledge receipt, and that we must not lose balloon packets. So this demo follows the most important rule:

> ACK only after the raw packet is persisted.

Processing happens after persistence so intake can remain fast during bursts.

## Architecture

```text
POST /downlink_packet
        |
        v
SQLite packets table  <- raw packet persisted here first
        |
        v
ACK 202 Accepted
        |
        v
Background processing
        |
        v
Decode base64(JSON)
        |
        v
SQLite observations table
        |
        v
GET /observations customer API
```

Production version would replace single-node SQLite with replicated durable storage and a durable queue such as Kafka/SQS.

## Files

```text
app.py              FastAPI routes
models.py           Request/response models
storage.py          SQLite persistence and metrics
decoder.py          Base64 JSON decoder
pipeline.py         Packet -> observation processing
fake_packets.py     Fake encoded packet generator
tests/              Pytest test cases
requirements.txt    Python dependencies
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run locally

```bash
uvicorn app:app --reload
```

Open:

```text
http://127.0.0.1:8000/docs
```

## Generate fake packet

```bash
python fake_packets.py
```

Example packet shape:

```json
{
  "packet_id": "pkt-B-00001-1781971200",
  "balloon_id": "B-00001",
  "downlink_time": "2026-06-20T12:00:00+00:00",
  "encoded_payload": "eyJsYXQiOj..."
}
```

## Example request

```bash
curl -X POST http://127.0.0.1:8000/downlink_packet \
  -H "Content-Type: application/json" \
  -d '{
    "packet_id": "pkt-B001-demo",
    "balloon_id": "B001",
    "downlink_time": "2026-06-20T12:00:00+00:00",
    "encoded_payload": "eyJsYXQiOjM3Ljc3NDksImxvbiI6LTEyMi40MTk0LCJhbHRpdHVkZV9tIjoxODIwMCwidGVtcGVyYXR1cmVfYyI6LTQ4LjUsInByZXNzdXJlX2hwYSI6NzIuMSwiaHVtaWRpdHkiOjAuMTJ9"
  }'
```

## Useful endpoints

```text
POST /downlink_packet       Submit/simulate encoded balloon packet
GET  /observations          Customer-visible observations
GET  /packets/{packet_id}   Inspect packet lifecycle state
GET  /packets               List packets
GET  /metrics               Revenue and system metrics
GET  /health                Health check
```

## Run tests

```bash
pytest
```

Tests cover:

- valid packet persists, ACKs, and creates observation
- duplicate packet submission is idempotent
- invalid packet is stored but marked `DECODE_FAILED`
- late observation earns `$0`
- burst of 100 packets is accepted and persisted

## What I would change before production

- Replace SQLite with replicated durable storage such as managed Postgres/CockroachDB.
- Add a durable queue between intake and processing, such as Kafka/SQS/PubSub.
- Horizontally scale decoder workers.
- Add retry policies and a dead-letter queue.
- Add authentication, rate limits, observability, dashboards, and alerting.
- Use provider-specific ACK APIs instead of HTTP response ACK simulation.

## AI usage note

AI was used to help structure the architecture, generate boilerplate FastAPI code, and outline reliability-focused tests. Final design choices are based on the assignment requirements: persistence before acknowledgement, traceability, and customer-visible observation latency.
