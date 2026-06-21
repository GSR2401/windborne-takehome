from typing import Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query

from models import DownlinkPacketRequest, DownlinkPacketResponse, UplinkCommandRequest, UplinkCommandResponse
from pipeline import process_packet
from storage import (
    get_packet,
    get_packets_by_status,
    init_db,
    list_observations,
    list_packets,
    metrics,
    persist_packet_before_ack,
)

app = FastAPI(
    title="WindBorne Take-Home Demo",
    description="Downlink packet ingestion -> persistence-before-ACK -> decoding -> customer-visible observations.",
    version="1.0.0",
)


@app.on_event("startup")
def startup() -> None:
    init_db()
    # Crash recovery: any packet still RECEIVED was persisted but never processed.
    # Reprocess them now so no packet is permanently stuck.
    for packet in get_packets_by_status("RECEIVED"):
        process_packet(packet["packet_id"])


@app.post("/downlink_packet", response_model=DownlinkPacketResponse, status_code=202)
def downlink_packet(payload: DownlinkPacketRequest, background_tasks: BackgroundTasks) -> DownlinkPacketResponse:
    """
    Simulates satellite-provider packet delivery.

    ACK rule:
    The response is returned only after the raw packet is committed to SQLite.
    Processing is kicked off after persistence so intake stays fast under bursts.
    """
    try:
        status = persist_packet_before_ack(
            packet_id=payload.packet_id,
            balloon_id=payload.balloon_id,
            downlink_time=payload.downlink_time,
            raw_payload=payload.encoded_payload,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Packet was not acknowledged because persistence failed: {exc}")

    background_tasks.add_task(process_packet, payload.packet_id)
    return DownlinkPacketResponse(
        packet_id=payload.packet_id,
        status=status,
        acked=True,
        message="ACK returned after packet was persisted. Processing continues in background.",
    )


@app.get("/observations")
def observations(balloon_id: Optional[str] = None, limit: int = Query(default=100, ge=1, le=1000)):
    """Customer-facing observations plus revenue summary."""
    items = list_observations(balloon_id=balloon_id, limit=limit)
    return {
        "observations": items,
        "count": len(items),
        "total_revenue_in_response": sum(item["earned_revenue"] for item in items),
    }


@app.get("/packets/{packet_id}")
def packet_state(packet_id: str):
    """Inspect a packet's lifecycle state."""
    packet = get_packet(packet_id)
    if packet is None:
        raise HTTPException(status_code=404, detail="packet not found")
    return packet


@app.get("/packets")
def packets():
    return {"packets": list_packets()}


@app.get("/metrics")
def system_metrics():
    return metrics()


@app.post("/uplink_command", response_model=UplinkCommandResponse, status_code=202)
def uplink_command(payload: UplinkCommandRequest) -> UplinkCommandResponse:
    """
    Queue a command for uplink to a balloon.

    In production: write to a durable uplink queue (SQS/Pub-Sub).
    A worker picks it up and calls the satellite provider's uplink API.
    Here we acknowledge receipt to demonstrate the interface.
    """
    return UplinkCommandResponse(
        command_id=payload.command_id,
        balloon_id=payload.balloon_id,
        queued=True,
        message="Command queued for uplink. In production a worker delivers this via the satellite provider API.",
    )


@app.get("/health")
def health():
    return {"status": "ok"}
