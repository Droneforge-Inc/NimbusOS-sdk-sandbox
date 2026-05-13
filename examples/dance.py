from __future__ import annotations

from pathlib import Path
import time

import numpy as np
import sounddevice as sd
import soundfile as sf

from nimbusos_sdk import NimbusClient


SONG_PATH = Path("song2.wav")
BPM = 180
BEATS_PER_MEASURE = 4
BOB_BEATS = (1, 2, 3, 4)
WAYPOINT_PUBLISH_LEAD_S = 0.250
METRONOME_CLICK_VOLUME = 0.0

HOVER_FORWARD_M = 0.75
HOVER_RIGHT_M = 0.0
HOVER_DOWN_M = -1.0
ODD_MEASURE_FORWARD_M = 0.8
EVEN_MEASURE_FORWARD_M = -0.8
LOW_BOUNCE_DOWN_M = -1.95
HIGH_BOUNCE_DOWN_M = -0.85
LEFT_ROLL_RIGHT_M = -0.55
RIGHT_ROLL_RIGHT_M = 0.55
WAYPOINT_THRESHOLD_M = 0.15


def make_click(
    sample_rate: int,
    duration_s: float = 0.03,
    freq_hz: float = 1000.0,
    volume: float = 1.0,
) -> np.ndarray:
    """
    Create one short metronome click.
    """
    n = int(sample_rate * duration_s)
    t = np.arange(n) / sample_rate

    # Short sine burst
    click = np.sin(2.0 * np.pi * freq_hz * t)

    # Fade out so it does not pop too badly
    envelope = np.linspace(1.0, 0.0, n)

    return click * envelope * volume


def add_metronome(
    audio: np.ndarray,
    sample_rate: int,
    bpm: float,
    click_volume: float = 0.35,
) -> np.ndarray:
    """
    Mix metronome clicks into audio.
    """
    if bpm <= 0.0:
        raise ValueError("bpm must be positive")

    # Ensure audio is 2D: [samples, channels]
    if audio.ndim == 1:
        audio = audio[:, None]

    samples = audio.shape[0]
    channels = audio.shape[1]

    output = audio.copy()

    seconds_per_beat = 60.0 / bpm
    samples_per_beat = int(seconds_per_beat * sample_rate)

    click = make_click(sample_rate, volume=click_volume)
    click = click[:, None]  # [click_samples, 1]

    # Duplicate click across channels
    click = np.repeat(click, channels, axis=1)

    for start in range(0, samples, samples_per_beat):
        end = start + len(click)

        if end > samples:
            click_part = click[:samples - start]
            output[start:samples] += click_part
        else:
            output[start:end] += click

    # Prevent clipping
    peak = np.max(np.abs(output))
    if peak > 1.0:
        output = output / peak

    return output


def load_file_with_metronome(path: Path, bpm: float) -> tuple[np.ndarray, int, float]:
    audio, sample_rate = sf.read(path, dtype="float32")

    mixed = add_metronome(
        audio=audio,
        sample_rate=sample_rate,
        bpm=bpm,
        click_volume=METRONOME_CLICK_VOLUME,
    )

    duration_s = mixed.shape[0] / sample_rate
    return mixed, sample_rate, duration_s


def arm_and_hover(client: NimbusClient) -> None:
    print("Waiting 10 seconds. Click Arm in Desktop now.", flush=True)
    time.sleep(10.0)

    print("Publishing go", flush=True)
    client.publish_guidance_request("go")

    time.sleep(5.0)

    print("Publishing hover waypoint", flush=True)
    client.publish_waypoint_command(
        mode="override",
        forward=HOVER_FORWARD_M,
        right=HOVER_RIGHT_M,
        down=HOVER_DOWN_M,
        threshold_m=WAYPOINT_THRESHOLD_M,
        hold_time_s=1.0,
    )


def build_bob_events(
    duration_s: float,
    bpm: float,
) -> list[tuple[float, int, int, float, float, float]]:
    seconds_per_beat = 60.0 / bpm
    seconds_per_measure = seconds_per_beat * BEATS_PER_MEASURE
    events: list[tuple[float, int, int, float, float, float]] = []
    measure_index = 0

    while True:
        measure_start_s = measure_index * seconds_per_measure
        if measure_start_s >= duration_s:
            return events

        measure = measure_index + 1
        if measure % 2:
            forward_m = ODD_MEASURE_FORWARD_M
        else:
            forward_m = EVEN_MEASURE_FORWARD_M

        for beat_index, beat in enumerate(BOB_BEATS):
            beat_time_s = measure_start_s + ((beat - 1) * seconds_per_beat)
            if beat_time_s >= duration_s:
                continue

            event_index = len(events)
            down_m = LOW_BOUNCE_DOWN_M if event_index % 2 == 0 else HIGH_BOUNCE_DOWN_M
            right_m = (
                LEFT_ROLL_RIGHT_M if event_index % 2 == 0 else RIGHT_ROLL_RIGHT_M
            )
            events.append((beat_time_s, measure, beat, forward_m, right_m, down_m))

        measure_index += 1


def sleep_until(deadline_s: float) -> None:
    while True:
        remaining_s = deadline_s - time.monotonic()
        if remaining_s <= 0.0:
            return
        time.sleep(min(remaining_s, 0.01))


def publish_bob_waypoint(
    client: NimbusClient,
    *,
    measure: int,
    beat: int,
    forward_m: float,
    right_m: float,
    down_m: float,
    target_song_time_s: float,
) -> None:
    print(
        "Publishing bob waypoint",
        f"measure={measure}",
        f"beat={beat}",
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


def play_song_and_publish_bobs(
    client: NimbusClient,
    audio: np.ndarray,
    sample_rate: int,
    duration_s: float,
    bpm: float,
) -> None:
    events = build_bob_events(duration_s, bpm)
    if not events:
        return

    (
        first_beat_time_s,
        first_measure,
        first_beat,
        first_forward_m,
        first_right_m,
        first_down_m,
    ) = events[0]
    if first_beat_time_s != 0.0:
        raise RuntimeError("first bob event must land on the start of the song")

    print(
        f"Publishing first bob waypoint {WAYPOINT_PUBLISH_LEAD_S:.3f}s before playback",
        flush=True,
    )
    publish_bob_waypoint(
        client,
        measure=first_measure,
        beat=first_beat,
        forward_m=first_forward_m,
        right_m=first_right_m,
        down_m=first_down_m,
        target_song_time_s=first_beat_time_s,
    )

    sleep_until(time.monotonic() + WAYPOINT_PUBLISH_LEAD_S)

    print(f"Playing {SONG_PATH} with metronome at {bpm:g} BPM", flush=True)
    song_start_s = time.monotonic()
    sd.play(audio, sample_rate)

    for beat_time_s, measure, beat, forward_m, right_m, down_m in events[1:]:
        publish_at_s = song_start_s + beat_time_s - WAYPOINT_PUBLISH_LEAD_S
        sleep_until(publish_at_s)
        publish_bob_waypoint(
            client,
            measure=measure,
            beat=beat,
            forward_m=forward_m,
            right_m=right_m,
            down_m=down_m,
            target_song_time_s=beat_time_s,
        )

    sd.wait()


def main() -> None:
    audio, sample_rate, duration_s = load_file_with_metronome(SONG_PATH, BPM)

    with NimbusClient() as client:
        arm_and_hover(client)
        play_song_and_publish_bobs(client, audio, sample_rate, duration_s, BPM)


if __name__ == "__main__":
    main()
