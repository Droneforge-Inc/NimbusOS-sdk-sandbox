# NimbusOS SDK Sandbox

Small external-consumer project for testing the `nimbusos-sdk` Python package exactly like a downstream developer would use it.

## 1. Install

After `nimbusos-sdk` is published to PyPI:

```bash
cd /Users/davidcrabtree/projects/NimbusOS-sdk-sandbox
uv sync
```

## 2. Smoke test the install

```bash
uv run python examples_v0/smoke_import.py
uv run nimbusos-subscribe --help
uv run nimbusos-arm --help
uv run nimbusos-guidance-request --help
uv run nimbusos-waypoint-command --help
```

## 3. Live test against NimbusOS

Start NimbusOS so core is publishing on the default local ZeroMQ endpoints:

```text
publish:   tcp://127.0.0.1:7771
subscribe: tcp://127.0.0.1:7772
```

Then run the developer-owned telemetry example:

```bash
uv run python examples/print_telemetry.py
```

For the live arm-and-waypoint test, the script publishes `arm_state`, waits
10 seconds, then publishes four waypoint commands without sleeps between them:
3 meters forward, 2 meters left with a 20 second hold, 2 meters right, then
3 meters backward.

```bash
uv run python examples/arm_waypoint_sequence.py
```

The old command-line shaped examples are preserved under `examples_v0/` for reference:

```bash
uv run python examples_v0/subscribe_topic.py telemetry --timeout 5
uv run python examples_v0/send_waypoint.py --forward 1.0 --right 0.0 --down -1.0
uv run python examples_v0/send_guidance.py go
```

## Development Loop

When changing SDK code:

```bash
cd /Users/davidcrabtree/projects/droneforge_mvp/NimbusOS/sdk
uv run python scripts/refresh_schema.py --check
uv build

cd /Users/davidcrabtree/projects/NimbusOS-sdk-sandbox
uv venv --python 3.12
uv pip install --reinstall /Users/davidcrabtree/projects/droneforge_mvp/NimbusOS/sdk/dist/nimbusos_sdk-0.1.0-py3-none-any.whl
.venv/bin/python examples_v0/smoke_import.py
```

Use this repo to refine the external developer experience. If an example feels clunky here, change the SDK API or docs in the monorepo, rebuild the wheel, reinstall it here, and test again.
