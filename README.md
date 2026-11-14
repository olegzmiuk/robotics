# UAV vision prototype

An educational Python project for UAV computer vision and Betaflight telemetry.
It detects a black square, includes an MSP v1 client for querying the flight
controller over UART, and is preparing a strictly gated, propeller-free motor
response demonstration for a secured bench stand. The currently implemented
MSP client and dashboard remain read-only.

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
                              -> rpicam-vid -> Camera Module 3
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

Before starting the dashboard, confirm that Raspberry Pi OS detects the camera:

```bash
rpicam-hello --list-cameras
```

If `rpicam-hello` or `rpicam-vid` is missing, install the Raspberry Pi camera
applications and reboot:

```bash
sudo apt update
sudo apt install -y rpicam-apps
sudo reboot
```

The first dashboard milestone provides telemetry snapshots, connection and
error states, an MSP command journal, a shared 10 Hz WebSocket stream, an
attitude indicator, battery and controller status, and a live Camera Module 3
panel with square detection.
The backend polls MSP only while at least one browser is connected, regardless
of how many dashboard tabs subscribe. It keeps one serial connection open,
reads `MSP_API_VERSION` once per connection, reads `MSP_ATTITUDE` at the fast
stream rate, and refreshes `MSP_STATUS` plus `MSP_ANALOG` once per second. A
transport or protocol failure closes the serial client and triggers a clean
reconnect. UART/MSP failures produce a dedicated dashboard alert with the
serial port, raw error, wiring and power checks, and automatic-retry status.
All MSP API routes are read-only.

The camera backend runs one `rpicam-vid` MJPEG process while one or more
browsers consume `/api/camera/stream`. Capture stops after the last subscriber
disconnects. Defaults are 1280x720, 15 FPS, JPEG quality 70, camera index 0,
180-degree rotation for the current upside-down mounting, and continuous
autofocus. They can be changed without rebuilding:

```bash
UAV_CAMERA_INDEX=0 \
UAV_CAMERA_WIDTH=1280 \
UAV_CAMERA_HEIGHT=720 \
UAV_CAMERA_FRAMERATE=15 \
UAV_CAMERA_JPEG_QUALITY=70 \
UAV_CAMERA_ROTATION=180 \
uv run --locked uvicorn uav_dashboard.api:app --host 0.0.0.0 --port 8000
```

Each camera JPEG is decoded on a worker thread and passed to the reusable
square detector. Frames with detections are re-encoded with green contours and
center points before being sent to every browser; frames without detections are
forwarded unchanged. Detection remains visual-only and has no connection to
motor output or flight control.

The same shared camera process publishes the largest detection through
`/api/camera/detection/ws`. The backend classifies its center into an image
quadrant and the dashboard displays a simulation-only `M1`-`M4` decision. This
mapping is only a UI placeholder and is not a Betaflight motor map. A missed
detection is held briefly to avoid flicker, then becomes stale and clears the
simulated output. An independent watchdog applies the same rule if camera
frames stop completely. The default stale timeout is 0.4 seconds and can be
changed with `UAV_DETECTION_STALE_TIMEOUT`.

The `Vision response dry run` panel exercises the next decision layer without
writing to the flight controller. It is disabled by default and requires an
explicit dashboard action plus healthy UART telemetry. One fresh target starts
one 200 ms simulated pulse, then a one-second cooldown; the target must clear
before another pulse. Holding never starts a pulse, while stale detection,
camera failure, UART failure, disabling, or camera shutdown clears the selected
output. Camera shutdown leaves the dry run faulted so the operator must
explicitly disable and re-enable it after reconnecting. The panel shows the
current state and a separate decision journal.

The dry-run timings can be adjusted without rebuilding:

```bash
UAV_SIMULATED_PULSE_DURATION=0.2 \
UAV_SIMULATED_PULSE_COOLDOWN=1.0 \
uv run --locked uvicorn uav_dashboard.api:app --host 0.0.0.0 --port 8000
```

These values control simulation only. There is still no MSP motor command,
ARM command, RC injection, or physical output path.

The default stream interval is 0.1 seconds. It can be changed when starting the
backend, with a minimum of 0.05 seconds:

```bash
UAV_TELEMETRY_INTERVAL=0.2 uv run --locked uvicorn \
  uav_dashboard.api:app --host 0.0.0.0 --port 8000
```

Slow telemetry and reconnect timing are independently configurable. Their
defaults are both one second:

```bash
UAV_SLOW_TELEMETRY_INTERVAL=2.0 \
UAV_TELEMETRY_RECONNECT_INTERVAL=1.0 \
uv run --locked uvicorn uav_dashboard.api:app --host 0.0.0.0 --port 8000
```

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

The dashboard, read-only telemetry, and live Camera Module 3 stream have passed
Raspberry Pi hardware smoke tests. Live square detection, quadrant decisions,
and the fail-closed dry-run pulse state machine are implemented and still need
a Raspberry Pi smoke test as one complete path. After that test, the camera
orientation and physical motor numbering must be verified before a separate
disabled-by-default bench adapter may issue bounded single-motor test pulses
with propellers removed. Automatic ARM, RC injection, configuration writes,
and flight control remain outside the current scope.

