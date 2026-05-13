from __future__ import annotations

import time

from nimbusos_sdk import NimbusClient

TARGET_DOWN_M = -1.0
THRESHOLD_M = 0.15

WAYPOINTS = [
    ("override", "3 meters forward", 3.0, 0.0, 0.0),
    ("queue", "2 meters right", 0.0, 2.0, 20.0),
    ("queue", "2 meters right", 0.0, 2.0, 0.0),
    ("queue", "3 meters backward", -3.0, 0.0, 0.0),
]


def main() -> None:
    with NimbusClient() as client:
        print("Publishing arm", flush=True)
        client.publish_arm_state(True)

        print("Waiting 10 seconds after arm", flush=True)
        time.sleep(10.0)

        for index, waypoint in enumerate(WAYPOINTS, 1):
            mode, label, forward, right, hold_time_s = waypoint
            print(f"Publishing waypoint {index}: {label}", flush=True)
            client.publish_waypoint_command(
                mode=mode,
                forward=forward,
                right=right,
                down=TARGET_DOWN_M,
                threshold_m=THRESHOLD_M,
                hold_time_s=hold_time_s,
            )


if __name__ == "__main__":
    main()
