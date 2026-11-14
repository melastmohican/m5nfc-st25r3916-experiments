# python: NFC tag reader/writer for the Mac

A command-line tool that reads and writes NTAG215 tags from a Mac. An Adafruit FT232H breakout acts as the I2C master (through `pyftdi`) and talks to an M5Stack NFC Universal Unit (ST25R3916, I2C address 0x50).

```
Mac --USB--> FT232H --I2C--> M5Stack NFC unit --13.56 MHz--> NTAG215 tag
```

## What you need

- [Adafruit FT232H breakout](https://shop.adafruit.com/products/2266) and a USB cable
- [M5Stack NFC Universal Unit (ST25R3916)](https://shop.m5stack.com/products/nfc-universal-unit-st25r3916), SKU U216, with its Grove cable
- NTAG215 tags (stickers or cards)
- macOS with [uv](https://docs.astral.sh/uv/) and libusb: `brew install libusb`

## Wiring

| FT232H | NFC unit (Grove cable) |
|---|---|
| D0 | SCL (white) |
| D1 and D2 tied together | SDA (yellow) |
| GND | GND (black) |
| USB 5V pin | 5V (red) |

D1 and D2 are tied because the FT232H's I2C data line uses two pins: D1 drives data out, D2 reads it back.

The unit's 5V pin feeds an on-board 3.3V regulator, and its I2C pull-ups go to that 3.3V rail. The FT232H's 3.3V signals therefore need no level shifter.

## Check the setup

```
uv run python -m nfc.scan          # expect: OK: NFC unit at 0x50
uv run python -m nfc.cli chip      # expect: ST25R3916 present: type=0x05 revision=2
```

If `scan` finds nothing, check the wiring. `uv run python -m nfc.aht30_check` tests the FT232H's I2C path on its own, using an AHT30 temperature sensor at address 0x38.

## Commands

Run from this folder with a tag lying flat on the unit:

```
uv run python -m nfc.cli <command>
```

| Command | What it does |
|---|---|
| `chip` | Checks that the ST25R3916 answers and prints its type and revision. |
| `detect` | Finds a card and prints its UID, ATQA and SAK, and whether it is an NTAG215. |
| `dump` | Prints all 135 pages (4 bytes each) as hex and ASCII. |
| `read-ndef` | Decodes the NDEF message: text and URI records. |
| `write-ndef [--text T] [--lang L] [--uri U]` | Writes a text and/or URI record, replacing the current message. Pages that already match are skipped. |
| `write-page <page> <8 hex digits> [--force]` | Writes one 4-byte page and reads it back to verify. |

Options before the command: `--freq` sets the I2C clock in Hz (default 100000) and `--url` sets the pyftdi device URL (default `ftdi://ftdi:232h/1`).

```
$ uv run python -m nfc.cli detect
UID:  04:64:31:66:D3:2A:81
ATQA: 4400  SAK: 0x00
Type: NTAG215

$ uv run python -m nfc.cli write-ndef --text "hello" --uri https://example.com
wrote 8 page(s)

$ uv run python -m nfc.cli read-ndef
text[en]: hello
uri: https://example.com
```

Errors print as `error: ...` on stderr and the exit status is 1.

### Which pages can be written

User memory is pages 4 to 129 (504 bytes). Writes outside that range, such as the UID, lock bytes, capability container (page 3) and configuration pages, are refused unless you pass `--force`. Their bits are one-way: a lock bit cannot be cleared again. Use a spare tag when you experiment with them.

## Supported tags and limits

- Reading and writing work for NTAG215 and other Type 2 tags with 4- or 7-byte UIDs. Only NTAG215 has been tested.
- `detect` identifies any ISO 14443-A card by UID, ATQA and SAK, but cannot read the content of other card types. DESFire cards (SAK `0x20`, for example an ORCA card) are detected only: the ISO-DEP layer they need is not implemented.
- A full 135-page dump takes about 7 seconds, because every I2C transfer goes through USB.

## How it works

| File | Role |
|---|---|
| `nfc/transport.py` | `FtdiTransport`: I2C writes and write-then-read exchanges through pyftdi. |
| `nfc/st25r3916.py` | The chip driver: registers, FIFO, direct commands, initialisation, field control, `transceive`. |
| `nfc/nfca.py` | ISO 14443-A: CRC_A, WUPA, anticollision, select, Type 2 READ and WRITE. |
| `nfc/ntag215.py` | The tag's memory model: 135 pages, write guards, NDEF read and write. |
| `nfc/ndef.py` | NDEF text and URI records and the TLV wrapper. |
| `nfc/cli.py` | The commands above. |
| `nfc/scan.py`, `nfc/aht30_check.py` | Setup checks. |

Behaviour of the chip worth knowing:

- After a checked receive, the chip leaves the CRC_A bytes in its FIFO. `transceive` strips them when they match.
- A tag's 4-bit write acknowledgement trips the chip's CRC and parity flags. The write path ignores those flags for that one frame.
- A tag that is already selected ignores WUPA until the field is power-cycled. `select_card` resets the field and retries once when WUPA gets no answer, so repeated selects work.
- The Grove connector has no interrupt pin, so the driver polls the chip over I2C and uses the chip's own no-response timer to end waits.

## Tests

```
uv run --with pytest python -m pytest -q
```

13 tests run the whole stack against a software fake chip (`tests/fakechip.py`) and need no hardware.

## Same functionality elsewhere in this repo

[`../micropython`](../micropython) and [`../arduino`](../arduino) turn a Feather RP2350 and the same NFC unit into a USB smart-card reader that any PC/SC tool on the Mac can use.
