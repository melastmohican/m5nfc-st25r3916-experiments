# rust: USB NFC reader and serial tool on a Feather RP2350, in Rust

Rust firmware ([Embassy](https://embassy.dev/)) for an Adafruit Feather RP2350 and an M5Stack NFC Universal Unit (ST25R3916). Same two functions as [`../arduino`](../arduino) and [`../tinygo`](../tinygo), over the Feather's one USB cable:

- **A USB smart-card (CCID) reader.** The Mac sees a standard PC/SC reader, so `pyscard`, OpenSC and `pcsctest` work with it.
- **A serial command line** (`chip`, `detect`, `dump`, `read-ndef`, `write-ndef`, `write-page`, `cycles`, `emulate`, `emu-dump`).

```
Mac --USB: CCID reader + serial--> Feather RP2350 --I2C (Qwiic)--> M5Stack NFC unit --13.56 MHz--> NTAG215 tag
```

Unlike the other variants, the chip driver is not ported: it comes from existing crates.

## Crates used and not used

| Need | Crate | Notes |
|---|---|---|
| ST25R3916 driver over I2C | `rnfc-st25r39` ([embassy-rs/rnfc](https://github.com/embassy-rs/rnfc), git) | Has an `I2cInterface`. Polls the IRQ registers, so the unit's missing IRQ line is not a problem (a dummy pin is passed in). Strips the CRC the chip leaves in the FIFO. |
| ISO 14443-A select, 4/7/10-byte UIDs | `rnfc` (same repo) | `Poller::select_any` |
| HAL, I2C, timer, executor | `embassy-rp` 0.10, `embassy-executor`, `embassy-time` | |
| USB device stack, CDC serial | `embassy-usb` 0.6 | |
| CCID class, NDEF, Type 2 READ/WRITE, APDU backend, CLI | own code, small | see below |

Considered and not used:

- `usbd-ccid` (Trussed): written for `usb-device`, and it has no interrupt endpoint, so no card insert and remove events.
- `ndef`, `nfc-forum-tags`, `ntag21x`, `iso14443` (Foundation Devices): licensed GPL-3.0-or-later, which conflicts with this repo's MIT license. `ndef` also has no URI record type. Their traits are synchronous, which would block the USB task during RF transactions.
- `ndef-rs` (MIT): needs `std`.
- `st25r3916-rfal`: a wrapper around ST's C library, aimed at ESP-IDF.

### Local patch to rnfc

`vendor/rnfc/` is a copy of three crates from `embassy-rs/rnfc` (rev `4711deb`, MIT or Apache-2.0) with one change, wired in through `[patch]` in `Cargo.toml`. A Type 2 WRITE is answered with a 4-bit ACK without CRC. The upstream low-level reader reports that as a CRC, parity or length error, and its tag wrapper panics on a partial byte. The patch adds a `Frame::StandardShortRx` that ignores the CRC and parity flags but accepts the reply only if it is exactly one byte ending in an incomplete byte, the same rule as ST's own RFAL library (`rfalErrorHandling` in [stm32duino/ST25R3916](https://github.com/stm32duino/ST25R3916)). Details: [`vendor/rnfc/PATCHES.md`](vendor/rnfc/PATCHES.md). Reads need no patch. A NAK reply is reported with its meaning (NTAG21x datasheet, Table 23: invalid page, parity/CRC error, authentication counter overflow, EEPROM write error); the NAK path itself is covered only by a host test, not yet seen on hardware.

## What you need

- Adafruit Feather RP2350 (PSRAM/HSTX model), a USB cable, a Qwiic cable
- M5Stack NFC Universal Unit (ST25R3916), NTAG215 tags
- Rust with the `thumbv8m.main-none-eabihf` target (`rustup target add thumbv8m.main-none-eabihf`) and [picotool](https://github.com/raspberrypi/picotool) (`brew install picotool`)

Wiring is the same as `../arduino`: the unit goes into the Qwiic port (I2C1, SDA GP2, SCL GP3).

## Build and flash

```
cargo build --release
picotool load -u -v -x -t elf target/thumbv8m.main-none-eabihf/release/m5nfc-rust
```

`picotool load` needs the board in the bootloader. The first time, hold BOOTSEL while plugging the Feather in. After that, the serial command `bootsel` reboots it into the bootloader, so no button is needed. There is no SWD probe on the Feather, so `probe-rs` is not used.

## Use it

### As a PC/SC reader

Same APDUs, status words and ATR as [`../arduino`](../arduino/README.md#as-a-pcsc-reader); the reader is named `Experiment NFC reader (Rust)`.

| APDU | Meaning | Reply |
|---|---|---|
| `FF CA 00 00 <Le>` | UID | UID + `90 00` |
| `FF B0 00 <page> <Le>` | Read `Le` bytes from `page` (`Le` = 0 reads 16), up to 255 | data + `90 00` |
| `FF D6 00 <page> 04 <4 bytes>` | Write one page, then read it back | `90 00` |
| `FF 60 00 00 00` | NTAG `GET_VERSION` | 8 bytes + `90 00` |
| `FF 3C 00 00 00` | NTAG `READ_SIG` | 32 bytes + `90 00` |
| `FF 39 00 02 00` | NTAG `READ_CNT` | 3 bytes + `90 00`, or `6A 81` if the tag refuses |

**ISO-DEP cards (DESFire).** If the card's SAK says it speaks ISO 14443-4, power-on also runs RATS and answers with an ATR built from the card's ATS (for a DESFire EV1: `3B 81 80 01 80 80`). Any APDU is then passed to the card, so DESFire native commands wrapped as `90 INS 00 00 00` work, including the "additional frame" chain across separate commands:

```
opensc-tool -s 9060000000 -s 90AF000000 -s 90AF000000    # GET_VERSION in three frames
```

The card stays activated between APDUs, in a task of its own (`src/isodep.rs`). While it is open that task holds the chip, so the serial command line waits, and the session closes by itself after 20 s without commands. While a session is open the reader does not probe for the card, so a removed card is noticed on the next command instead of through a slot-change event. Only unauthenticated commands were tried; there is no key handling.

Check it with the scripts in [`../micropython`](../micropython) (`uv run --with pyscard python <script>`): `pcsc_check.py`, `pcsc_monitor.py`, `pcsc_ndef_roundtrip.py`.

### As a serial command line

Open the Feather's USB serial port at 115200 baud. The board waits 3 seconds after power-up. Commands are the same as [`../arduino`](../arduino/README.md#as-a-serial-command-line), plus `ident`, `desfire` and `bootsel`; each ends with `ok` or `error: ...`. Pages outside user memory (4 to 129) are refused unless `--force`; those bits are one-way.

### Identifying an NTAG (`ident`)

`ident` sends `GET_VERSION`, `READ_SIG` and `READ_CNT` (NTAG21x datasheet 10.1, 10.8 and 10.6), each in its own field-on session so that a NAK from one does not disturb the next. It prints the version bytes, the type, the signature and the counter (`not readable (NAK)` when the tag refuses). A tag that stays silent after a command is selected again: if it still answers, the command is reported as refused (`6A 81` over PC/SC), otherwise the tag is absent. The same commands are the `FF 60`, `FF 3C` and `FF 39` APDUs above. The signature is shown as the tag sends it; checking it against NXP's public key is not done.

### Reading a DESFire card (`desfire`)

`desfire` runs ISO-DEP (RATS, ATS, framing, chaining) on the Rust-only `rnfc` ISO-DEP layer and asks the card for what it reveals without keys: its ATS, `GET_VERSION` (hardware and software version, storage size, UID, production date) and `GET_APPLICATION_IDS`. Tried on an ORCA transit card (MIFARE DESFire EV1 4K): the UID, ATQA, SAK and historical byte match what NFC Tools on an iPhone shows, the version reply reports a 4096-byte DESFire EV1, and the card lists its application IDs without authentication. File contents need keys that this repo does not have, so none are read. `detect` on such a card prints its UID, ATQA and SAK and points to `desfire`.

## Source layout

| File | Role |
|---|---|
| `src/lib.rs` | Host-testable part, no hardware: |
| `src/ccid.rs` | CCID message layer and the async `Backend` trait. |
| `src/ndef.rs` | NDEF text and URI records, TLV wrapper. |
| `src/tag.rs` | NTAG215 memory model and write guards. |
| `src/parse.rs` | Command-line tokenizer and hex parsing. |
| `src/main.rs` | Clocks, I2C1, USB, tasks. Holds the dummy IRQ pin type. |
| `src/nfc.rs` | One field-on session over `rnfc`: select, Type 2 READ and WRITE, NTAG215 dump, NDEF. |
| `src/backend.rs` | Maps the pseudo-APDUs onto the tag; hands ISO-DEP cards to `isodep.rs`. |
| `src/isodep.rs` | The task that keeps an ISO-DEP card activated between APDUs. |
| `src/class.rs` | The USB CCID class on embassy-usb. |
| `src/cli.rs` | The serial command line. |
| `src/emu/` | Tag emulation models in the host-testable part: an NTAG215 (`type2tag.rs`), an ISO-DEP target (`isodep.rs`), a Type 4 NDEF app and an APDU sniffer (`apps.rs`), and the chip's target-mode state machine (`listener.rs`, which talks to the chip through a small `Chip` trait and is tested on a fake chip). Ported from `../arduino/src`. |
| `src/emulate.rs` | The `emulate [S] [t2|t4|sniff]` and `emu-dump` commands and the adapter from rnfc's `Interface` to the listener. `rnfc-st25r39` has no target mode, so the listener drives the chip directly; see patch 3 in [`vendor/rnfc/PATCHES.md`](vendor/rnfc/PATCHES.md). Checked with an iPhone and NFC Tools on 21 Nov 2026: `t2` was identified as an NTAG215 and took a text record (25 frames answered); `t4` took a text record through the Type 4 NDEF file and the phone read it back (37 frames). `sniff` not run. |

### How it works

- The NFC chip sits behind one async mutex, shared by the serial command line and the CCID reader. A long command such as `cycles` makes PC/SC answers wait.
- Every operation opens its own session: field on, select, work, field off. Dropping the session power-cycles the tag, which also fixes a tag left ACTIVE by an earlier select. The cost is about 25 ms extra per APDU.
- The CCID class is one interface with bulk OUT, bulk IN and an interrupt IN endpoint. The receive loop reassembles a message from the length in its header, replies in 64-byte packets and ends an exact multiple of 64 with a zero-length packet. A second future polls for a card every 300 ms and sends a slot-change notification.
- Blocking I2C is used inside the async tasks. Each transfer takes 1 to 2 ms, which the USB task tolerates.

## Tests

```
cargo test --lib --target aarch64-apple-darwin     # host: CCID layer, NDEF, write guards, parsing (11 tests)
```

On hardware, with a tag on the unit (2026-11-14): `chip`, `detect`, `dump`, NDEF write and read, refused writes to pages 0, 3 and 135, and `cycles 50` give 50 of 50 full dumps at about 167 ms each. Over PC/SC: ATR, UID, page reads, refused lock-page write, NDEF round trip and the card-removed event all pass; a 255-byte read takes about 114 ms.

## Limits

- Type 2 tags are read and written. DESFire cards can be identified with `desfire` (no keys, so no file contents), and unauthenticated DESFire commands can be passed through the PC/SC reader (see above); nothing that needs keys.
- The records written by this variant have not been checked with a phone.
- No fake-chip test for the driver; it comes from `rnfc-st25r39` and is tested only on hardware.
- Depends on git crates (`rnfc*`) pinned to one commit, and on the local patch above.
- Only one firmware can be on the Feather at a time.
