# NimbusOS SDK Sandbox

Small external-consumer project for testing the `nimbusos-sdk` Python package exactly like a downstream developer would use it.

Setup: https://droneforge.gitbook.io/droneforge-docs/nimbusos-sdk/python-api/setup

Quickstart: https://droneforge.gitbook.io/droneforge-docs/nimbusos-sdk/python-api/quick-start

Documentation: https://droneforge.gitbook.io/droneforge-docs

## 1. Install

After `nimbusos-sdk` is published to PyPI:

```bash
cd /Users/davidcrabtree/projects/NimbusOS-sdk-sandbox
uv sync --upgrade-package nimbusos-sdk
```

## 2. Live test against NimbusOS

Start NimbusOS Desktop, connect the drone, and complete the normal setup flow.

Then run the live examples from the repo root in this order. Each example is
meant to show one practical SDK pattern you can reuse in your own program.

### `getting_started.py`

Shows the basic flight-control loop: connect with `NimbusClient`, arm, send
`go`, publish a short rectangle of waypoint commands, wait for waypoint-status
completion, land, and disarm after the rangefinder reports the drone is near the
ground.

```bash
uv run python getting_started.py
```

### `commanding_yaw.py`

Shows how to command heading changes directly. It arms, starts flight, publishes
four 90 degree yaw turns, then lands.

```bash
uv run python commanding_yaw.py
```

### `dance_audio_reactive.py`

Shows how to drive motion from an external signal. It analyzes an audio file,
detects bass, snare, and treble events, then publishes timed waypoint updates
while the song plays.

```bash
uv run python dance_audio_reactive.py
```

### `next_steps.py`

Shows how to build a slightly more structured mission with reusable helper
functions. It flies four forward legs, waits for each waypoint to complete,
turns 90 degrees between legs, and lands/disarms if a leg times out.

```bash
uv run python next_steps.py
```

## Development Loop

When changing SDK code:

```bash
cd /Users/davidcrabtree/projects/droneforge_mvp/NimbusOS/sdk
uv run python scripts/refresh_schema.py --check
uv build

cd /Users/davidcrabtree/projects/NimbusOS-sdk-sandbox
uv venv --python 3.12
cd /Users/davidcrabtree/projects/droneforge_mvp/NimbusOS/sdk
uv run python scripts/install_local_wheel.py /Users/davidcrabtree/projects/NimbusOS-sdk-sandbox/.venv/bin/python
cd /Users/davidcrabtree/projects/NimbusOS-sdk-sandbox
.venv/bin/python -c "from nimbusos_sdk import NimbusClient, ReceivedMessage; print('import ok')"
```

Use this repo to refine the external developer experience. If an example feels clunky here, change the SDK API or docs in the monorepo, rebuild the wheel, reinstall it here, and test again.
