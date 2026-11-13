# Phase B: MicroPython + USB CCID

Feather RP2350 (STEMMA QT) -> M5Stack NFC Unit. The Feather presents itself to the Mac as a CCID smart-card reader, so PC/SC tools work with no custom Mac software.

Status: gate test passed (stub CCID reader enumerates on macOS). Driver port not started. See `../.agents/notes/PLAN.md` for the plan.

## Gate test (passed)
Board: Feather RP2350, MicroPython v1.29.0 (`ADAFRUIT_FEATHER_RP2350` UF2), plus `mpremote mip install usb-device`.

    mpremote cp ccid_stub.py :ccid_stub.py
    mpremote exec "import ccid_stub"     # USB re-enumerates; an mpremote 'Device not configured' error here is expected
    uv run --with pyscard python host_check.py    # expect: Experiment NFC CCID stub

To run it at every boot, copy it to the board as `main.py`.

## Wiring
Qwiic/STEMMA QT for SDA/SCL/GND (3.3V). The NFC unit's 5V comes from the Feather's USB pin, not the connector.

## Plan
1. Gate test (stub CCID).
2. Port the driver from `../python/nfc/` (replace transport; no `dataclasses` or `typing.Protocol`).
3. CCID bulk messages and pseudo-APDU mapping (`FF CA`, `FF B0`, `FF D6`).
4. Test with `pcsc_scan` and pyscard.
