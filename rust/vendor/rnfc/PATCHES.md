# Local patch to embassy-rs/rnfc (rev 4711debbad00b1da7862238ce5d02be7b0a7709e)

Copied from https://github.com/embassy-rs/rnfc (MIT OR Apache-2.0, see LICENSE-*).
One change, so Type 2 WRITE works: the tag answers with a 4-bit ACK (0xA) without CRC, which
the upstream low-level reader reports as a CRC/parity/length error.

- `rnfc-traits`: new `Frame::StandardShortRx { timeout_1fc }`.
- `rnfc-st25r39/src/iso14443a.rs`: for that frame, receive without CRC check/strip and ignore the
  CRC and parity flags and the "last byte missing parity" status, but accept the reply only if it
  is exactly one byte ending in an incomplete byte (FIFO status `fifo_lb` != 0). It returns the
  bit count (4 for an ACK/NAK) with the value in `rx[0]`. This is the same rule ST's RFAL applies
  (`rfalErrorHandling`, "T2T 4 bit ACK / NAK" in stm32duino/ST25R3916). Other framing errors still fail.

Wired in through `[patch]` in `../../Cargo.toml`.
