from __future__ import annotations

import argparse

from nimbusos_sdk import NimbusClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish a Nimbus guidance request.")
    parser.add_argument("type", choices=["go", "land", "relative_waypoint", "return_home"])
    parser.add_argument("--forward", type=float, default=0.0)
    parser.add_argument("--right", type=float, default=0.0)
    parser.add_argument("--down", type=float, default=0.0)
    parser.add_argument("--hold-time", type=float, default=0.0)
    args = parser.parse_args()

    with NimbusClient() as client:
        client.publish_guidance_request(
            args.type,
            forward=args.forward,
            right=args.right,
            down=args.down,
            hold_time_s=args.hold_time,
        )

    print("published guidance request:", args.type)


if __name__ == "__main__":
    main()
