from __future__ import annotations

import math
import time

from nimbusos_sdk import NimbusClient

TURN_DEG = 90.0
TURN_COUNT = 4
TURN_WAIT_S = 10.0


def main() -> None:
    with NimbusClient() as client:
        print("Publishing arm", flush=True)
        client.publish_arm_state(True)

        print("Waiting 10 seconds after arm", flush=True)
        time.sleep(10.0)

        print("Publishing takeoff", flush=True)
        client.publish_takeoff()

        print("Waiting 10 seconds after takeoff", flush=True)
        time.sleep(10.0)

        for turn_number in range(1, TURN_COUNT + 1):
            print(
                f"Publishing yaw turn {turn_number}: {TURN_DEG:.0f} degrees",
                flush=True,
            )
            client.publish_yaw_turn_command(math.radians(TURN_DEG))

            if turn_number < TURN_COUNT:
                print(
                    f"Waiting {TURN_WAIT_S:.0f} seconds before next yaw turn",
                    flush=True,
                )
                time.sleep(TURN_WAIT_S)

        print("Publishing land", flush=True)
        client.publish_autonomy_request("land")


if __name__ == "__main__":
    main()
