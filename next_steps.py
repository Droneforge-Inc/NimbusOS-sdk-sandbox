from __future__ import annotations

import math
import time
from typing import Any

from nimbusos_sdk import NimbusClient

FORWARD_STEP_M = 1.0
THRESHOLD_M = 0.15
HOLD_TIME_S = 0.5
WAYPOINT_TIMEOUT_S = 30.0
TURN_DEG = 90.0
TURN_SETTLE_S = 3.0
LEG_COUNT = 4
LAND_DISARM_OPTRANGE_M = 0.10
LAND_DISARM_TIMEOUT_S = 30.0


def wait_for_waypoint_complete(client: NimbusClient, label: str) -> None:
    saw_active = False

    for status in client.waypoint_status(timeout_sec=WAYPOINT_TIMEOUT_S):
        if not status.active:
            continue

        saw_active = True
        if status.held:
            print(
                f"{label} complete: "
                f"waypoint_index={status.waypoint_index} "
                f"distance={status.distance_m:.2f}m",
                flush=True,
            )
            return

    if saw_active:
        raise TimeoutError(f"{label} did not complete within {WAYPOINT_TIMEOUT_S:.0f}s")
    raise TimeoutError(f"{label} never became active")


def publish_forward_waypoint(client: NimbusClient, label: str) -> None:
    print(f"Publishing {label}: {FORWARD_STEP_M:.1f} meter forward", flush=True)
    client.publish_relative_waypoint(
        mode="override",
        forward=FORWARD_STEP_M,
        right=0.0,
        down=0.0,
        threshold_m=THRESHOLD_M,
        hold_time_s=HOLD_TIME_S,
    )


def publish_yaw_turn(client: NimbusClient, label: str) -> None:
    print(f"Publishing {label}: yaw {TURN_DEG:.0f} degrees", flush=True)
    client.publish_yaw_turn_command(math.radians(TURN_DEG))
    print(f"Waiting {TURN_SETTLE_S:.0f} seconds after yaw", flush=True)
    time.sleep(TURN_SETTLE_S)


def land_and_disarm(client: NimbusClient, reason: str) -> None:
    print(f"{reason}; publishing land", flush=True)
    client.publish_autonomy_request("land")

    print(
        f"Waiting for optrange <= {LAND_DISARM_OPTRANGE_M:.2f} m before disarm",
        flush=True,
    )
    wait_for_landing_optrange(client)

    print("Publishing disarm", flush=True)
    client.publish_arm_state(False)


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

        print("Publishing takeoff", flush=True)
        client.publish_takeoff()

        print("Waiting 10 seconds after takeoff", flush=True)
        time.sleep(10.0)

        for leg_number in range(1, LEG_COUNT + 1):
            label = f"leg {leg_number}"
            publish_forward_waypoint(client, label)
            try:
                wait_for_waypoint_complete(client, label)
            except TimeoutError:
                land_and_disarm(client, f"{label} did not complete")
                raise
            publish_yaw_turn(client, label)

        print("Publishing land", flush=True)
        client.publish_autonomy_request("land")


if __name__ == "__main__":
    main()
