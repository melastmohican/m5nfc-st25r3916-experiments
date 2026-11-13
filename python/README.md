# Phase A: Python + FT232H

Mac -> USB -> Adafruit FT232H (pyftdi, I2C) -> M5Stack NFC Unit (ST25R3916, 0x50) -> NTAG215.

## Wiring
| FT232H | NFC unit (Grove) |
|---|---|
| D0 | SCL (white) |
| D1 + D2 tied together | SDA (yellow) |
| GND | GND (black) |
| USB 5V pin | 5V (red) |

Check the unit's I2C pull-up voltage first (see `../.agents/notes/PLAN.md`).

## Setup
```
brew install libusb
uv run python -m nfc.scan          # expect 0x50
uv run python -m nfc.aht30_check   # sanity check with an AHT30 at 0x38
```

## Use
```
uv run python -m nfc.cli chip
uv run python -m nfc.cli detect
uv run python -m nfc.cli dump
uv run python -m nfc.cli read-ndef
uv run python -m nfc.cli write-ndef --text "hello" --uri https://example.com
uv run python -m nfc.cli write-page 10 DEADBEEF
```
Lock, config and capability pages need `--force` and are one-way.

## Tests
```
uv run --with pytest python -m pytest -q
```
Runs against a software fake chip (`tests/fakechip.py`); not yet run on real NFC hardware.
