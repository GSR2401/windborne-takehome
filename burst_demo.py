"""
Burst demo: fires N packets at POST /downlink_packet with bounded concurrency,
then shows before/after /metrics so you can see the jump in Swagger.

Usage:
    python3.11 burst_demo.py              # 100 packets, 50 concurrent
    python3.11 burst_demo.py 1000         # 1000 packets, 50 concurrent
    python3.11 burst_demo.py 1000 100     # 1000 packets, 100 concurrent
"""

import sys
import time
import json
import random
import threading
from urllib.request import urlopen, Request
from urllib.error import URLError

from fake_packets import make_fake_packet

BASE_URL = "http://127.0.0.1:8000"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 100
CONCURRENCY = int(sys.argv[2]) if len(sys.argv) > 2 else 50


def get_metrics() -> dict:
    with urlopen(f"{BASE_URL}/metrics") as r:
        return json.loads(r.read())


def post_packet(packet: dict, results: list, idx: int, semaphore: threading.Semaphore) -> None:
    body = json.dumps(packet).encode()
    req = Request(
        f"{BASE_URL}/downlink_packet",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with semaphore:
        try:
            with urlopen(req, timeout=10) as r:
                results[idx] = r.status  # 202 on success
        except URLError as e:
            results[idx] = str(e)


def main() -> None:
    try:
        get_metrics()
    except URLError:
        print("ERROR: server is not running. Start it with:")
        print("  python3.11 -m uvicorn app:app --reload")
        sys.exit(1)

    print(f"\n=== Burst Demo: {N} packets, max {CONCURRENCY} concurrent ===\n")

    before = get_metrics()
    print("BEFORE burst:")
    print(f"  packets_received    : {before['packets_received']}")
    print(f"  packets_processed   : {before['packets_processed']}")
    print(f"  observations_created: {before['observations_created']}")
    print(f"  total_revenue       : {before['total_revenue']}")

    packets = [make_fake_packet(random.randint(1, 50)) for _ in range(N)]
    results = [None] * N
    semaphore = threading.Semaphore(CONCURRENCY)
    threads = [
        threading.Thread(target=post_packet, args=(p, results, i, semaphore))
        for i, p in enumerate(packets)
    ]

    print(f"\nFiring {N} packets (max {CONCURRENCY} in-flight at once)...")
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.perf_counter() - t0

    ok = sum(1 for r in results if r == 202)
    fail = N - ok
    print(f"Done in {elapsed:.2f}s  |  ACKed: {ok}  |  Failed: {fail}")

    print("\nWaiting 2s for background processing...")
    time.sleep(2)

    after = get_metrics()
    print("\nAFTER burst:")
    print(f"  packets_received    : {after['packets_received']}  (+{after['packets_received'] - before['packets_received']})")
    print(f"  packets_processed   : {after['packets_processed']}  (+{after['packets_processed'] - before['packets_processed']})")
    print(f"  observations_created: {after['observations_created']}  (+{after['observations_created'] - before['observations_created']})")
    print(f"  total_revenue       : {after['total_revenue']}  (+{after['total_revenue'] - before['total_revenue']})")

    print(f"\nThroughput: {ok / elapsed:.1f} packets/sec (ACK rate)")
    print("\nRefresh GET /metrics in Swagger to see the same numbers live.")


if __name__ == "__main__":
    main()
