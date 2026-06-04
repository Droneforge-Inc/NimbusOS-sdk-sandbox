from __future__ import annotations

import os
import signal
import threading
import time
from typing import Any

import zmq

from nimbusos_sdk import NimbusClient

# NimbusClient import sets the packaged generated schema on sys.path.
from droneforge.schema.ArmMessage import ArmMessage

WAYPOINT_DOWN_M = 0.0
THRESHOLD_M = 0.25
BOX_FORWARD_M = 1.0
BOX_RIGHT_M = 1.0
BOX_WAYPOINT_COUNT = 4
PRE_BOX_STATUS_TIMEOUT_S = 2.0
BOX_SEQUENCE_TIMEOUT_S = 60.0
LAND_DISARM_OPTRANGE_M = 0.10
LAND_DISARM_TIMEOUT_S = 30.0
ARM_STATE_TOPIC = b"arm_state"
DEFAULT_SUB_ENDPOINT = "tcp://127.0.0.1:7772"
DISARM_MONITOR_POLL_MS = 100
DISARM_MONITOR_JOIN_TIMEOUT_S = 1.0


def active_waypoint_index(client: NimbusClient) -> int | None:
    for status in client.waypoint_status(
        timeout_sec=PRE_BOX_STATUS_TIMEOUT_S,
        receive_hwm=8,
    ):
        if status.active:
            return status.waypoint_index

    return None


def wait_for_box_complete(
    client: NimbusClient,
    previous_waypoint_index: int | None,
) -> None:
    final_waypoint_index = (
        None
        if previous_waypoint_index is None
        else previous_waypoint_index + BOX_WAYPOINT_COUNT
    )
    saw_final_waypoint = False

    for status in client.waypoint_status(timeout_sec=BOX_SEQUENCE_TIMEOUT_S):
        if not status.active:
            continue

        if final_waypoint_index is None:
            final_waypoint_index = status.waypoint_index + BOX_WAYPOINT_COUNT - 1

        if status.waypoint_index < final_waypoint_index:
            continue
        if status.waypoint_index > final_waypoint_index:
            raise RuntimeError(
                f"Skipped final rectangle waypoint: "
                f"expected index {final_waypoint_index}, "
                f"saw index {status.waypoint_index}"
            )

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


def start_disarm_monitor(
    stop_monitor: threading.Event,
    app_disarm_received: threading.Event,
) -> threading.Thread:
    thread = threading.Thread(
        target=monitor_app_disarm,
        args=(stop_monitor, app_disarm_received),
        daemon=True,
    )
    thread.start()
    return thread


def monitor_app_disarm(
    stop_monitor: threading.Event,
    app_disarm_received: threading.Event,
) -> None:
    context = zmq.Context.instance()
    socket = context.socket(zmq.SUB)
    socket.setsockopt(zmq.LINGER, 0)
    socket.setsockopt(zmq.RCVHWM, 8)
    socket.setsockopt(zmq.SUBSCRIBE, ARM_STATE_TOPIC)
    socket.connect(os.environ.get("DF_ZMQ_SUB_ENDPOINT", DEFAULT_SUB_ENDPOINT))

    try:
        while not stop_monitor.is_set():
            if socket.poll(timeout=DISARM_MONITOR_POLL_MS) == 0:
                continue
            if stop_monitor.is_set():
                return

            topic_frame, payload_frame = socket.recv_multipart()
            if topic_frame != ARM_STATE_TOPIC:
                continue

            arm_message = ArmMessage.GetRootAs(payload_frame, 0)
            if arm_message.Armed():
                continue
            if stop_monitor.is_set():
                return

            print("App published disarm; stopping script", flush=True)
            app_disarm_received.set()
            os.kill(os.getpid(), signal.SIGINT)
            return
    except Exception as exc:
        print(f"Disarm monitor failed: {exc!r}", flush=True)
        os.kill(os.getpid(), signal.SIGINT)
        raise
    finally:
        socket.close()


def publish_disarm(client: NimbusClient, stop_monitor: threading.Event) -> None:
    stop_monitor.set()
    print("Publishing disarm", flush=True)
    client.publish_arm_state(False)


def land_and_disarm(client: NimbusClient, stop_monitor: threading.Event) -> None:
    print("Publishing land", flush=True)
    client.publish_autonomy_request("land")

    print(
        f"Waiting for optrange <= {LAND_DISARM_OPTRANGE_M:.2f} m before disarm",
        flush=True,
    )
    wait_for_landing_optrange(client)
    publish_disarm(client, stop_monitor)


def main() -> None:
    stop_monitor = threading.Event()
    app_disarm_received = threading.Event()

    with NimbusClient() as client:
        disarm_monitor: threading.Thread | None = None

        try:
            disarm_monitor = start_disarm_monitor(stop_monitor, app_disarm_received)

            print("Publishing arm", flush=True)
            client.publish_arm_state(True)

            print("Waiting 10 seconds after arm", flush=True)
            time.sleep(10.0)

            print("Publishing takeoff", flush=True)
            client.publish_autonomy_request("takeoff")

            print("Waiting 10 seconds after takeoff", flush=True)
            time.sleep(10.0)

            previous_waypoint_index = active_waypoint_index(client)

            print(
                f"Publishing waypoint 1: {BOX_FORWARD_M:.1f} meters forward",
                flush=True,
            )
            client.publish_relative_waypoint(
                mode="override",
                forward=BOX_FORWARD_M,
                right=0.0,
                down=WAYPOINT_DOWN_M,
                threshold_m=THRESHOLD_M,
                hold_time_s=0.0,
            )

            print(f"Publishing waypoint 2: {BOX_RIGHT_M:.1f} meters right", flush=True)
            client.publish_relative_waypoint(
                mode="queue",
                forward=0.0,
                right=BOX_RIGHT_M,
                down=WAYPOINT_DOWN_M,
                threshold_m=THRESHOLD_M,
                hold_time_s=0.0,
            )

            print(
                f"Publishing waypoint 3: {BOX_FORWARD_M:.1f} meters backward",
                flush=True,
            )
            client.publish_relative_waypoint(
                mode="queue",
                forward=-BOX_FORWARD_M,
                right=0.0,
                down=WAYPOINT_DOWN_M,
                threshold_m=THRESHOLD_M,
                hold_time_s=0.0,
            )

            print(f"Publishing waypoint 4: {BOX_RIGHT_M:.1f} meters left", flush=True)
            client.publish_relative_waypoint(
                mode="queue",
                forward=0.0,
                right=-BOX_RIGHT_M,
                down=WAYPOINT_DOWN_M,
                threshold_m=THRESHOLD_M,
                hold_time_s=0.0,
            )

            print("Waiting for the final rectangle waypoint", flush=True)
            wait_for_box_complete(client, previous_waypoint_index)

            land_and_disarm(client, stop_monitor)
        except KeyboardInterrupt:
            if app_disarm_received.is_set():
                print("Stopped after app disarm", flush=True)
                return

            print("Keyboard interrupt; publishing disarm before exit", flush=True)
            publish_disarm(client, stop_monitor)
        except Exception as exc:
            print(f"Mission failed: {exc!r}; landing before exit", flush=True)
            land_and_disarm(client, stop_monitor)
            raise
        finally:
            stop_monitor.set()
            if disarm_monitor is not None:
                disarm_monitor.join(timeout=DISARM_MONITOR_JOIN_TIMEOUT_S)


if __name__ == "__main__":
    main()
