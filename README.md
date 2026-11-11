# UAV vision prototype

An educational Python project that detects a black square in a static image.
The Raspberry Pi and live camera will be added in the next increment, after the
static-image flow is understood and verified.

## Project structure

```text
.
├── uav/
│   ├── src/
│   │   └── uav/
│   │       ├── __init__.py
│   │       ├── capture.py           # capture one local webcam frame
│   │       ├── square_detection.py  # detection algorithm and result models
│   │       └── cli.py               # file I/O and CLI arguments
│   ├── tests/
│   │   ├── test_capture.py
│   │   ├── test_cli.py
│   │   └── test_square_detection.py
│   └── artifacts/                  # local input and generated images
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
```

`uv` creates `.venv` automatically. `pyproject.toml` declares the allowed
dependency ranges; `uv.lock` stores the exact resolved versions and must be
committed to Git.

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

## Run the checks

```bash
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
uv run --locked pytest
```

After changing dependencies in `pyproject.toml`, deliberately refresh the lock
file with `uv lock` and commit both files.

## Next increment

After the static-image code is understood and debugged, add one Raspberry Pi
camera adapter that provides frames to the same `detect_squares()` function.
The detection algorithm must remain independent from Picamera2.

