# Phase B: MicroPython + USB CCID

Feather RP2350 (STEMMA QT) -> M5Stack NFC Unit. The Feather presents itself to the Mac as a CCID smart-card reader, so PC/SC tools work with no custom Mac software.

Status: not started. See `../.agents/notes/PLAN.md` for the plan.

## First step (gate test)
Flash a stub CCID device that only reports "slot present". `pcsc_scan` must list it. If it does not, stop and use Phase C.

## Wiring
Qwiic/STEMMA QT for SDA/SCL/GND (3.3V). The NFC unit's 5V comes from the Feather's USB pin, not the connector.

## Plan
1. Gate test (stub CCID).
2. Port the driver from `../python/nfc/` (replace transport; no `dataclasses` or `typing.Protocol`).
3. CCID bulk messages and pseudo-APDU mapping (`FF CA`, `FF B0`, `FF D6`).
4. Test with `pcsc_scan` and pyscard.
