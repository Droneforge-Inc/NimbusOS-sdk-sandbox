from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import argparse
import statistics
import threading
import time
from typing import Any

import zmq

from nimbusos_sdk import NimbusClient
from nimbusos_sdk.schema import load_message_class


HAND_MODEL = "openai/clip-vit-base-patch32"
HAND_LABELS = [
    "an open hand showing five fingers",
    "a closed fist",
    "a hand showing fewer than five fingers",
    "no hand visible",
]
OPEN_HAND_LABEL = HAND_LABELS[0]

DEFAULT_HAND_CONFIDENCE = 0.55
DEFAULT_CONSECUTIVE_HAND_FRAMES = 3
DEFAULT_CAMERA_CHECK_PERIOD_S = 0.05
INFERENCE_IMAGE_MAX_SIZE = 224

DEFAULT_RANGE_BASELINE_SAMPLES = 8
DEFAULT_RANGE_DELTA_M = 0.35
DEFAULT_RANGE_RATIO = 0.25
DEFAULT_RANGE_HOLD_TIME_S = 0.35
DEFAULT_CATCH_RANGE_INCHES = 5.0
INCHES_TO_METERS = 0.0254

DESKTOP_START_WAIT_S = 10.0
READY_HOVER_DOWN_M = -1.5
READY_HOVER_THRESHOLD_M = 0.15
READY_HOVER_HOLD_TIME_S = 999.0
STATE_SAMPLE_TIMEOUT_S = 2.0
CAMERA_READER_RECEIVE_HWM = 1
CAMERA_DISPLAY_PERIOD_S = 1.0 / 60.0
MAX_CAMERA_DRAIN_PER_TICK = 1000

HOLD_WAYPOINT_THRESHOLD_M = 0.12


@dataclass(frozen=True)
class CatchMeConfig:
    hand_confidence: float
    consecutive_hand_frames: int
    camera_check_period_s: float
    range_baseline_samples: int
    range_delta_m: float
    range_ratio: float
    range_hold_time_s: float
    catch_range_m: float
    dry_run: bool
    display: bool
    publish_go: bool
    publish_ready_hover: bool
    inference_device: str


@dataclass
class InferenceSnapshot:
    label: str = "waiting for inference"
    score: float = 0.0
    consecutive_hits: int = 0
    frame_seq: int | None = None


@dataclass
class LatestCameraImage:
    image: Any | None = None
    frame_seq: int | None = None
    frame_t_ns: int | None = None


@dataclass
class LatestCameraFrame:
    payload: bytes | None = None
    received_monotonic_ns: int | None = None
    update_id: int = 0


def build_hand_classifier(config: CatchMeConfig) -> Any:
    try:
        from PIL import Image
        import torch
        from transformers import pipeline
    except ImportError as exc:
        raise RuntimeError(
            "This example needs Hugging Face transformers and Pillow. "
            "Install them with torch support before running, for example: "
            "uv add transformers pillow torch"
        ) from exc

    device_arg, device_name, use_fp16 = resolve_torch_device(torch, config)
    print(f"Loading hand model: {HAND_MODEL} on {device_name}", flush=True)
    pipeline_args = dict(
        task="zero-shot-image-classification",
        model=HAND_MODEL,
        device=device_arg,
    )

    if use_fp16:
        torch.backends.cudnn.benchmark = True
        try:
            classifier = pipeline(**pipeline_args, dtype=torch.float16)
        except TypeError:
            try:
                classifier = pipeline(**pipeline_args, torch_dtype=torch.float16)
            except TypeError:
                classifier = pipeline(**pipeline_args)
    else:
        classifier = pipeline(**pipeline_args)

    print("Hand model loaded.", flush=True)
    return Image, classifier


def resolve_torch_device(torch: Any, config: CatchMeConfig) -> tuple[int, str, bool]:
    if config.inference_device == "cpu":
        return -1, "CPU", False

    cuda_available = torch.cuda.is_available()
    if config.inference_device == "cuda" and not cuda_available:
        raise RuntimeError(
            "CUDA was requested with --inference-device cuda, but "
            "torch.cuda.is_available() is false in this environment."
        )

    if cuda_available:
        return 0, f"CUDA: {torch.cuda.get_device_name(0)}", True

    return -1, "CPU (CUDA unavailable)", False


def wait_for_desktop_start_and_hover(
    client: NimbusClient,
    config: CatchMeConfig,
) -> None:
    print(
        "Waiting 10 seconds. Press Start in Desktop now.",
        flush=True,
    )
    time.sleep(DESKTOP_START_WAIT_S)

    print("Publishing arm request.", flush=True)
    if not config.dry_run:
        client.publish_arm_state(True)

    if config.publish_go:
        print("Publishing go", flush=True)
    if config.publish_go and not config.dry_run:
        client.publish_guidance_request("go")

    if not config.publish_ready_hover:
        print(
            "Ready hover command disabled. Holding via Desktop/NimbusOS control.",
            flush=True,
        )
        return

    state = latest_state(client, timeout_sec=STATE_SAMPLE_TIMEOUT_S)
    if state is None:
        print(
            "No state sample available; publishing vertical-only relative hover "
            "request instead of an absolute horizontal waypoint.",
            flush=True,
        )
        if not config.dry_run:
            client.publish_guidance_request(
                "relative_waypoint",
                forward=0.0,
                right=0.0,
                down=READY_HOVER_DOWN_M,
                hold_time_s=READY_HOVER_HOLD_TIME_S,
            )
        return

    forward_m = state.position.x_m
    right_m = state.position.y_m

    print(
        "Publishing ready hover waypoint",
        f"forward={forward_m:.2f}m",
        f"right={right_m:.2f}m",
        f"down={READY_HOVER_DOWN_M:.2f}m",
        flush=True,
    )
    if config.dry_run:
        return

    client.publish_waypoint_command(
        mode="override",
        forward=forward_m,
        right=right_m,
        down=READY_HOVER_DOWN_M,
        threshold_m=READY_HOVER_THRESHOLD_M,
        hold_time_s=READY_HOVER_HOLD_TIME_S,
    )


def latest_state(client: NimbusClient, *, timeout_sec: float) -> Any | None:
    state = None
    for sampled_state in client.state(timeout_sec=timeout_sec):
        state = sampled_state
    return state


def build_preview_modules() -> Any:
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError(
            "Camera preview needs OpenCV. Install dependencies with: uv sync"
        ) from exc

    cv2.namedWindow("Nimbus catch me camera", cv2.WINDOW_NORMAL)
    return cv2


def build_cv2_module() -> Any:
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("Camera decode needs OpenCV. Install dependencies with: uv sync") from exc

    return cv2


def show_preview(
    cv2: Any,
    frame_bgr: Any,
    *,
    label: str,
    score: float,
    consecutive_hits: int,
    config: CatchMeConfig,
) -> None:
    status = (
        "MATCH"
        if label == OPEN_HAND_LABEL and score >= config.hand_confidence
        else "SCAN"
    )
    color = (0, 220, 0) if status == "MATCH" else (0, 180, 255)
    lines = [
        f"{status}: {label}",
        f"confidence={score:.3f} threshold={config.hand_confidence:.3f}",
        f"hits={consecutive_hits}/{config.consecutive_hand_frames}",
        "press q or Esc to stop",
    ]

    for index, line in enumerate(lines):
        y = 28 + (index * 28)
        cv2.putText(
            frame_bgr,
            line,
            (12, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 0, 0),
            4,
            cv2.LINE_AA,
        )
        cv2.putText(
            frame_bgr,
            line,
            (12, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            color,
            2,
            cv2.LINE_AA,
        )

    cv2.imshow("Nimbus catch me camera", frame_bgr)
    key = cv2.waitKey(1) & 0xFF
    if key in (ord("q"), 27):
        raise KeyboardInterrupt("camera preview stopped")


def wait_for_five_fingers(client: NimbusClient, config: CatchMeConfig) -> None:
    cv2 = build_preview_modules() if config.display else build_cv2_module()
    camera_message_class = load_message_class("CameraJpegMessage")

    latest_frame = LatestCameraFrame()
    latest = LatestCameraImage()
    snapshot = InferenceSnapshot()
    latest_frame_lock = threading.Lock()
    latest_lock = threading.Lock()
    snapshot_lock = threading.Lock()
    stop_event = threading.Event()
    detected_event = threading.Event()
    camera_reader = threading.Thread(
        target=raw_camera_frame_reader,
        args=(client.sub_endpoint, latest_frame, latest_frame_lock, stop_event),
        daemon=True,
    )
    worker = threading.Thread(
        target=inference_worker,
        args=(
            latest,
            snapshot,
            latest_lock,
            snapshot_lock,
            stop_event,
            detected_event,
            config,
        ),
        daemon=True,
    )
    camera_reader.start()
    worker.start()

    print("Watching camera feed for an open hand with five fingers.", flush=True)
    try:
        displayed_update: int | None = None
        while not stop_event.is_set():
            with latest_frame_lock:
                payload = latest_frame.payload
                update_id = latest_frame.update_id

            if payload is None or update_id == displayed_update:
                if detected_event.is_set():
                    print("Open hand detected. Entering catch me mode.", flush=True)
                    return
                stop_event.wait(0.005)
                continue

            displayed_update = update_id
            decoded = camera_message_class.GetRootAs(payload, 0)
            frame_seq = decoded.Seq()
            frame_t_ns = decoded.TNs()
            frame_bgr = cv2.imdecode(decoded.JpegAsNumpy(), cv2.IMREAD_COLOR)
            if frame_bgr is None:
                continue

            with latest_lock:
                latest.image = frame_bgr.copy()
                latest.frame_seq = frame_seq
                latest.frame_t_ns = frame_t_ns

            if config.display:
                with snapshot_lock:
                    label = snapshot.label
                    score = snapshot.score
                    consecutive_hits = snapshot.consecutive_hits
                show_preview(
                    cv2,
                    frame_bgr,
                    label=label,
                    score=score,
                    consecutive_hits=consecutive_hits,
                    config=config,
                )

            if detected_event.is_set():
                print("Open hand detected. Entering catch me mode.", flush=True)
                if config.display:
                    with snapshot_lock:
                        label = snapshot.label
                        score = snapshot.score
                        consecutive_hits = snapshot.consecutive_hits
                    show_preview(
                        cv2,
                        frame_bgr,
                        label=label,
                        score=score,
                        consecutive_hits=consecutive_hits,
                        config=config,
                    )
                    time.sleep(0.5)
                return

            stop_event.wait(CAMERA_DISPLAY_PERIOD_S)
    finally:
        stop_event.set()
        camera_reader.join(timeout=1.0)
        worker.join(timeout=1.0)
        if cv2 is not None:
            cv2.destroyAllWindows()


def raw_camera_frame_reader(
    sub_endpoint: str,
    latest_frame: LatestCameraFrame,
    latest_frame_lock: threading.Lock,
    stop_event: threading.Event,
) -> None:
    context = zmq.Context.instance()
    socket = context.socket(zmq.SUB)
    socket.setsockopt(zmq.LINGER, 0)
    socket.setsockopt(zmq.RCVHWM, CAMERA_READER_RECEIVE_HWM)
    socket.setsockopt(zmq.SUBSCRIBE, b"camera")
    socket.connect(sub_endpoint)

    try:
        while not stop_event.is_set():
            if socket.poll(timeout=100) == 0:
                continue

            latest_payload_frame = None
            latest_received_ns = None
            drained = 0

            while drained < MAX_CAMERA_DRAIN_PER_TICK:
                try:
                    topic_frame, payload_frame = socket.recv_multipart(
                        flags=zmq.NOBLOCK,
                        copy=False,
                    )
                except zmq.Again:
                    break

                if topic_frame.bytes != b"camera":
                    continue

                latest_payload_frame = payload_frame
                latest_received_ns = time.monotonic_ns()
                drained += 1

            if latest_payload_frame is None:
                continue

            with latest_frame_lock:
                latest_frame.payload = latest_payload_frame.bytes
                latest_frame.received_monotonic_ns = latest_received_ns
                latest_frame.update_id += 1
    finally:
        socket.close()


def inference_worker(
    latest: LatestCameraImage,
    snapshot: InferenceSnapshot,
    latest_lock: threading.Lock,
    snapshot_lock: threading.Lock,
    stop_event: threading.Event,
    detected_event: threading.Event,
    config: CatchMeConfig,
) -> None:
    image_module, classifier = build_hand_classifier(config)
    processed_seq: int | None = None

    while not stop_event.is_set() and not detected_event.is_set():
        started_s = time.monotonic()
        with latest_lock:
            frame_bgr = latest.image.copy() if latest.image is not None else None
            frame_seq = latest.frame_seq

        if frame_bgr is None or frame_seq == processed_seq:
            stop_event.wait(0.02)
            continue

        processed_seq = frame_seq
        image = image_module.fromarray(frame_bgr[:, :, ::-1].copy())
        image.thumbnail((INFERENCE_IMAGE_MAX_SIZE, INFERENCE_IMAGE_MAX_SIZE))
        predictions = classifier(image, candidate_labels=HAND_LABELS)
        best = predictions[0]
        label = best["label"]
        score = float(best["score"])

        with snapshot_lock:
            if label == OPEN_HAND_LABEL and score >= config.hand_confidence:
                snapshot.consecutive_hits += 1
            else:
                snapshot.consecutive_hits = 0

            snapshot.label = label
            snapshot.score = score
            snapshot.frame_seq = frame_seq
            consecutive_hits = snapshot.consecutive_hits

        print(
            f"camera seq={frame_seq} label={label!r} score={score:.3f}",
            flush=True,
        )
        if consecutive_hits >= config.consecutive_hand_frames:
            detected_event.set()
            return

        elapsed_s = time.monotonic() - started_s
        wait_s = max(0.0, config.camera_check_period_s - elapsed_s)
        stop_event.wait(wait_s)


def hold_current_position(client: NimbusClient, config: CatchMeConfig) -> None:
    state = None
    for sampled_state in client.state(timeout_sec=1.0):
        state = sampled_state

    if state is None:
        print("No state sample available; entering catch mode without hold waypoint.", flush=True)
        return

    forward_m = state.position.x_m
    right_m = state.position.y_m
    down_m = state.position.z_m
    print(
        "Publishing hold waypoint",
        f"forward={forward_m:.2f}m",
        f"right={right_m:.2f}m",
        f"down={down_m:.2f}m",
        flush=True,
    )

    if config.dry_run:
        return

    client.publish_waypoint_command(
        mode="override",
        forward=forward_m,
        right=right_m,
        down=down_m,
        threshold_m=HOLD_WAYPOINT_THRESHOLD_M,
        hold_time_s=0.0,
    )


def raw_range_distance_m(decoded_telemetry: Any) -> float | None:
    rangefinder = decoded_telemetry.Rangefinder()
    if rangefinder is not None:
        return _positive_distance_or_none(rangefinder.Distance())

    optrange = decoded_telemetry.Optrange()
    if optrange is not None:
        return _positive_distance_or_none(optrange.Distance())

    return None


def _positive_distance_or_none(distance_m: float) -> float | None:
    if distance_m <= 0.0:
        return None
    return float(distance_m)


def wait_for_range_change(client: NimbusClient, config: CatchMeConfig) -> None:
    baseline_samples: deque[float] = deque(maxlen=config.range_baseline_samples)
    triggered_since_s: float | None = None

    print(
        "Monitoring rangefinder for catch/disarm",
        f"catch_range={config.catch_range_m:.3f}m",
        flush=True,
    )
    for message in client.subscribe_telemetry():
        distance_m = raw_range_distance_m(message.decoded)
        if distance_m is None:
            continue

        if len(baseline_samples) < config.range_baseline_samples:
            baseline_samples.append(distance_m)
            print(
                "range baseline",
                f"{len(baseline_samples)}/{config.range_baseline_samples}",
                f"distance={distance_m:.2f}m",
                flush=True,
            )
            continue

        baseline_m = statistics.median(baseline_samples)
        delta_m = abs(distance_m - baseline_m)
        ratio = delta_m / max(baseline_m, 0.01)
        changed = delta_m >= config.range_delta_m or ratio >= config.range_ratio
        within_catch_range = distance_m <= config.catch_range_m

        print(
            "range",
            f"distance={distance_m:.2f}m",
            f"baseline={baseline_m:.2f}m",
            f"delta={delta_m:.2f}m",
            f"ratio={ratio:.2f}",
            f"catch={within_catch_range}",
            flush=True,
        )

        now_s = time.monotonic()
        if changed and within_catch_range:
            if triggered_since_s is None:
                triggered_since_s = now_s
            elif now_s - triggered_since_s >= config.range_hold_time_s:
                print("Catch range confirmed.", flush=True)
                return
        else:
            triggered_since_s = None
            baseline_samples.append(distance_m)


def disarm_after_catch(client: NimbusClient, config: CatchMeConfig) -> None:
    print("Publishing disarm request.", flush=True)
    if config.dry_run:
        return

    client.publish_arm_state(False)
    print("Disarm request sent.", flush=True)


def parse_args() -> CatchMeConfig:
    parser = argparse.ArgumentParser(
        description="Detect a five-finger hand gesture, hold, then disarm on range change."
    )
    parser.add_argument("--hand-confidence", type=float, default=DEFAULT_HAND_CONFIDENCE)
    parser.add_argument(
        "--consecutive-hand-frames",
        type=int,
        default=DEFAULT_CONSECUTIVE_HAND_FRAMES,
    )
    parser.add_argument(
        "--camera-check-period",
        type=float,
        default=DEFAULT_CAMERA_CHECK_PERIOD_S,
    )
    parser.add_argument(
        "--range-baseline-samples",
        type=int,
        default=DEFAULT_RANGE_BASELINE_SAMPLES,
    )
    parser.add_argument("--range-delta", type=float, default=DEFAULT_RANGE_DELTA_M)
    parser.add_argument("--range-ratio", type=float, default=DEFAULT_RANGE_RATIO)
    parser.add_argument(
        "--range-hold-time",
        type=float,
        default=DEFAULT_RANGE_HOLD_TIME_S,
    )
    parser.add_argument(
        "--catch-range-inches",
        type=float,
        default=DEFAULT_CATCH_RANGE_INCHES,
        help="Disarm only when the changed rangefinder distance is at or below this many inches.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run detection and print actions without publishing drone commands.",
    )
    parser.add_argument(
        "--no-display",
        action="store_true",
        help="Disable the OpenCV camera preview window.",
    )
    parser.add_argument(
        "--publish-go",
        action="store_true",
        help="Also publish a go request after the 10 second Desktop Start wait.",
    )
    parser.add_argument(
        "--no-ready-hover",
        action="store_true",
        help=(
            "After Desktop Start, skip the default -1.5m ready hover command."
        ),
    )
    parser.add_argument(
        "--inference-device",
        choices=["auto", "cuda", "cpu"],
        default="auto",
        help="Run Hugging Face hand inference on auto-selected GPU, forced CUDA, or CPU.",
    )
    args = parser.parse_args()

    if args.consecutive_hand_frames <= 0:
        raise ValueError("--consecutive-hand-frames must be positive")
    if args.camera_check_period <= 0.0:
        raise ValueError("--camera-check-period must be positive")
    if args.range_baseline_samples <= 0:
        raise ValueError("--range-baseline-samples must be positive")
    if args.range_delta <= 0.0:
        raise ValueError("--range-delta must be positive")
    if args.range_ratio <= 0.0:
        raise ValueError("--range-ratio must be positive")
    if args.range_hold_time < 0.0:
        raise ValueError("--range-hold-time must be non-negative")
    if args.catch_range_inches <= 0.0:
        raise ValueError("--catch-range-inches must be positive")

    return CatchMeConfig(
        hand_confidence=args.hand_confidence,
        consecutive_hand_frames=args.consecutive_hand_frames,
        camera_check_period_s=args.camera_check_period,
        range_baseline_samples=args.range_baseline_samples,
        range_delta_m=args.range_delta,
        range_ratio=args.range_ratio,
        range_hold_time_s=args.range_hold_time,
        catch_range_m=args.catch_range_inches * INCHES_TO_METERS,
        dry_run=args.dry_run,
        display=not args.no_display,
        publish_go=args.publish_go,
        publish_ready_hover=not args.no_ready_hover,
        inference_device=args.inference_device,
    )


def main() -> None:
    config = parse_args()

    with NimbusClient() as client:
        wait_for_desktop_start_and_hover(client, config)
        wait_for_five_fingers(client, config)
        hold_current_position(client, config)
        wait_for_range_change(client, config)
        disarm_after_catch(client, config)


if __name__ == "__main__":
    main()
