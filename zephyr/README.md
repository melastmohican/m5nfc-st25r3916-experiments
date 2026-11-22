# zephyr: USB NFC reader and serial tool on a Feather RP2350

C++ firmware for [Zephyr](https://zephyrproject.org/) on an Adafruit Feather RP2350 and an M5Stack NFC Universal Unit (ST25R3916). Over the Feather's USB cable it does two things at once, like the other Feather folders:

- **A USB smart-card (CCID) reader.** The Mac sees a standard PC/SC reader. Zephyr has no CCID class, so `src/ccid_class.c` is a small custom one.
- **A serial command line** (`chip`, `detect`, `dump`, `read-ndef`, `write-ndef`, `write-page`, `ident`, `desfire`, `cycles`, `emulate`, `emu-dump`).

```
Mac --USB: CCID reader + serial--> Feather RP2350 (Zephyr) --I2C (Qwiic)--> M5Stack NFC unit --13.56 MHz--> NTAG215 tag
```

## Status

| Piece | State |
|---|---|
| Chip, NFC-A, NTAG215, NDEF, `ident`, ISO-DEP, `desfire`, serial CLI | Works. Same source as `arduino/` (copied, see below). |
| USB CCID reader, PC/SC APDUs | Works on the Mac. macOS lists the reader with the standard ATR; `FF CA`, `FF B0`, the `ident` APDUs and the lock-page refusal (`69 82`) answer as in `arduino/`; DESFire pass-through gives ATR `3B 81 80 01 80 80`, `GET_VERSION` with its two additional frames, and the 3 application IDs; insert and remove events arrive. |
| Tag emulation (`emulate [S] [t2|t4|sniff]`, `emu-dump`) | Works with an iPhone and NFC Tools (21 Nov 2026). `t2`: identified as an NTAG215, wrote a text record, `emu-dump` decoded it; 29 frames answered. `t4`: the phone wrote a text record to the Type 4 NDEF file (4 updates) and read it back; 74 frames answered. `sniff` not run. |
| Serial checks on hardware | `chip`, `detect`, `dump` (all 135 pages), `read-ndef`, `write-ndef` (9 pages, read back as written), `write-page` (page 100 written and verified, page 3 refused), `ident`, `cycles 50` (50/50, avg 161 ms) on an NTAG215; `detect` and `desfire` on an ORCA DESFire card. |
| PC/SC writes and long reads | `FF D6` writes page 101 and `FF B0` reads it back; `FF D6` to page 3 is refused with `69 82`; `FF B0` with `Le` = 255 returns 255 bytes. |
| Not checked on hardware yet | A phone reading the NDEF, and running with the reader and the serial port both busy at once. |
| Host tests | 3 programs pass: CCID message layer, `ident`, ISO-DEP framing (Annex B). The Zephyr-only files (`main.cpp`, `ccid_class.c`, `CcidService.cpp`, `ZephyrTransport.h`, `Io.cpp`) have no host test. |
| Passport / NFC-B | Not attempted (same as every other folder). |

## What you need

- Adafruit Feather RP2350 (the PSRAM/HSTX model) and a USB cable
- [M5Stack NFC Universal Unit (ST25R3916)](https://shop.m5stack.com/products/nfc-universal-unit-st25r3916), SKU U216, and a Qwiic / STEMMA QT cable
- NTAG215 tags; a DESFire card for `desfire`
- On the Mac: `west`, `ninja`, `dtc`, and the Zephyr SDK (ARM toolchain only), as below. A C++ compiler for the host tests.

PlatformIO cannot build this: its `raspberrypi` platform has no Zephyr framework, so this folder uses `west`.

## Wiring

Same as `arduino/`: plug the unit into the Feather's Qwiic port. That is I2C1 with SDA on GP2 and SCL on GP3, 400 kHz, set in [`app.overlay`](app.overlay).

## One-time setup (about 15 minutes plus downloads, about 7 GB)

```
uv tool install west
brew install ninja dtc
cd ~/Src && west init --mr v4.5.0 zephyrproject
cd zephyrproject && west update --narrow -o=--depth=1 -n
uv venv .venv && uv pip install --python .venv/bin/python -r zephyr/scripts/requirements.txt west
. .venv/bin/activate && west zephyr-export
west sdk install --toolchains arm-zephyr-eabi --install-dir ~/zephyr-sdk
```

Tested with Zephyr v4.5.0 and SDK 1.0.1. `run.sh` looks for the workspace in `~/Src/zephyrproject`; set `ZEPHYR_WS` to use another path.

## Build and flash

```
./run.sh           # builds build/zephyr/zephyr.uf2
```

Hold BOOTSEL while plugging the Feather in, then copy `build/zephyr/zephyr.uf2` to the `RP2350` drive. The board reboots into the firmware by itself.

The board target is `rpi_pico2/rp2350a/m33`. Zephyr has no Feather RP2350 board, but the chip is the same; the overlay moves I2C1 to the Qwiic pins.

The red LED (GP7) shows progress: 3 quick blinks at boot, then steady on once USB is up. A slow blink forever means USB initialisation failed; 2 blinks and a pause means enabling the USB device failed.

## Use it

### As a PC/SC reader

After power-up the Mac lists a reader named `Experiment Experiment NFC (Zephyr)` (the product string repeats the manufacturer). It starts answering after the 3-second start-up delay:

```
system_profiler SPSmartCardsDataType      # lists the reader, with the card's ATR
uv run --with pyscard python ../micropython/pcsc_check.py
uv run --with pyscard python ../micropython/pcsc_monitor.py    # insert and remove events
```

The APDUs, ATRs and status words are the same as in [`arduino/`](../arduino/README.md#as-a-pcsc-reader) (`FF CA`, `FF B0`, `FF D6`, `FF 60/3C/39`, anything else passed to an ISO-DEP card).

### As a serial tool

The Feather also shows up as a serial port (`/dev/cu.usbmodem*`). Open it at any baud rate:

```
screen /dev/cu.usbmodem* 115200
```

The commands and their output are the same as in [`arduino/`](../arduino/README.md): `chip`, `detect`, `dump`, `read-ndef`, `write-ndef`, `write-page`, `ident`, `desfire`, `cycles`, `emulate`, `emu-dump`, `help`. Every command ends with `ok` or `error: <reason>`. `ident` and `cycles` are for NTAG tags; a DESFire card refuses them.

## Tests

```
./test/run.sh
```

Needs only a C++ compiler. It builds and runs the three test programs against the sources in `src/`. There is no test for the Zephyr-only files (`main.cpp`, `ZephyrTransport.h`, `Io.cpp`); those are checked on the hardware.

## How it is built

- The chip, tag and CLI code in `src/` was copied from `arduino/src` and then changed in three places: `Stream` became `Io` (`Io.h`), `delay` and `millis` became `k_msleep` and `k_uptime_get_32`. There is no dependency on `arduino/`, and the copies are free to diverge, so a fix in one folder must be made in the other by hand.
- `ZephyrTransport.h` implements the `Transport` interface with `i2c_write` and `i2c_write_read`.
- `main.cpp` brings up the USB device (the new `usbd` stack, with the setup helper from the Zephyr samples), enables it with `usbd_enable()` (this board cannot detect VBUS, so the application has to), exposes a CDC-ACM serial port, and runs the CLI and the CCID service in one loop.
- `ccid_class.c` is the CCID class: interface class 0x0B, the 54-byte class descriptor, bulk OUT, bulk IN and an interrupt IN endpoint for slot-change notifications. The stack renumbers interfaces and endpoints, so the class reads the addresses back from its own descriptor. The USB callbacks only record that a command arrived; `CcidService.cpp` runs the NFC (I2C) work in the main thread and sends the reply, with a zero-length packet after a reply whose length is a multiple of 64.
- `CcidProto` and `NfcBackend` (copied from `arduino/src`) are the message layer and the NFC side of the reader.
- `hw-flow-control` on the CDC node in `app.overlay` is required. Without it the driver drops bytes when its buffer fills, and long output such as `dump` loses lines.

## Lessons from the port

- Zephyr only calls a class's `enable()` for the default alternate setting; `update()` is for explicit `SET_INTERFACE` requests. A class that waits for `update()` never opens.
- A descriptor array that is declared but not filled in has `bLength` 0, which the stack reads as the end of the list. The host then sees the interface without its class descriptor and endpoints, and macOS never lists the reader.
- The CDC-ACM node needs `hw-flow-control`, or the driver drops bytes when its buffer fills.

## Next

- Optional: [`vouch-opensource/zephyr-st25r`](https://github.com/vouch-opensource/zephyr-st25r) (ST's RFAL as a Zephyr module) for NFC-B/F/V. Its driver needs the chip's IRQ pin, which the unit does not expose on Qwiic, and it was last updated in 2023.
