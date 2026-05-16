from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any

import numpy as np
import sounddevice as sd
import soundfile as sf

from nimbusos_sdk import NimbusClient


DEFAULT_SONG_PATH = Path("song4.wav")
DEFAULT_MAGNITUDE = 1.0
WAYPOINT_PUBLISH_LEAD_S = 0.250

HOVER_FORWARD_M = 0.75
HOVER_RIGHT_M = 0.0
HOVER_DOWN_M = -1.0
DESKTOP_START_WAIT_S = 10.0
STATE_SAMPLE_TIMEOUT_S = 2.0
FORWARD_PITCH_M = 0.8
BACKWARD_PITCH_M = -0.8
LOW_BOUNCE_DOWN_M = -1.95
HIGH_BOUNCE_DOWN_M = -0.85
LEFT_ROLL_RIGHT_M = -0.55
RIGHT_ROLL_RIGHT_M = 0.55
WAYPOINT_THRESHOLD_M = 0.15

ANALYSIS_WINDOW_S = 0.046
ANALYSIS_HOP_S = 0.020
EVENT_MERGE_WINDOW_S = 0.035


@dataclass(frozen=True)
class BandConfig:
    name: str
    low_hz: float
    high_hz: float
    min_interval_s: float
    threshold_mad: float


@dataclass(frozen=True)
class AudioEvent:
    time_s: float
    bands: frozenset[str]


BANDS = (
    BandConfig("bass", 20.0, 160.0, 0.28, 2.2),
    BandConfig("snare", 180.0, 2_500.0, 0.18, 2.6),
    BandConfig("treble", 4_000.0, 12_000.0, 0.12, 2.8),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Play a song and drive dance waypoints from audio-band onsets."
    )
    parser.add_argument(
        "song_path",
        nargs="?",
        type=Path,
        default=DEFAULT_SONG_PATH,
        help=f"Audio file to play and analyze. Defaults to {DEFAULT_SONG_PATH}.",
    )
    parser.add_argument(
        "--magnitude",
        type=float,
        default=DEFAULT_MAGNITUDE,
        help=(
            "Movement amplitude multiplier. 0 keeps the hover pose, "
            "1 uses the default dance size, values above 1 are more reactive."
        ),
    )
    args = parser.parse_args()

    if args.magnitude < 0.0:
        raise ValueError("--magnitude must be non-negative")

    return args


def load_file(path: Path) -> tuple[np.ndarray, int, float]:
    audio, sample_rate = sf.read(path, dtype="float32")
    duration_s = audio.shape[0] / sample_rate
    return audio, sample_rate, duration_s


def mono_for_analysis(audio: np.ndarray) -> np.ndarray:
    if audio.ndim == 1:
        mono = audio
    else:
        mono = np.mean(audio, axis=1)

    peak = float(np.max(np.abs(mono))) if mono.size else 0.0
    if peak > 0.0:
        mono = mono / peak

    return mono.astype(np.float32, copy=False)


def smooth(values: np.ndarray, samples: int) -> np.ndarray:
    if samples <= 1 or values.size == 0:
        return values

    kernel = np.ones(samples, dtype=np.float32) / samples
    return np.convolve(values, kernel, mode="same")


def analyze_band_novelty(
    audio: np.ndarray,
    sample_rate: int,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    mono = mono_for_analysis(audio)
    frame_size = max(512, int(sample_rate * ANALYSIS_WINDOW_S))
    hop_size = max(1, int(sample_rate * ANALYSIS_HOP_S))

    if mono.size < frame_size:
        return np.array([], dtype=np.float32), {
            band.name: np.array([], dtype=np.float32) for band in BANDS
        }

    starts = np.arange(0, mono.size - frame_size + 1, hop_size)
    times_s = (starts + (frame_size / 2.0)) / sample_rate
    freqs = np.fft.rfftfreq(frame_size, d=1.0 / sample_rate)
    window = np.hanning(frame_size).astype(np.float32)
    masks = {
        band.name: (freqs >= band.low_hz) & (freqs <= band.high_hz) for band in BANDS
    }
    energies = {band.name: np.zeros(starts.size, dtype=np.float32) for band in BANDS}

    for index, start in enumerate(starts):
        frame = mono[start : start + frame_size] * window
        spectrum = np.abs(np.fft.rfft(frame))

        for band in BANDS:
            mask = masks[band.name]
            if np.any(mask):
                energies[band.name][index] = float(np.mean(spectrum[mask] ** 2))

    novelty_by_band: dict[str, np.ndarray] = {}
    for band in BANDS:
        envelope = np.log1p(energies[band.name])
        novelty = np.maximum(np.diff(envelope, prepend=envelope[:1]), 0.0)
        novelty_by_band[band.name] = smooth(novelty, samples=3)

    return times_s.astype(np.float32, copy=False), novelty_by_band


def detect_band_events(
    times_s: np.ndarray,
    novelty: np.ndarray,
    band: BandConfig,
) -> list[float]:
    if times_s.size < 3 or novelty.size < 3:
        return []

    median = float(np.median(novelty))
    mad = float(np.median(np.abs(novelty - median)))
    threshold = median + (band.threshold_mad * max(mad, 1.0e-6))
    events: list[float] = []
    last_event_time_s = -band.min_interval_s

    for index in range(1, novelty.size - 1):
        if novelty[index] < threshold:
            continue
        if novelty[index] < novelty[index - 1] or novelty[index] < novelty[index + 1]:
            continue

        event_time_s = float(times_s[index])
        if event_time_s - last_event_time_s < band.min_interval_s:
            continue

        events.append(event_time_s)
        last_event_time_s = event_time_s

    return events


def merge_band_events(events: list[tuple[float, str]]) -> list[AudioEvent]:
    merged: list[AudioEvent] = []

    for event_time_s, band_name in sorted(events):
        if merged and event_time_s - merged[-1].time_s <= EVENT_MERGE_WINDOW_S:
            previous = merged[-1]
            merged[-1] = AudioEvent(
                time_s=previous.time_s,
                bands=previous.bands | frozenset((band_name,)),
            )
        else:
            merged.append(AudioEvent(event_time_s, frozenset((band_name,))))

    return merged


def build_audio_events(audio: np.ndarray, sample_rate: int) -> list[AudioEvent]:
    times_s, novelty_by_band = analyze_band_novelty(audio, sample_rate)
    raw_events: list[tuple[float, str]] = []

    for band in BANDS:
        band_events = detect_band_events(times_s, novelty_by_band[band.name], band)
        print(
            f"Detected {len(band_events)} {band.name} triggers "
            f"({band.low_hz:g}-{band.high_hz:g} Hz)",
            flush=True,
        )
        raw_events.extend((event_time_s, band.name) for event_time_s in band_events)

    return merge_band_events(raw_events)


def latest_state(client: NimbusClient, *, timeout_sec: float) -> Any | None:
    state = None
    for sampled_state in client.state(timeout_sec=timeout_sec):
        state = sampled_state
    return state


def wait_for_desktop_start_and_hover(client: NimbusClient) -> None:
    print("Waiting 10 seconds. Press Start in Desktop now.", flush=True)
    time.sleep(DESKTOP_START_WAIT_S)

    print("Publishing arm request.", flush=True)
    client.publish_arm_state(True)

    print("Publishing go", flush=True)
    client.publish_guidance_request("go")

    state = latest_state(client, timeout_sec=STATE_SAMPLE_TIMEOUT_S)
    if state is None:
        print(
            "No state sample available; publishing vertical-only relative hover "
            "request instead of an absolute horizontal waypoint.",
            flush=True,
        )
        client.publish_guidance_request(
            "relative_waypoint",
            forward=0.0,
            right=0.0,
            down=HOVER_DOWN_M,
            hold_time_s=1.0,
        )
        return

    forward_m = state.position.x_m
    right_m = state.position.y_m

    print(
        "Publishing ready hover waypoint",
        f"forward={forward_m:.2f}m",
        f"right={right_m:.2f}m",
        f"down={HOVER_DOWN_M:.2f}m",
        flush=True,
    )
    client.publish_waypoint_command(
        mode="override",
        forward=forward_m,
        right=right_m,
        down=HOVER_DOWN_M,
        threshold_m=WAYPOINT_THRESHOLD_M,
        hold_time_s=1.0,
    )


def sleep_until(deadline_s: float) -> None:
    while True:
        remaining_s = deadline_s - time.monotonic()
        if remaining_s <= 0.0:
            return
        time.sleep(min(remaining_s, 0.01))


def publish_audio_waypoint(
    client: NimbusClient,
    *,
    bands: frozenset[str],
    forward_m: float,
    right_m: float,
    down_m: float,
    target_song_time_s: float,
) -> None:
    print(
        "Publishing audio-reactive waypoint",
        f"bands={','.join(sorted(bands))}",
        f"forward={forward_m:.2f}m",
        f"right={right_m:.2f}m",
        f"down={down_m:.2f}m",
        f"target_song_time={target_song_time_s:.3f}s",
        flush=True,
    )
    client.publish_waypoint_command(
        mode="override",
        forward=forward_m,
        right=right_m,
        down=down_m,
        threshold_m=WAYPOINT_THRESHOLD_M,
        hold_time_s=0.0,
    )


def scale_reaction(neutral_m: float, target_m: float, magnitude: float) -> float:
    return neutral_m + ((target_m - neutral_m) * magnitude)


def play_song_and_publish_audio_reactions(
    client: NimbusClient,
    song_path: Path,
    audio: np.ndarray,
    sample_rate: int,
    events: list[AudioEvent],
    magnitude: float,
) -> None:
    print(
        f"Playing {song_path} with audio-reactive waypoints "
        f"at magnitude={magnitude:g}",
        flush=True,
    )
    song_start_s = time.monotonic()
    sd.play(audio, sample_rate)

    forward_m = HOVER_FORWARD_M
    right_m = HOVER_RIGHT_M
    down_m = HOVER_DOWN_M
    bass_low_next = True
    snare_forward_next = True
    treble_left_next = True

    for event in events:
        publish_at_s = song_start_s + event.time_s - WAYPOINT_PUBLISH_LEAD_S
        sleep_until(publish_at_s)

        if "bass" in event.bands:
            target_down_m = LOW_BOUNCE_DOWN_M if bass_low_next else HIGH_BOUNCE_DOWN_M
            down_m = scale_reaction(HOVER_DOWN_M, target_down_m, magnitude)
            bass_low_next = not bass_low_next

        if "snare" in event.bands:
            target_forward_m = FORWARD_PITCH_M if snare_forward_next else BACKWARD_PITCH_M
            forward_m = scale_reaction(HOVER_FORWARD_M, target_forward_m, magnitude)
            snare_forward_next = not snare_forward_next

        if "treble" in event.bands:
            target_right_m = LEFT_ROLL_RIGHT_M if treble_left_next else RIGHT_ROLL_RIGHT_M
            right_m = scale_reaction(HOVER_RIGHT_M, target_right_m, magnitude)
            treble_left_next = not treble_left_next

        publish_audio_waypoint(
            client,
            bands=event.bands,
            forward_m=forward_m,
            right_m=right_m,
            down_m=down_m,
            target_song_time_s=event.time_s,
        )

    sd.wait()


def main() -> None:
    args = parse_args()
    audio, sample_rate, duration_s = load_file(args.song_path)
    events = build_audio_events(audio, sample_rate)
    print(
        f"Prepared {len(events)} merged audio triggers over {duration_s:.2f}s",
        flush=True,
    )

    with NimbusClient() as client:
        wait_for_desktop_start_and_hover(client)
        play_song_and_publish_audio_reactions(
            client,
            args.song_path,
            audio,
            sample_rate,
            events,
            args.magnitude,
        )


if __name__ == "__main__":
    main()
