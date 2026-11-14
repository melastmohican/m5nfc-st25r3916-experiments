# Phase C: Arduino-Pico + PlatformIO + USB CCID

Same goal as Phase B (Feather RP2350 as a CCID reader for the NFC unit), written in C++ with the Arduino-Pico core under PlatformIO.

Status: full driver ported and verified on hardware, with a serial CLI that mirrors the Python one (50/50 reliability cycles). CCID not started. See `../.agents/notes/PLAN.md` for the plan.

## Notes
- TinyUSB in the Pico SDK has no mainline CCID class; add a custom class driver through the app-driver hook.
- References: RevK's TinyUSB CCID driver, EspUsbDevice SmartCardReader example.
- Port register logic from `../python/nfc/st25r3916.py`; M5's official library targets M5Unified.
- Same gate test as Phase B: `pcsc_scan` must list the device.

## Build and flash
Board: `adafruit_feather_rp2350_hstx` (the Feather RP2350 with PSRAM; CircuitPython calls it `adafruit_feather_rp2350`), Arduino-Pico core via the maxgerhardt PlatformIO platform.

    pio run                  # build (first build downloads the toolchain, about 2 minutes)
    pio run -t upload        # needs the board in the bootloader (RP2350 drive)

Coming from MicroPython: `mpremote exec "import machine; machine.bootloader()"`, wait for the `RP2350` drive, then upload. Once Arduino firmware is on the board, Arduino-Pico's 1200-baud touch reboots it for uploads.
Qwiic I2C is I2C1: SDA GP2, SCL GP3.

## Source layout (`src/`)
- `St25r3916.h/.cpp`: register/FIFO/command layer, init, transceive. Talks to a `Transport`, not to `Wire`, and returns status codes (no exceptions).
- `Nfca.h/.cpp`: CRC_A, WUPA/anticollision/select (4- and 7-byte UIDs, field reset and retry for a stuck tag), Type 2 READ and WRITE.
- `WireTransport.h`: Arduino `Wire` transport. On this board `Wire` is I2C1 on GP2/GP3 (Qwiic). Do not use `Wire1` (I2C0, unconnected pin): `Wire1.setSDA(2)` panics and hangs the board; recover with BOOTSEL held while plugging in.
- `Ntag215.h/.cpp`: 135-page model, dump, page write with lock/config guard and read-back verify, NDEF read/write (identical pages are skipped).
- `Ndef.h/.cpp`: text and URI records, TLV wrap, no heap (caller buffers).
- `Cli.h/.cpp`: line-based serial CLI, same commands and output as `python -m nfc.cli`, plus `cycles`.
- `main.cpp`: starts the chip and the CLI on USB serial.
Ports of `python/nfc`: keep fixes in sync by hand.

## Serial CLI
Open the USB serial port at 115200 (the board waits 3 s after boot). One command per line; every command ends with `ok` or `error: ...`.

    chip | detect | dump | read-ndef
    write-ndef [--text "hello"] [--lang en] [--uri https://example.com]
    write-page <page> <8 hex digits> [--force]
    cycles [N]        # N x (select + full dump, compared with the first); default 50
    help

Lock, config and capability pages need `--force` and are one-way (same as the Python CLI).
