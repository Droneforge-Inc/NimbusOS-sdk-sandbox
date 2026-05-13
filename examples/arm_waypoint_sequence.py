from __future__ import annotations

import time

from nimbusos_sdk import NimbusClient

TARGET_DOWN_M = -1.0
THRESHOLD_M = 0.15


def main() -> None:
    with NimbusClient() as client:
        print("Publishing arm", flush=True)
        client.publish_arm_state(True)

        print("Waiting 10 seconds after arm", flush=True)
        time.sleep(10.0)

        print("Publishing go", flush=True)
        client.publish_guidance_request("go")

        print("Waiting 10 seconds after go", flush=True)
        time.sleep(10.0)

        print("Publishing waypoint 1: 3 meters forward", flush=True)
        client.publish_waypoint_command(
            mode="override",
            forward=3.0,
            right=0.0,
            down=TARGET_DOWN_M,
            threshold_m=THRESHOLD_M,
            hold_time_s=0.0,
        )

        print("Publishing waypoint 2: 2 meters right, hold 10 seconds", flush=True)
        client.publish_waypoint_command(
            mode="queue",
            forward=0.0,
            right=2.0,
            down=TARGET_DOWN_M,
            threshold_m=THRESHOLD_M,
            hold_time_s=10.0,
        )

        print("Publishing waypoint 3: 2 meters right", flush=True)
        client.publish_waypoint_command(
            mode="queue",
            forward=0.0,
            right=2.0,
            down=TARGET_DOWN_M,
            threshold_m=THRESHOLD_M,
            hold_time_s=0.0,
        )

        print("Publishing waypoint 4: 3 meters backward", flush=True)
        client.publish_waypoint_command(
            mode="queue",
            forward=-3.0,
            right=0.0,
            down=TARGET_DOWN_M,
            threshold_m=THRESHOLD_M,
            hold_time_s=0.0,
        )


if __name__ == "__main__":
    main()
