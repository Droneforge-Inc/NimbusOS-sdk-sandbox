from __future__ import annotations

from dataclasses import dataclass
from collections import deque
import argparse
import threading
import time
from typing import Any

import zmq

from nimbusos_sdk.client import DEFAULT_SUB_ENDPOINT
from nimbusos_sdk.schema import load_message_class


WINDOW_NAME = "Nimbus camera"
DEFAULT_CAMERA_TOPIC = "camera"
DEFAULT_RECEIVE_HWM = 1
DEFAULT_DISPLAY_HZ = 120.0
FPS_WINDOW_SAMPLES = 60
MAX_DRAIN_PER_TICK = 1000
BENCHMARK_LOG_PERIOD_S = 1.0


@dataclass
class LatestFrame:
    payload: bytes | None = None
    seq: int | None = None
    t_ns: int | None = None
    received_monotonic_ns: int | None = None


@dataclass
class ReaderStats:
    received: int = 0
    drained: int = 0
    dropped_by_seq: int = 0
    last_seq: int | None = None


def raw_camera_reader(
    sub_endpoint: str,
    topic: str,
    latest: LatestFrame,
    stats: ReaderStats,
    lock: threading.Lock,
    stop_event: threading.Event,
    receive_hwm: int,
) -> None:
    context = zmq.Context.instance()
    socket = context.socket(zmq.SUB)
    socket.setsockopt(zmq.LINGER, 0)
    socket.setsockopt(zmq.RCVHWM, receive_hwm)
    topic_bytes = topic.encode("utf-8")
    socket.setsockopt(zmq.SUBSCRIBE, topic_bytes)
    socket.connect(sub_endpoint)

    try:
        while not stop_event.is_set():
            if socket.poll(timeout=100) == 0:
                continue

            latest_payload_frame = None
            latest_received_ns = None
            drained = 0

            while drained < MAX_DRAIN_PER_TICK:
                try:
                    topic_frame, payload_frame = socket.recv_multipart(
                        flags=zmq.NOBLOCK,
                        copy=False,
                    )
                except zmq.Again:
                    break

                if topic_frame.bytes != topic_bytes:
                    continue

                latest_payload_frame = payload_frame
                latest_received_ns = time.monotonic_ns()
                drained += 1

            if latest_payload_frame is None:
                continue

            latest_payload = latest_payload_frame.bytes

            with lock:
                stats.received += 1
                stats.drained += drained
                latest.payload = latest_payload
                latest.received_monotonic_ns = latest_received_ns
    finally:
        socket.close()


def decode_jpeg(cv2: Any, jpeg: Any) -> Any | None:
    return cv2.imdecode(jpeg, cv2.IMREAD_COLOR)


def put_overlay(
    cv2: Any,
    image: Any,
    *,
    seq: int | None,
    width: int | None,
    height: int | None,
    source_age_ms: float | None,
    local_age_ms: float | None,
    decode_ms: float,
    display_fps: float,
    received: int,
    drained: int,
    dropped_by_seq: int,
) -> None:
    lines = [
        f"seq={seq} source_age={format_ms(source_age_ms)} local_age={format_ms(local_age_ms)}",
        f"frame={width}x{height} decode={decode_ms:.1f}ms display={display_fps:.1f}fps",
        f"latest_updates={received} drained={drained} seq_gaps={dropped_by_seq} q/Esc quit",
    ]

    for index, line in enumerate(lines):
        y = 28 + (index * 28)
        cv2.putText(
            image,
            line,
            (12, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 0, 0),
            4,
            cv2.LINE_AA,
        )
        cv2.putText(
            image,
            line,
            (12, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 255, 255),
            2,
            cv2.LINE_AA,
        )


def format_ms(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.1f}ms"


def source_age_ms(frame_t_ns: int | None) -> float | None:
    if frame_t_ns is None:
        return None

    age_ms = (time.monotonic_ns() - frame_t_ns) / 1_000_000.0
    if age_ms < -1000.0:
        return None
    return age_ms


def run_camera_preview(args: argparse.Namespace) -> None:
    if args.list_topics:
        list_topic_rates(args.sub_endpoint)
        return

    camera_message_class = load_message_class("CameraJpegMessage")
    latest = LatestFrame()
    stats = ReaderStats()
    lock = threading.Lock()
    stop_event = threading.Event()
    frame_times: deque[float] = deque(maxlen=FPS_WINDOW_SAMPLES)
    displayed_seq: int | None = None
    display_period_s = 1.0 / args.display_hz

    cv2 = None
    if not args.receive_only:
        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError(
                "Camera preview needs opencv-python. Run uv sync."
            ) from exc

        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)

    reader = threading.Thread(
        target=raw_camera_reader,
        args=(
            args.sub_endpoint,
            args.topic,
            latest,
            stats,
            lock,
            stop_event,
            args.receive_hwm,
        ),
        daemon=True,
    )
    reader.start()

    try:
        while not stop_event.is_set():
            with lock:
                payload = latest.payload
                received_monotonic_ns = latest.received_monotonic_ns
                received = stats.received
                drained = stats.drained
                dropped_by_seq = stats.dropped_by_seq

            if payload is None or received == displayed_seq:
                time.sleep(0.001)
                continue

            displayed_seq = received
            if args.receive_only:
                print_receive_only_stats(stats, lock)
                continue

            decode_start_s = time.perf_counter()
            decoded = camera_message_class.GetRootAs(payload, 0)
            seq = decoded.Seq()
            t_ns = decoded.TNs()
            width = decoded.Width()
            height = decoded.Height()
            jpeg = decoded.JpegAsNumpy()
            image = decode_jpeg(cv2, jpeg)
            decode_ms = (time.perf_counter() - decode_start_s) * 1000.0
            if image is None:
                continue

            with lock:
                if stats.last_seq is not None and seq > stats.last_seq + 1:
                    stats.dropped_by_seq += seq - stats.last_seq - 1
                    dropped_by_seq = stats.dropped_by_seq
                stats.last_seq = seq

            now_s = time.perf_counter()
            frame_times.append(now_s)
            display_fps = rolling_fps(frame_times)
            local_age_ms = None
            if received_monotonic_ns is not None:
                local_age_ms = (time.monotonic_ns() - received_monotonic_ns) / 1_000_000.0

            if not args.no_overlay:
                put_overlay(
                    cv2,
                    image,
                    seq=seq,
                    width=width,
                    height=height,
                    source_age_ms=source_age_ms(t_ns),
                    local_age_ms=local_age_ms,
                    decode_ms=decode_ms,
                    display_fps=display_fps,
                    received=received,
                    drained=drained,
                    dropped_by_seq=dropped_by_seq,
                )

            cv2.imshow(WINDOW_NAME, image)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                return

            elapsed_s = time.perf_counter() - now_s
            if elapsed_s < display_period_s:
                time.sleep(display_period_s - elapsed_s)
    finally:
        stop_event.set()
        reader.join(timeout=1.0)
        if cv2 is not None:
            cv2.destroyAllWindows()


def rolling_fps(frame_times: deque[float]) -> float:
    if len(frame_times) < 2:
        return 0.0

    elapsed_s = frame_times[-1] - frame_times[0]
    if elapsed_s <= 0.0:
        return 0.0
    return (len(frame_times) - 1) / elapsed_s


def print_receive_only_stats(stats: ReaderStats, lock: threading.Lock) -> None:
    now_s = time.monotonic()
    last_print_s = getattr(print_receive_only_stats, "_last_print_s", 0.0)
    if now_s - last_print_s < BENCHMARK_LOG_PERIOD_S:
        return

    with lock:
        received = stats.received
        drained = stats.drained
        seq_gaps = stats.dropped_by_seq

    last_received = getattr(print_receive_only_stats, "_last_received", 0)
    last_drained = getattr(print_receive_only_stats, "_last_drained", 0)
    elapsed_s = now_s - last_print_s if last_print_s else BENCHMARK_LOG_PERIOD_S
    latest_hz = (received - last_received) / elapsed_s
    drained_hz = (drained - last_drained) / elapsed_s

    print(
        "receive-only",
        f"latest_updates={received}",
        f"latest_hz={latest_hz:.1f}",
        f"drained={drained}",
        f"drained_hz={drained_hz:.1f}",
        f"seq_gaps={seq_gaps}",
        flush=True,
    )
    setattr(print_receive_only_stats, "_last_print_s", now_s)
    setattr(print_receive_only_stats, "_last_received", received)
    setattr(print_receive_only_stats, "_last_drained", drained)


def list_topic_rates(sub_endpoint: str) -> None:
    context = zmq.Context.instance()
    socket = context.socket(zmq.SUB)
    socket.setsockopt(zmq.LINGER, 0)
    socket.setsockopt(zmq.RCVHWM, 4096)
    socket.setsockopt(zmq.SUBSCRIBE, b"")
    socket.connect(sub_endpoint)

    counts: dict[str, int] = {}
    byte_counts: dict[str, int] = {}
    last_s = time.monotonic()

    print(f"Monitoring all topics on {sub_endpoint}. Press Ctrl-C to stop.", flush=True)
    try:
        while True:
            if socket.poll(timeout=100) != 0:
                topic_frame, payload_frame = socket.recv_multipart(copy=False)
                topic = topic_frame.bytes.decode("utf-8", errors="replace")
                counts[topic] = counts.get(topic, 0) + 1
                byte_counts[topic] = byte_counts.get(topic, 0) + len(payload_frame)

            now_s = time.monotonic()
            if now_s - last_s >= 1.0:
                elapsed_s = now_s - last_s
                if counts:
                    parts = []
                    for topic in sorted(counts):
                        hz = counts[topic] / elapsed_s
                        mbps = (byte_counts[topic] * 8.0) / elapsed_s / 1_000_000.0
                        parts.append(f"{topic}: {hz:.1f}Hz {mbps:.2f}Mbps")
                    print(" | ".join(parts), flush=True)
                else:
                    print("no messages", flush=True)
                counts.clear()
                byte_counts.clear()
                last_s = now_s
    finally:
        socket.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Show the latest Nimbus camera frame.")
    parser.add_argument("--sub-endpoint", default=DEFAULT_SUB_ENDPOINT)
    parser.add_argument("--topic", default=DEFAULT_CAMERA_TOPIC)
    parser.add_argument("--receive-hwm", type=int, default=DEFAULT_RECEIVE_HWM)
    parser.add_argument("--display-hz", type=float, default=DEFAULT_DISPLAY_HZ)
    parser.add_argument("--no-overlay", action="store_true")
    parser.add_argument(
        "--receive-only",
        action="store_true",
        help="Drain camera messages and print receive stats without JPEG decode/display.",
    )
    parser.add_argument(
        "--list-topics",
        action="store_true",
        help="Print per-topic message rates on the subscribe endpoint.",
    )
    args = parser.parse_args()

    if args.receive_hwm <= 0:
        raise ValueError("--receive-hwm must be positive")
    if args.display_hz <= 0.0:
        raise ValueError("--display-hz must be positive")

    return args


def main() -> None:
    run_camera_preview(parse_args())


if __name__ == "__main__":
    main()
