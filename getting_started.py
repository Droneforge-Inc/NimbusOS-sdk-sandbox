from __future__ import annotations

import time
from typing import Any

from nimbusos_sdk import NimbusClient

TARGET_DOWN_M = -1.0
THRESHOLD_M = 0.25
BOX_FORWARD_M = 1.0
BOX_RIGHT_M = 1.0
BOX_WAYPOINT_COUNT = 4
BOX_SEQUENCE_TIMEOUT_S = 60.0
LAND_DISARM_OPTRANGE_M = 0.10
LAND_DISARM_TIMEOUT_S = 30.0


def wait_for_box_complete(client: NimbusClient) -> None:
    final_command_seq = BOX_WAYPOINT_COUNT - 1
    saw_final_waypoint = False

    for status in client.waypoint_status(timeout_sec=BOX_SEQUENCE_TIMEOUT_S):
        if not status.active or status.command_seq != final_command_seq:
            continue

        saw_final_waypoint = True
        if status.held:
            print(
                "Rectangle complete: "
                f"waypoint_index={status.waypoint_index} "
                f"distance={status.distance_m:.2f}m",
                flush=True,
            )
            return

    if saw_final_waypoint:
        raise TimeoutError(
            f"Final rectangle waypoint did not complete within "
            f"{BOX_SEQUENCE_TIMEOUT_S:.0f}s"
        )
    raise TimeoutError("Final rectangle waypoint never became active")


def wait_for_landing_optrange(client: NimbusClient) -> float:
    for message in client.subscribe_telemetry(
        timeout_sec=LAND_DISARM_TIMEOUT_S,
        receive_hwm=8,
    ):
        distance_m = optrange_distance_m(message)
        if 0.0 < distance_m <= LAND_DISARM_OPTRANGE_M:
            print(f"Landing optrange reached: {distance_m:.2f} m", flush=True)
            return distance_m

    raise TimeoutError(
        f"Optrange did not reach {LAND_DISARM_OPTRANGE_M:.2f} m within "
        f"{LAND_DISARM_TIMEOUT_S:.0f}s"
    )


def optrange_distance_m(message: Any) -> float:
    optrange = message.decoded.Optrange()
    if optrange is None:
        raise RuntimeError("Telemetry message missing optrange")
    return optrange.Distance()


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

        print(f"Publishing waypoint 1: {BOX_FORWARD_M:.1f} meters forward", flush=True)
        client.publish_waypoint_command(
            mode="override",
            forward=BOX_FORWARD_M,
            right=0.0,
            down=TARGET_DOWN_M,
            threshold_m=THRESHOLD_M,
            hold_time_s=0.0,
        )

        print(f"Publishing waypoint 2: {BOX_RIGHT_M:.1f} meters right", flush=True)
        client.publish_waypoint_command(
            mode="queue",
            forward=0.0,
            right=BOX_RIGHT_M,
            down=TARGET_DOWN_M,
            threshold_m=THRESHOLD_M,
            hold_time_s=0.0,
        )

        print(f"Publishing waypoint 3: {BOX_FORWARD_M:.1f} meters backward", flush=True)
        client.publish_waypoint_command(
            mode="queue",
            forward=-BOX_FORWARD_M,
            right=0.0,
            down=TARGET_DOWN_M,
            threshold_m=THRESHOLD_M,
            hold_time_s=0.0,
        )

        print(f"Publishing waypoint 4: {BOX_RIGHT_M:.1f} meters left", flush=True)
        client.publish_waypoint_command(
            mode="queue",
            forward=0.0,
            right=-BOX_RIGHT_M,
            down=TARGET_DOWN_M,
            threshold_m=THRESHOLD_M,
            hold_time_s=0.0,
        )

        print("Waiting for the final rectangle waypoint", flush=True)
        wait_for_box_complete(client)

        print("Publishing land", flush=True)
        client.publish_guidance_request("land")

        print(
            f"Waiting for optrange <= {LAND_DISARM_OPTRANGE_M:.2f} m before disarm",
            flush=True,
        )
        wait_for_landing_optrange(client)

        print("Publishing disarm", flush=True)
        client.publish_arm_state(False)


if __name__ == "__main__":
    main()
