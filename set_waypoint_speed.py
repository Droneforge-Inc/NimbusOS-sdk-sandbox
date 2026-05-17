from __future__ import annotations

from nimbusos_sdk import NimbusClient

WAYPOINT_SPEED_MPS = 0.20


def main() -> None:
    with NimbusClient() as client:
        print(f"Publishing waypoint speed: {WAYPOINT_SPEED_MPS:.2f} m/s", flush=True)
        client.publish_waypoint_speed(WAYPOINT_SPEED_MPS)


if __name__ == "__main__":
    main()
