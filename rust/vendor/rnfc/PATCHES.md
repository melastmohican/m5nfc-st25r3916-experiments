# Local patches to embassy-rs/rnfc (rev 4711debbad00b1da7862238ce5d02be7b0a7709e)

Copied from https://github.com/embassy-rs/rnfc (MIT OR Apache-2.0, see LICENSE-*).
Three changes.

## 1. Type 2 WRITE

So Type 2 WRITE works: the tag answers with a 4-bit ACK (0xA) without CRC, which
the upstream low-level reader reports as a CRC/parity/length error.

- `rnfc-traits`: new `Frame::StandardShortRx { timeout_1fc }`.
- `rnfc-st25r39/src/iso14443a.rs`: for that frame, receive without CRC check/strip and ignore the
  CRC and parity flags and the "last byte missing parity" status, but accept the reply only if it
  is exactly one byte ending in an incomplete byte (FIFO status `fifo_lb` != 0). It returns the
  bit count (4 for an ACK/NAK) with the value in `rx[0]`. This is the same rule ST's RFAL applies
  (`rfalErrorHandling`, "T2T 4 bit ACK / NAK" in stm32duino/ST25R3916). Other framing errors still fail.

Wired in through `[patch]` in `../../Cargo.toml`.

## 2. Expose the ATS

- `rnfc/src/iso_dep.rs`: `IsoDepA` keeps the ATS the card sent for RATS and returns it from the new
  `ats()` method (it was a local variable and dropped). Used by the `desfire` command to show it.

## 3. Direct chip access for tag emulation

`rnfc-st25r39` has no target (listener) mode. The listener in `../../src/emu/listener.rs` drives the
chip itself through the `Interface` trait, so:

- `Raw::interface()` hands out the bus interface (`lib.rs`).
- `Interface::read_regs` reads consecutive registers; the I2C implementation does it in one
  auto-incrementing transfer for register space A, so one poll reads the four interrupt registers
  in one transfer instead of four.
- `Interface::write_pt_memory_a` loads the passive target memory (opcode 0xA0), implemented for I2C
  and SPI.

The driver does not know about the changes the listener makes to the chip. Call `Raw::field_on`
before and `Raw::field_off` after: `field_on` runs the driver's own mode set-up again.
