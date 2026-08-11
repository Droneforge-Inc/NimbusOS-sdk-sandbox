from __future__ import annotations

from nimbusos_sdk import NimbusClient

STATE_TIMEOUT_S = 10.0


def main() -> None:
    saw_state = False

    with NimbusClient() as client:
        for state in client.selected_state(timeout_sec=STATE_TIMEOUT_S):
            saw_state = True
            print(
                "state "
                f"forward={state.position.x_m:.2f}m "
                f"right={state.position.y_m:.2f}m "
                f"down={state.position.z_m:.2f}m",
                flush=True,
            )

    if not saw_state:
        raise TimeoutError(f"No drone state received within {STATE_TIMEOUT_S:.0f}s")


if __name__ == "__main__":
    main()
