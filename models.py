from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field


class PacketStatus(str, Enum):
    RECEIVED = "RECEIVED"
    PROCESSED = "PROCESSED"
    DECODE_FAILED = "DECODE_FAILED"


class DownlinkPacketRequest(BaseModel):
    packet_id: str = Field(..., examples=["pkt-B001-20260620T120000Z"])
    balloon_id: str = Field(..., examples=["B001"])
    downlink_time: str = Field(..., description="Original balloon downlink time in ISO-8601 UTC")
    encoded_payload: str = Field(..., description="Base64 encoded JSON telemetry payload")


class DownlinkPacketResponse(BaseModel):
    packet_id: str
    status: PacketStatus
    acked: bool
    message: str


class Observation(BaseModel):
    observation_id: str
    packet_id: str
    balloon_id: str
    downlink_time: str
    customer_visible_time: str
    latency_seconds: float
    earned_revenue: int
    data: dict[str, Any]


class UplinkCommandRequest(BaseModel):
    command_id: str = Field(..., examples=["cmd-B001-reboot-001"])
    balloon_id: str = Field(..., examples=["B001"])
    command_type: str = Field(..., examples=["reboot", "altitude_change", "ping"])
    parameters: dict[str, Any] = Field(default_factory=dict)


class UplinkCommandResponse(BaseModel):
    command_id: str
    balloon_id: str
    queued: bool
    message: str


class PacketState(BaseModel):
    packet_id: str
    balloon_id: str
    downlink_time: str
    status: PacketStatus
    received_at: str
    processed_at: Optional[str] = None
    error: Optional[str] = None
