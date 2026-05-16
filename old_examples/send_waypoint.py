from __future__ import annotations

import argparse

from nimbusos_sdk import NimbusClient


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish a Nimbus waypoint command.")
    parser.add_argument("--mode", choices=["override", "queue"], default="override")
    parser.add_argument("--forward", type=float, required=True)
    parser.add_argument("--right", type=float, required=True)
    parser.add_argument("--down", type=float, required=True)
    parser.add_argument("--threshold", type=float, default=0.05)
    parser.add_argument("--hold-time", type=float, default=0.0)
    args = parser.parse_args()

    with NimbusClient() as client:
        client.publish_waypoint_command(
            mode=args.mode,
            forward=args.forward,
            right=args.right,
            down=args.down,
            threshold_m=args.threshold,
            hold_time_s=args.hold_time,
        )

    print(
        "published waypoint command:",
        f"mode={args.mode}",
        f"forward={args.forward}",
        f"right={args.right}",
        f"down={args.down}",
    )


if __name__ == "__main__":
    main()
