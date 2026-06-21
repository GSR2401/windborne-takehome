# WindBorne Take-Home Demo

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
