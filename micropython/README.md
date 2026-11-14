# Phase B: MicroPython + USB CCID

Feather RP2350 (STEMMA QT) -> M5Stack NFC Unit. The Feather presents itself to the Mac as a CCID smart-card reader, so PC/SC tools work with no custom Mac software.

Status: gate test passed; driver ported and verified on the Feather (chip ID, detect, NDEF read, page reads). CCID reader works end to end over PC/SC (UID, page read, page write verified from the Mac via pyscard). Live insert/remove events and an NDEF round trip over PC/SC also verified. Installed as main.py and verified from a cold boot. See `../.agents/notes/PLAN.md` for the plan.

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

## CCID reader (working, run from RAM)
Files: `ccid_proto.py` (CCID messages, host-testable), `nfc_backend.py` (pseudo-APDUs -> tag), `ccid_reader.py` (USB glue + main loop), `main.py` (boot window), `run_reader.py` (start now, no main.py).

    uv run --with pytest python -m pytest tests -q       # protocol layer, on the Mac
    mpremote cp ccid_proto.py nfc_backend.py ccid_reader.py run_reader.py :
    mpremote exec "import run_reader"                   # REPL port disappears; mpremote then errors, that is expected
    uv run --with pyscard python pcsc_check.py          # UID, reads, write guard over PC/SC

The reader turns the USB serial REPL off. Unplug and replug the Feather to get the REPL back (nothing runs at boot until `main.py` is installed).
Pseudo-APDUs: `FF CA 00 00 00` UID, `FF B0 00 <page> <Le>` read, `FF D6 00 <page> 04 <data>` write; lock/config pages answer 69 82.
`main.py` starts the reader at boot after a 4 s window; hold BOOTSEL in that window to keep the REPL.

Extra checks (Mac side): `pcsc_monitor.py 45` prints live insert/remove events; `pcsc_ndef_roundtrip.py` writes text+URI records via `FF D6`, reads them back via `FF B0`, then restores the tag.

## Install / recovery
    mpremote cp main.py :          # reader starts ~4 s after power-up
To get the REPL back: during the 4 s boot window `mpremote exec ...` interrupts main.py (verified), or hold BOOTSEL for ~3 s right after plugging in (main.py then skips the reader and the REPL stays up; verified). Do not hold BOOTSEL at the moment of power-up: that enters the ROM bootloader (RP2350 drive) instead. Otherwise replug and retry.
