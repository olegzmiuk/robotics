# UAV vision prototype

An educational Python project for UAV computer vision and read-only Betaflight
telemetry. It detects a black square in an image and includes a minimal MSP v1
client for querying the flight controller over UART.

## Project structure

```text
.
├── uav/
│   ├── src/
│   │   └── uav/
│   │       ├── __init__.py
│   │       ├── capture.py           # capture one local webcam frame
│   │       ├── square_detection.py  # detection algorithm and result models
│   │       ├── cli.py               # image file I/O and CLI arguments
│   │       ├── msp.py               # MSP v1 framing, transport, and API model
│   │       └── msp_cli.py           # read-only Betaflight CLI
│   ├── tests/
│   │   ├── test_capture.py
│   │   ├── test_cli.py
│   │   ├── test_msp.py
│   │   ├── test_msp_cli.py
│   │   └── test_square_detection.py
│   └── artifacts/                  # local input and generated images
├── dashboard/
│   ├── backend/                    # FastAPI API and telemetry orchestration
│   └── frontend/                   # React, TypeScript, and Vite UI
└── pyproject.toml           # dependencies and tool configuration
```

The flow is intentionally small:

```text
input.jpg
  -> uav/src/uav/cli.py
  -> detect_squares()
  -> annotate_squares()
  -> JSON result + annotated_<timestamp>.jpg
```

## Local setup

Python 3.10 or newer and `uv` are required. Install `uv` once using one of the
methods from its official installation guide, then synchronize the environment:

```bash
uv sync --locked --extra dev
npm ci --prefix dashboard/frontend
```

`uv` creates `.venv` automatically. `pyproject.toml` declares the allowed
dependency ranges; `uv.lock` stores the exact resolved versions and must be
committed to Git. The dashboard frontend requires Node.js 24 and uses
`package-lock.json` for reproducible npm installs.

## Run the detector

```bash
python -m uav.cli uav/artifacts/input.jpg
```

The installed command does the same thing:

```bash
robot-detect-square uav/artifacts/input.jpg
```

`uav/artifacts/input.jpg` is an example path to an image supplied by you. The
`uav/artifacts/` directory is created automatically and ignored by Git.
Generated files use names such as `annotated_14:37:22:184532.jpg`, so
consecutive runs do not overwrite each other. Passing `--output` explicitly
still uses the exact path you provide.

### Capture from the MacBook camera

```bash
robot-capture-image
```

The first run may cause macOS to ask for camera permission for Terminal or VS
Code. The command discards 30 warmup frames, processes the next available
frame, and writes a timestamped annotated image to `uav/artifacts/`.

Optional arguments:

```bash
robot-capture-image --camera-index 0 --warmup-frames 30
```

## Read Betaflight telemetry

The MSP client supports the read-only `MSP_API_VERSION`, `MSP_STATUS`,
`MSP_ATTITUDE`, and `MSP_ANALOG` commands. On the Raspberry Pi, with UART3
already configured for MSP in Betaflight, read the API version with:

```bash
uv run --locked uav-msp --port /dev/ttyAMA0 --command api-version
```

Expected output for the currently tested Betaflight 4.4.3 controller:

```text
MSP API: 1.45 (protocol version 0)
```

Read attitude once:

```bash
uv run --locked uav-msp --port /dev/ttyAMA0 --command attitude
```

Continuously poll attitude every 250 milliseconds until Ctrl+C:

```bash
uv run --locked uav-msp \
  --port /dev/ttyAMA0 \
  --command attitude \
  --watch \
  --poll-interval 0.25
```

Betaflight 4.4.3 returns a six-byte attitude payload: signed little-endian
16-bit roll and pitch in tenths of a degree, followed by signed little-endian
16-bit yaw in whole degrees. The CLI converts all three values to degrees.

Read flight-controller status:

```bash
uv run --locked uav-msp --port /dev/ttyAMA0 --command status
```

The output includes PID cycle time, I2C error count, available sensors, raw
flight-mode flags, PID profile, system load, arming-disable flags, and the
reboot-required state. Mode flag positions depend on `MSP_BOXIDS`, so the CLI
deliberately displays the exact bitmask instead of guessing mode names.

Read battery and signal telemetry:

```bash
uv run --locked uav-msp --port /dev/ttyAMA0 --command analog
```

`MSP_ANALOG` reports precise battery voltage in 0.01 V units, legacy voltage in
0.1 V units, current in signed 0.01 A units, consumed capacity in mAh, and raw
RSSI. The CLI converts voltage and current to volts and amperes.

The command defaults to 115200 baud and a two-second timeout. It does not arm
the controller, control motors, modify Betaflight configuration, or write to
EEPROM. Bench tests must be performed without propellers.

## Run the web dashboard

The dashboard is kept separate from the reusable UAV package:

```text
dashboard/frontend  -> React UI
dashboard/backend   -> FastAPI -> uav.msp -> /dev/ttyAMA0
```

For local frontend development on the MacBook while FastAPI runs on the
Raspberry Pi:

```bash
cd dashboard/frontend
DASHBOARD_BACKEND_URL=http://raspberrypi.local:8000 npm run dev
```

Open `http://localhost:5173`. Vite proxies `/api` to the Raspberry Pi, so the
browser never accesses UART directly.

For a production-like deployment on Raspberry Pi, build the static frontend
and run one FastAPI process that serves both the UI and API:

```bash
uv sync --locked --extra dev
npm ci --prefix dashboard/frontend
npm run build --prefix dashboard/frontend
uv run --locked uvicorn uav_dashboard.api:app --host 0.0.0.0 --port 8000
```

Then open `http://raspberrypi.local:8000` from the MacBook. Node.js is needed
only to build the frontend; it is not a second production backend process.

The first dashboard milestone provides telemetry snapshots, connection and
error states, an MSP command journal, automatic one-second refresh, an attitude
indicator, battery and controller status, and a reserved camera panel. All API
routes are read-only. Camera streaming is not implemented yet.

## Run the checks

```bash
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
uv run --locked pytest
cd dashboard/frontend
npm run check
npm run build
```

After changing dependencies in `pyproject.toml`, deliberately refresh the lock
file with `uv lock` and commit both files.

## Next increments

The API-version CLI has passed its Raspberry Pi hardware smoke test. Next,
deploy the dashboard to Raspberry Pi and smoke-test attitude, status, and
analog reads through the browser. Raspberry Pi Camera Module 3 capture and
dashboard streaming remain a separate future increment; the detection
algorithm must remain independent from Picamera2 and serial communication.

