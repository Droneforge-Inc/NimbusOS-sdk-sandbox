from __future__ import annotations

import argparse

from nimbusos_sdk import NimbusClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Subscribe to a Nimbus SDK topic.")
    parser.add_argument("topic", choices=["telemetry", "state", "camera"])
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    with NimbusClient() as client:
        count = 0
        for message in subscribe(client, args.topic, timeout_sec=args.timeout):
            print(
                message.topic,
                f"bytes={len(message.payload)}",
                f"type={message.root_type}",
                f"seq={message.seq}",
                f"t_ns={message.t_ns}",
            )
            count += 1
            if args.limit and count >= args.limit:
                return


def subscribe(client: NimbusClient, topic: str, *, timeout_sec: float):
    if topic == "telemetry":
        return client.subscribe_telemetry(timeout_sec=timeout_sec)
    if topic == "state":
        return client.subscribe_state(timeout_sec=timeout_sec)
    if topic == "camera":
        return client.subscribe_camera(timeout_sec=timeout_sec)
    raise ValueError(f"unsupported topic: {topic}")


if __name__ == "__main__":
    main()
