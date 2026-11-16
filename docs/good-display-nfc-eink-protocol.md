# What the Good Display phone app sends to an NFC e-paper board

Observed on 2026-11-15 with the iOS app NFC-D3-Pro sending a 200x200 black and white image to the
`arduino/` firmware pretending to be the board (`emulate 600 sniff`, see `arduino/README.md`). The
board is Good Display's [NFC-D1-0154](https://www.good-display.com/product/390.html) (1.54", FM1280
chip), which the app is meant to talk to. **Not yet checked against the real board**: the answers
below are what our fake card gave (`90 00` to everything, `00 90 00` to the busy query), and the app
accepted them and showed its success check mark.

## Conversation

The card is an ISO 14443-4 (ISO-DEP) card. Every command is a short APDU; every answer was `90 00`.
Class byte `F0` is Good Display's own.

| # | APDU | Meaning |
|---|---|---|
| 1 | `00 A4 04 00 07 D2 76 00 00 85 01 01 00` | SELECT the NFC Forum NDEF application. This is iOS: an app may only talk to a card after the system selected one of the application IDs the app declares. |
| 2 | `F0 DB 02 00 00` | Unknown (no data). Answered `90 00`, the app went on. No PIN was asked for. |
| 3 | `F0 DB 00 00 62 <98 bytes>` | Screen set-up, a list of tagged entries (see below). |
| 4 | `F0 DA 00 00 03 F0 12 20` | Unknown, three data bytes. |
| 5 to 24 | `F0 D2 00 <n> FA <250 bytes>` | **Image data**, `P1` = image index (0), `P2` = packet number 0 to 19, 250 bytes per packet. |
| 25 | `F0 D4 05 80 00` | **Refresh the screen**: `P1` = 5 (power-on delay 5 x 100 ms), `P2` = `0x80` (bit 7: return at once) with image index 0. |
| 26 | `F0 DE 00 00 01` | **Read the busy flag**: `00` = idle (we answered idle at once), `01` = still refreshing. |

`F0 D2`, `F0 D4` and `F0 DE` are described in Good Display's "FMSC_NFC Protocol" document (the
command names there are "Load image", "Redraw screen" and "Get EPD busy status"). `F0 DB` and
`F0 DA` are not in that document.

## Image format

200 x 200 pixels, 1 bit per pixel, rows of 25 bytes, most significant bit first, **1 = white,
0 = black**, 5000 bytes in 20 packets. `arduino/tools/gd_image.py` rebuilds the picture from a
capture. (For a three-colour panel Good Display's document says a second image at index + 1 carries
the second colour; not seen here.)

## Screen set-up entry (command 3)

```
A0 06 12 20 00 C8 00 C8
A4 01 0C   A5 02 00 0A   A4 01 08   A5 02 00 0A   A4 01 0C   A5 02 00 0A   A4 01 02
A1 01 12   A4 01 02   A1 04 01 C7 00 01   A1 02 11 01   A1 03 44 00 18   A1 05 45 C7 00 00 00
A1 02 3C 05   A1 02 18 80   A1 02 4E 00   A1 03 4F C7 00
A3 01 24   A2 02 22 F7   A2 01 20   A4 01 02   A2 02 10 01   A5 02 00 0A
```

Format `tag, length, bytes`. This is my reading, not documented: `A0` holds the panel size
(`00 C8 00 C8` = 200 x 200), and the `A1`, `A2` and `A3` entries look like the controller commands
of an SSD1681-type 1.54" panel (`12` software reset, `11` data entry mode, `44` and `45` RAM X and Y
range for 25 bytes by 200 rows, `3C` border, `18` temperature sensor, `4E` and `4F` RAM counters,
`24` write black/white RAM, `22 F7` and `20` update sequence and master activation, `10` deep
sleep). `A4` and `A5` entries look like delays and waits. So the app tells the board how to drive
its own panel, and only the image bytes and the refresh need to change between pictures.

## Busy polling

After `F0 D4` the app asks `F0 DE 00 00 01` again and again until the answer is `00`. In a second run
the fake card answered `01` (busy) three times and then `00` (idle): the app polled four times, took
the last `00` as the end, and finished. (In the first run `00` came at once and the app stopped
after one query.) A real panel is busy for a second or two while it redraws.

## Other panel sizes

The app has one button per panel size, and the set-up entry and the sizes change with it. A run
with the 0.97" panel sent `F0 DA 00 00 03 F0 07 20` and a set-up of 103 bytes starting
`A0 06 07 20 00 58 00 B8` (88 x 184 pixels), with the RAM window entries `44 00 0A` (11 bytes per
row) and `45 27 01 00 00` (296 rows) and 9 image packets, which rebuilt to a 88 x 184 picture
("0.97 INCH E-PAPER SMILE", drawn rotated). The 1.54" run had `12 20` where this one has `07 20`, in
both `F0 DA` and the first two bytes of the `A0` entry, so those two bytes look like a panel type
code (`12` for 1.54", `07` for 0.97").

## Practical notes for sniffing

- Keep the phone still on the unit until the app shows its final message. A first run that sent 8
  commands and then failed ("tag response error / no response") came from the phone dropping the
  field mid-transfer.
- After the app has finished, the iPhone's own background tag reader keeps selecting the NDEF
  application and reading the capability container. The sniffer answers those as a real Type 4 tag
  (class `00` commands), otherwise the log fills with repeats.

## What is still unknown

- What the real board answers (status words, data in the answer to `F0 DB 02 00 00` or `F0 DE`).
- What `F0 DB 02 00 00` and `F0 DA` do, and whether a PIN or key check exists on a board that has
  one set (the protocol document lists the status words `64 5A` "key not initialized" and
  `69 82` "security status not satisfied").
- Whether a board needs the set-up entry at all for the same panel, or keeps it in its memory.
- Whether the iOS-only SELECT in row 1 matters to the real board.
