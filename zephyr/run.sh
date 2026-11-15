#!/bin/sh
# Build the Zephyr firmware. Usage: ./run.sh [build|flash-uf2]
# Needs the workspace at ~/Src/zephyrproject (west init, west update) and the SDK in ~/zephyr-sdk.
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
WS=${ZEPHYR_WS:-$HOME/Src/zephyrproject}
. "$WS/.venv/bin/activate"
cd "$WS"
west build -b rpi_pico2/rp2350a/m33 "$HERE" -d "$HERE/build" -- -DDTC_OVERLAY_FILE="$HERE/app.overlay"
echo "uf2: $HERE/build/zephyr/zephyr.uf2 (hold BOOTSEL, plug in, copy to the RP2350 drive)"
