from decoder import decode_payload
from storage import get_packet, mark_packet_failed, save_observation


def process_packet(packet_id: str) -> None:
    """Decode a persisted packet and create a customer-visible observation."""
    packet = get_packet(packet_id)
    if packet is None:
        return

    try:
        decoded = decode_payload(packet["raw_payload"])
        # Some transmissions may produce zero observations. For demo purposes,
        # include a simple opt-out flag in fake payloads.
        if decoded.get("no_observation"):
            return
        save_observation(
            packet_id=packet["packet_id"],
            balloon_id=packet["balloon_id"],
            downlink_time=packet["downlink_time"],
            data=decoded,
        )
    except Exception as exc:
        mark_packet_failed(packet_id, str(exc))
