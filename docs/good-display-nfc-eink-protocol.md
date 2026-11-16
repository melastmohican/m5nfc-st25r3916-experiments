# How Good Display's phone apps talk to an NFC e-paper board

Sources, strongest first:

1. **Traffic.** The iOS app NFC-D3-Pro sending images to the `arduino/` firmware pretending to be the
   board (`emulate 600 sniff`, see `arduino/README.md`), 2026-11-15. Six captures: four
   two-colour panels (1.54", 1.53", 0.97", 2.13"), one four-colour (2.66", 296 x 152) and one three-colour
   (2.13").
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

Seen in traffic: `12` 1.54" (200 x 200) and 1.53" (152 x 152), `00` 2.13" (122 x 250, 16 blocks) and
`07` 0.97" (88 x 184), all two-colour; `06` 2.66" four-colour; `00` 2.13" three-colour (chan `30`).
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

## Check against the traffic

The `F0 DB` set-up, the `F0 DA` panel type and the image blocks seen from the iOS app were compared with
the strings the decompiled Android app builds for the same panel (`data.setEpdInit`). **All six match
byte for byte**, so the decompiled `data` class is a reliable map of what the iPhone sends too.

| Capture | Set-up | `F0 DA` | Image | Refresh |
|---|---|---|---|---|
| 1.54", 1.53", 0.97", 2.13", two-colour | 103 or 108 B, SSD1680/1681-style opcodes | `F0 <res> 20` | one plane, index 0 | `F0 D4 05 80 00` |
| 2.13", three-colour | 109 B, same opcodes, chan `30` | `F0 00 30` | 16 blocks at index 0 (black/white) and 16 at index 1 (red) | `F0 D4 05 80 00`, 16 busy polls |
| 2.66", four-colour (296 x 152) | 141 B, different opcodes (below) | `F0 06 20` | 45 blocks, index 0 only | `F0 D4 85 80 00`, 14 busy polls |

**Four-colour differences:**
- The set-up uses a different command set: `00` (panel setting), `01`, `03`, `06` (booster), `50`, `60`,
  `61` (resolution), `E3`, `E9`. It looks like the JD79661/JD79667 family, not SSD. The wrapper (`A0`
  start, `A1` command, `A2`/`A3` update and RAM write, `A4`/`A5` pins and waits) is the same, so the board
  seems to replay whatever script the phone sends for the panel; not documented by Good Display.
- The width in the `A0` entry is counted in bits, so twice the pixel width: 296 x 152 sends `01 30` (304) x
  `01 28` (296).
- Data is 2 bits per pixel in one plane: 152 x 296 x 2 / 8 = 11,248 B = 45 blocks.
- The refresh uses `P1 = 85` (bit 7: skip the panel check) where the other kinds use `05`.

**Four-colour panels the Android app knows** (from the `A0` entry; width is half the bit count):

| Panel | Width x height | Notes |
|---|---|---|
| 0.97" | 88 x 184 | |
| 1.53" / 1.54" | 152 x 152 / 200 x 200 | |
| 2.13" | 128 x 250 | |
| 2.66" | 152 x 296 | GDEY0266F51; also on iOS |
| 2.66" | 184 x 360 | GDEY0266F52H; **not on iOS** |
| 2.7" | 176 x 264 | |
| 2.9" | 128 x 296 | GDEY029F52; also on iOS |
| 2.9" | 168 x 384 | GDEY029F51H; **not on iOS** |
| 3.7" | 240 x 416 | GDEM037F51; excluded for iPhone by Good Display |
| 4.2" | 400 x 300 | |

## Panels the iOS app does not offer

Good Display's NFC-D3-029 page lists GDEY0266F52H, GDEY029F51H and GDEM037F51 as not working on iPhone.
The iOS app offers one four-colour entry for 2.66" (296 x 152) and one for 2.9" (128 x 296); the higher
resolution variants are missing. The Android app has scripts for them, so the board side is fine.

- Both missing panels have more pixels than the offered ones (16 KB of four-colour data against 9 to 11 KB)
  and use the newer JD79667 driver, where the offered ones use JD79661. The two traits go together here,
  so this doesn't say which one matters.
- In `data.java` the four-colour sizes with a third string, `epd_init[2]` (never sent by the Android app),
  are 0.97", 1.54", 2.13", 2.66" (296 x 152), 2.7" and 2.9" (128 x 296). The ones without it are 1.53",
  2.66" (184 x 360), 2.9" (168 x 384), 3.7" and 4.2". The gap matches the exclusions, but what that string
  is for is unknown.
- Memory is not the reason as far as the traffic shows: the image is streamed in 250-byte blocks.

## Where the panel is programmed

**In the app, not in the board.** The phone sends the panel's whole command script in the `F0 DB` set-up
before every image, one script per panel and colour type (`data.setEpdInit`). The board's chip (FM1280)
only receives it; as far as the traffic shows it clocks each `A1`/`A2`/`A3` entry out to the panel. A panel
the app has no script for can't be driven from it, which is why the iOS app can't offer some panels.

Checked against the JADARD JD79661AA datasheet (V1.0.4, copy on
[Adafruit's CDN](https://cdn-learn.adafruit.com/assets/assets/000/138/563/original/JD79661AA_V1.0.4_20230720_spec.pdf)):
the opcodes `00` PSR, `01` PWR, `02` POF, `04` PON, `06` BTST, `07` DSLP (check code `A5`), `10` DTM, `12`
DRF (parameter `00`), `30` PLL, `50` CDI and `61` TRES (HRES 152 and VRES 296 for the 2.66") are all in it,
with matching parameters. `03`, `4D`, `60`, `B4`, `B5`, `E0`, `E6`, `E7` and `E9` are not; they are
probably vendor settings. Nothing has run on a real panel yet.

### What changes from panel to panel (four-colour scripts)

Values after each opcode, from the app's scripts. `-` means the script doesn't send it.

| Opcode | 0.97" | 1.53" | 2.66" 152x296 | 2.66" 184x360 | 2.7" | 2.9" 128x296 | 2.9" 168x384 | 3.7" | 4.2" |
|---|---|---|---|---|---|---|---|---|---|
| `4D` | 78 | 78 | 78 | 78 | 78 | 78 | 78 | - | - |
| `00` | 0F29 | 0F29 | 0F29 | 0F29 | 0F29 | 0F29 | 0F29 | 0F29 | 2F69 |
| `01` | 0700 | - | 0700 | 0700 | 0700 | 0700 | 0700 | 070022780A22 | 07F0 |
| `03` | 105444 | - | 105444 | 105444 | 105444 | 105444 | 105444 | 105444 | - |
| `06` | 05003F0A25121A | 0D123020192A22 | 05003F0A25121A | same | same | same | same | C0C0C0 | 0F8B9C96 |
| `50` | 37 | 37 | 37 | 37 | 37 | 37 | 37 | 37 | 37 |
| `60`, `E7`, `E3`, `B4`, `B5` | 0202, 1C, 22, D0, - | - | 0202, 1C, 22, D0, 03 | same | same | same | same | no `B4`/`B5` | - |
| `61` | 0058 00B8 | 0098 0098 | 0098 0128 | 00B8 0168 | 00B0 0128 | 0080 0128 | 00A8 0180 | 00F0 01A0 | 0190 012C |
| `E9`, `30` | 01, 08 | 01, 08 | 01, 08 | 01, 08 | 01, 08 | 01, 08 | 01, 08 | 01, 08 | 01, 08 |
| `E0` + `E6` | 02, 5D | - | 02, 68 | **-** | 02, 5C | 02, 5A | **-** | - | - |

What this says:
- **One oddity:** for the 2.7" the `A0` entry says 176 x 264 and the unused `epd_init[2]` string says the
  same in its `61`, but the string the app sends has `61 00 B0 01 28`, i.e. 296 rows. Either a typo in the
  app or the panel ignores it; unexplained.
- **The undocumented opcodes carry the same values on every panel that has them** (0.97", 2.66", 2.7" and
  2.9"). They look like chip set-up, not per-panel tuning. The script differs from panel to panel in the
  resolution (`61`, and the `A0` header) and in `E6`, which takes 5A, 5C, 5D or 68 (my guess: a
  fast-refresh waveform or temperature value; the datasheet has no `E0` or `E6`).
- **The two panels the iOS app doesn't offer (2.66" 184 x 360, 2.9" 168 x 384) have the same script as
  their lower-resolution siblings, except for the resolution and the missing `E0`/`E6` step.** Both use
  the newer JD79667 chip according to the vendor pages; the script doesn't show a difference.
- **`E0`/`E6` is absent from exactly the sizes that have no `epd_init[2]`** (1.53", 2.66" 184 x 360,
  2.9" 168 x 384, 3.7", 4.2"). So that third string probably belongs to the fast-refresh variant. What
  it is sent for is still unknown.
- **Other chips.** 1.54" (`E9 EF F6 EF 04 E0 E6`, no resolution command), 2.13" (`06 E9 04 E0 E6`), 3.7"
  (`C3`, `FD`, `DA`, `C9`, `A8`, `FF` entries) and 4.2" (`00 2F69`, `62`) use quite different scripts, so
  they are different panel chips.

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
