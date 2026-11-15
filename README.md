# M5Stack NFC unit (ST25R3916): read and write NTAG215 tags

Five ways to read and write NTAG215 NFC tags with an [M5Stack NFC Universal Unit (ST25R3916)](https://shop.m5stack.com/products/nfc-universal-unit-st25r3916), a small I2C module with a 13.56 MHz antenna:

| Folder | Setup | You use it as |
|---|---|---|
| [`python/`](python) | Mac, FT232H breakout, the unit | a command-line tool |
| [`micropython/`](micropython) | Feather RP2350 running MicroPython, the unit | a USB smart-card (PC/SC) reader |
| [`arduino/`](arduino) | Feather RP2350 running Arduino-Pico firmware, the unit | a USB smart-card (PC/SC) reader and a serial command line |
| [`tinygo/`](tinygo) | Feather RP2350 running TinyGo (Go) firmware, the unit | a USB smart-card (PC/SC) reader and a serial command line |
| [`rust/`](rust) | Feather RP2350 running Rust (Embassy) firmware, the unit | a USB smart-card (PC/SC) reader and a serial command line |

```
python/        Mac --USB--> FT232H ----------I2C--+
micropython/   Mac --USB--> Feather RP2350 --I2C--+--> M5Stack NFC unit --13.56 MHz--> NTAG215 tag
arduino/       Mac --USB--> Feather RP2350 --I2C--+
tinygo/        Mac --USB--> Feather RP2350 --I2C--+
rust/          Mac --USB--> Feather RP2350 --I2C--+
```

| `python/`: FT232H and the unit | `micropython/`, `arduino/`, `tinygo/`, `rust/`: Feather RP2350 and the unit |
|---|---|
| <img src="images/ft232h-nfc-unit.jpg" width="300" alt="FT232H breakout connected to the NFC unit"> | <img src="images/feather-nfc-unit.jpg" width="400" alt="Feather RP2350 connected to the NFC unit"> |

The four Feather variants present a standard CCID reader, so the Mac needs no custom driver: `pyscard`, OpenSC, `pcsctest` and `system_profiler` all work with them.

## What all five do

- Find a tag and print its UID, ATQA and SAK, and tell whether it is an NTAG215.
- Dump all 135 pages (4 bytes each).
- Read and write NDEF text and URI records. A phone (iPhone, NFC Tools) reads the records written by `python/` and `arduino/`; the `micropython/`, `tinygo/` and `rust/` records have not been checked with a phone.
- Write single pages, and read each one back to verify it.
- Refuse writes to the UID, lock bytes, capability container and configuration pages unless forced. Those bits are one-way, so use a spare tag if you experiment with them.

## Comparison

| | `python/` | `micropython/` | `arduino/` | `tinygo/` | `rust/` |
|---|---|---|---|---|---|
| Runs on | Mac (Python 3.10+, `uv`) | Feather RP2350, MicroPython | Feather RP2350, Arduino-Pico (C++, PlatformIO) | Feather RP2350, TinyGo (Go) | Feather RP2350, Rust (Embassy, Cargo) |
| Talks to the unit through | FT232H via `pyftdi` | the Feather's Qwiic port (I2C1) | the Feather's Qwiic port (I2C1) | the Feather's Qwiic port (I2C1) | the Feather's Qwiic port (I2C1), driver crate `rnfc-st25r39` |
| Extra hardware | FT232H breakout | Qwiic cable | Qwiic cable | Qwiic cable | Qwiic cable |
| Interface to the Mac | CLI: `python -m nfc.cli` | USB PC/SC reader | USB PC/SC reader and serial CLI | USB PC/SC reader and serial CLI | USB PC/SC reader and serial CLI |
| Commands | `chip`, `detect`, `dump`, `read-ndef`, `write-ndef`, `write-page` | pseudo-APDUs `FF CA` (UID), `FF B0` (read), `FF D6` (write) | the same pseudo-APDUs, and the serial CLI: the `python/` commands plus `cycles` | the same pseudo-APDUs, and the same serial CLI as `arduino/` | the same pseudo-APDUs and serial CLI as `arduino/` |
| Live card insert and remove events | no | yes | yes | yes | yes |
| Full 135-page dump | about 7 s | not measured | about 180 ms | about 184 ms | about 167 ms |
| Longest read per command | n/a | 48 bytes tested | 255 bytes | 255 bytes | 255 bytes |
| Tests | 13, against a software fake chip | 11, for the CCID message layer | host test for the CCID message layer; `cycles` on hardware | host tests for NDEF, CRC and the CCID message layer; `cycles` on hardware | 11 host tests (CCID layer, NDEF, guards, parsing); `cycles` on hardware |
| Reliability run | 50 of 50 dumps | not run | 50 of 50 dumps | 50 of 50 dumps | 50 of 50 dumps |
| Easiest to change | yes: a script on the Mac | yes: Python on the board, no build | needs a PlatformIO build | needs TinyGo and a copy to the BOOTSEL drive | needs Rust, picotool and a first BOOTSEL; the chip driver is a dependency, not code to edit |

How to choose:

- **`python/`** if you want to script tag access from the Mac and have an FT232H.
- **`micropython/`**, **`arduino/`**, **`tinygo/`** or **`rust/`** if you want a USB reader that any PC/SC software can use. `arduino/` is faster, handles longer reads and adds the serial command line; `micropython/` is quicker to modify; `tinygo/` is the same as `arduino/` written in Go; `rust/` is the same again in Rust, and the only one that reuses a published chip driver instead of porting one.
- Only one firmware can be on a Feather at a time. Switching between `micropython/`, `arduino/`, `tinygo/` and `rust/` means flashing the board again; each README says how.

## Supported tags and limits

- Reading and writing work for NTAG215 and other Type 2 tags with 4- or 7-byte UIDs. Only NTAG215 has been tested.
- Other ISO 14443-A cards are detected (UID, ATQA, SAK) but their content cannot be read. DESFire cards, such as an ORCA card, need an ISO-DEP layer that is not implemented.
- The unit is powered from the Feather's Qwiic 3.3V, or from 5V with the FT232H. Read range was tested only with a tag lying on the unit.

## Getting started

Pick a folder and follow its README: it lists the hardware, the wiring, the setup and a check that everything works.

- [`python/README.md`](python/README.md)
- [`micropython/README.md`](micropython/README.md)
- [`arduino/README.md`](arduino/README.md)
- [`tinygo/README.md`](tinygo/README.md)
- [`rust/README.md`](rust/README.md)

## Hardware

- [M5Stack NFC Universal Unit (ST25R3916)](https://shop.m5stack.com/products/nfc-universal-unit-st25r3916): I2C address 0x50, runs from 5V with its own 3.3V regulator and I2C pull-ups
- [Adafruit FT232H breakout](https://shop.adafruit.com/products/2266) for `python/`
- Adafruit Feather RP2350 (the PSRAM/HSTX model) for `micropython/`, `arduino/`, `tinygo/` and `rust/`
- NTAG215 tags

## License

[MIT](LICENSE).
