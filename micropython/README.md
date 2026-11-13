# Phase B: MicroPython + USB CCID

Feather RP2350 (STEMMA QT) -> M5Stack NFC Unit. The Feather presents itself to the Mac as a CCID smart-card reader, so PC/SC tools work with no custom Mac software.

Status: gate test passed; driver ported and verified on the Feather (chip ID, detect, NDEF read, page reads). CCID bulk messages not started. See `../.agents/notes/PLAN.md` for the plan.

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

## Driver port (done)
`nfc/` is a copy of `../python/nfc/` with `dataclasses`, `typing.Protocol` and `from __future__` removed, a `time.ticks_ms` clock, and `transport.py` using `machine.I2C` (Qwiic port = I2C1, SDA GP2, SCL GP3). Keep fixes in sync by hand with the Python version.

    mpremote cp -r nfc :
    mpremote run nfc_check.py     # chip ID, UID, NDEF, first pages

The unit works from Qwiic's 3.3V on its 5V pin. Read range at 3.3V is untested beyond a tag laid on the unit.
