# NimbusOS SDK Sandbox

Small external-consumer project for testing the local `nimbus-developer` Python SDK exactly like a downstream developer would use it.

## 1. Build the SDK package

From the NimbusOS monorepo:

```bash
cd /Users/davidcrabtree/projects/droneforge_mvp/NimbusOS/developer
uv run python scripts/refresh_schema.py --check
uv build
```

That should produce:

```text
dist/nimbus_developer-0.1.0-py3-none-any.whl
dist/nimbus_developer-0.1.0.tar.gz
```

## 2. Install the built wheel here

From this sandbox repo:

```bash
cd /Users/davidcrabtree/projects/NimbusOS-sdk-sandbox
uv venv --python 3.12
uv pip install /Users/davidcrabtree/projects/droneforge_mvp/NimbusOS/developer/dist/nimbus_developer-0.1.0-py3-none-any.whl
```

## 3. Smoke test the install

```bash
.venv/bin/python examples_v0/smoke_import.py
.venv/bin/nimbus-subscribe --help
.venv/bin/nimbus-guidance-request --help
.venv/bin/nimbus-waypoint-command --help
```

## 4. Live test against NimbusOS

Start NimbusOS so core is publishing on the default local ZeroMQ endpoints:

```text
publish:   tcp://127.0.0.1:7771
subscribe: tcp://127.0.0.1:7772
```

Then run the developer-owned telemetry example:

```bash
.venv/bin/python examples/print_telemetry.py
```

The old command-line shaped examples are preserved under `examples_v0/` for reference:

```bash
.venv/bin/python examples_v0/subscribe_topic.py telemetry --timeout 5
.venv/bin/python examples_v0/send_waypoint.py --forward 1.0 --right 0.0 --down -1.0
.venv/bin/python examples_v0/send_guidance.py go
```

## Development Loop

When changing SDK code:

```bash
cd /Users/davidcrabtree/projects/droneforge_mvp/NimbusOS/developer
uv run python scripts/refresh_schema.py --check
uv build

cd /Users/davidcrabtree/projects/NimbusOS-sdk-sandbox
uv pip install --reinstall /Users/davidcrabtree/projects/droneforge_mvp/NimbusOS/developer/dist/nimbus_developer-0.1.0-py3-none-any.whl
.venv/bin/python examples_v0/smoke_import.py
```

Use this repo to refine the external developer experience. If an example feels clunky here, change the SDK API or docs in the monorepo, rebuild the wheel, reinstall it here, and test again.
