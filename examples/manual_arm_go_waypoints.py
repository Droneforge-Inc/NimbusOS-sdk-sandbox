from __future__ import annotations

import time

from nimbusos_sdk import NimbusClient


def main() -> None:
    with NimbusClient() as client:
        print("Waiting 30 seconds. Click Arm in Desktop now.", flush=True)
        time.sleep(30.0)

        print("Publishing go", flush=True)
        client.publish_guidance_request("go")

        time.sleep(5.0)

        print("Publishing waypoint 1", flush=True)
        client.publish_waypoint_command(
            mode="override",
            forward=0.75,
            right=0.0,
            down=-1.0,
            threshold_m=0.15,
            hold_time_s=1.0,
        )

        time.sleep(8.0)

        print("Publishing waypoint 2", flush=True)
        client.publish_waypoint_command(
            mode="override",
            forward=0.0,
            right=0.0,
            down=-1.0,
            threshold_m=0.15,
            hold_time_s=1.0,
        )


if __name__ == "__main__":
    main()
