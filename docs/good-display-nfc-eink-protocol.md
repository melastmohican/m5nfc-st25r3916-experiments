# How Good Display's phone apps talk to an NFC e-paper board

Sources, strongest first:

1. **Traffic.** The iOS app NFC-D3-Pro sending images to the `arduino/` firmware pretending to be the
   board (`emulate 600 sniff`, see `arduino/README.md`), 2026-11-15. Three panel sizes.
2. **The Android app's code.** `NFC-D3-Pro.apk` from Good Display, decompiled with JADX (the app was
   never run). The protocol is in one routine, `writeTag`, and a table class, `data`. The APK and its
   decompiled source are not in this repo.
3. **A Flipper Zero app's source**, [RebornedBrain/nfc_eink](https://github.com/RebornedBrain/nfc_eink)
   (GPL-3.0, so used as a reference only). Points that come only from it are marked *(Flipper)*.

The board is Good Display's [NFC-D1-0154](https://www.good-display.com/product/390.html) (1.54", FM1280
chip) or a sibling. **Nothing here has been run against a real board yet**: the answers in our traffic
are what our fake card gave.

## The whole conversation (Android app)

The card is ISO 14443-4 (ISO-DEP). Every command is a short APDU of class `F0`, always sent as 255 bytes
for image blocks. The Android app opens the connection with a 50 s timeout and then:

| Step | APDU | Meaning |
|---|---|---|
| 1 | `F0 DB 02 00 00` | Handshake, no data. The iOS app sends it too, after SELECT of the NDEF application (an iOS requirement). |
| 2 | `F0 DB 00 00 <len> <entries>` | **Screen set-up** for the chosen panel (see below). The app checks that the answer starts with `90`. |
| 3 | `F0 DA 00 00 03 F0 <res> <chan>` | Panel type; the two bytes repeat the first two bytes of the `A0` entry. |
| 4 | `F0 D2 <index> <block> FA <250 bytes>` | **Image data.** `index` 0 is the black/white plane, 1 the red plane of a three-colour panel; `block` counts from 0. The last block is padded to 250 bytes. A 10 ms pause follows the set-up commands, none follows the image blocks. |
| 5 | `F0 D4 <P1> 80 00` | **Refresh.** `P1` is `05` (power-on delay 5 x 100 ms), or `85` for 4.2" panels and 4-colour panels (bit 7: skip the panel check). `P2 = 80`: return at once. |
| 6 | | The app **waits**, keeping the connection (and so the reader's field) up, because the chip is powered by the field while the panel redraws. It does not poll anything: fixed times of 2 s for two-colour panels, 16 s for three-colour, 12 s (up to 2.9") or 20 s (larger) for four-colour. During the wait `IsoDep.isConnected()` runs every 100 ms; Android answers that with ISO-DEP presence checks (R-blocks). |

The Android app has **no PIN, no authentication and no `F0 DE`**. The iOS app can't send presence checks,
so after the refresh it polls `F0 DE 00 00 01` (`01` busy, `00` idle) until `00`, then closes its session
after a 4 s timeout. Good Display's own protocol document ("FMSC_NFC Protocol", listed as
`DMN037FW_SDK_FMSC_NFC` on [their NFC download page](https://www.good-display.com/companyfile/39/);
"FMSC" is probably Fudan Micro's NFC shelf-label line, but no page spells it out) describes `F0 D2`, `F0 D4`
and `F0 DE` and mentions "PIN authentication" as a precondition; no app we looked at does it.

Four-colour panels send `width x height / 4` bytes (2 bits per pixel) as one plane at index 0. A 3.7"
two-colour panel takes a special path (two planes written one after the other with a different set-up
helper); details not read.

## Image format

1 bit per pixel, rows of `width / 8` bytes (rounded up), most significant bit first, **1 = white, 0 =
black**, in blocks of 250 bytes. A 200 x 200 panel needs 5000 bytes (20 blocks). Images are rotated for
portrait panels. `arduino/tools/gd_image.py` rebuilds the picture from a capture.

## Panel table

The first entry of the set-up is `A0 06 <res> <chan> <width hi lo> <height hi lo>`. `res` is a panel
family (controller), not a size; `chan` is `20` for black/white and `30` for black/white/red. The app
carries 27 variants:

| res | chan | width x height | | res | chan | width x height |
|---|---|---|---|---|---|---|
| 00 | 20 | 122 x 250 (2.13") | | 06 | 20 | 152 x 296 |
| 00 | 20 | 256 x 250 | | 06 | 20 | 304 x 296 |
| 00 | 30 | 122 x 250 | | 06 | 20 | 352 x 264 |
| 01 | 20 | 128 x 296 (2.9") | | 06 | 20 | 368 x 360 |
| 01 | 20 | 256 x 296 | | 06 | 20 | 800 x 300 |
| 01 | 20 | 336 x 384 | | 06 | 30 | 152 x 296 |
| 01 | 30 | 128 x 296 | | 07 | 20 | 88 x 184 (0.97") |
| 03 | 30 | 400 x 300 | | 07 | 20 | 176 x 184 |
| 04 | 20 | 176 x 264 (2.7") | | 07 | 30 | 88 x 184 |
| 04 | 30 | 176 x 264 | | 12 | 20 | 152 x 152 |
| 12 | 20 | 200 x 200 (1.54") | | 12 | 20 | 304 x 152 |
| 12 | 20 | 400 x 200 | | 12 | 30 | 200 x 200 |
| 51 | 20 | 240 x 416 (3.71") | | 51 | 20 | 480 x 416 |
| 51 | 30 | 240 x 416 | | | | |

Seen in traffic: `12` 1.54" (200 x 200), `00` 2.13" (122 x 250, 16 blocks) and `07` 0.97" (88 x 184).
The Flipper source agrees on `00`, `01`, `12` and `51`.

## The set-up entries

The app builds the `F0 DB` payload from named parts, and they read as commands for an SSD1680/1681-type
panel controller. Captured 1.54" set-up, with the part names from the app's code:

```
F0 DB 00 00 62
A0 06 12 20 00 C8 00 C8                       start: panel type and size
A4 01 0C  A5 02 00 0A  A4 01 08  A5 02 00 0A  A4 01 0C  A5 02 00 0A  A4 01 02    reset pin sequence and waits
A1 01 12  A4 01 02                            soft reset, wait
A1 04 01 C7 00 01                             driver output control (gate count)
A1 02 11 01                                   data entry mode
A1 03 44 00 18   A1 05 45 C7 00 00 00         RAM X range (25 bytes) and Y range (200 rows)
A1 02 3C 05                                   border waveform
A1 02 18 80                                   temperature sensor
A1 02 4E 00  A1 03 4F C7 00                   RAM counters
A3 01 24                                      write black/white RAM
A2 02 22 F7  A2 01 20  A4 01 02               update sequence, master activation, wait
A2 02 10 01  A5 02 00 0A                      deep sleep
```

`A1` sends a controller command with data, `A2` is a command after the RAM write, `A3` starts the RAM
write, `A4` and `A5` are pin states, waits and delays (my reading). The payload length and some values
(`44`, `45`, `4F`, the gate count) change with the panel, and the app has one block per size. The
Flipper writer sends **one fixed set-up for every panel** and patches only the `A0` entry, and reports
working with several sizes, so the board probably tolerates a mismatch; not verified.

## Before the set-up *(Flipper)*

The Flipper writer does a longer handshake first: S(DESELECT), SELECT the NDEF application, SELECT the
capability container `E103` and read 15 bytes, SELECT `E104` and read 2 bytes (expects `00 00 90 00`),
then two more DESELECTs with the card re-activated each time. The Android app does none of this, so the
board does not need it. In its model of a board the Flipper answers the refresh with five ISO-DEP S(WTX)
"wait longer" requests before `90 00`; the `arduino/` sniffer does the same, and the iOS app carries on.

## Practical notes for sniffing

- Keep the phone still on the unit until the app shows its final message. A run that sent 8 commands and
  then failed ("tag response error / no response") came from the phone dropping the field mid-transfer.
- After the app has finished, the iPhone's own background tag reader keeps selecting the NDEF application
  and reading the capability container. The sniffer answers those as a real Type 4 tag (class `00`
  commands), otherwise the log fills with repeats.

## What is still unknown

- What a real board answers (status words, any data in the answers to `F0 DB 02 00 00` and `F0 DB`).
  The Android app only looks at the first byte of the set-up answer; it ignores the answers to
  everything else.
- Whether a board needs the set-up at all for a panel it already knows.
- How a board that has been locked behaves (the protocol document lists the status words `64 5A` "key not
  initialized" and `69 82` "security status not satisfied"); the apps never authenticate.

## Related projects

- [RebornedBrain/nfc_eink](https://github.com/RebornedBrain/nfc_eink): Flipper Zero, Good Display and
  Waveshare, writing and emulating (GPL-3.0).
- [CompassSecurity/nfcink](https://github.com/CompassSecurity/nfcink): Python over PC/SC for a
  different, 4-colour 240 x 416 badge from the same command family (`F0 D2` raw upload, `F0 D3`
  LZO-compressed upload, a refresh `F0 D4 85 00 00` that takes about 15 s). No licence stated.
- [joshuatz/nfc-epaper-writer](https://github.com/joshuatz/nfc-epaper-writer): Android, Waveshare boards
  through Waveshare's closed SDK (MIT). No protocol detail.
