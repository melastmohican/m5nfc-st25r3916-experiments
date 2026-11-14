// Phase C test harness: one-letter serial commands (the full CLI is a later backlog item).
//   c chip   d detect   D dump   n read-ndef   w write test NDEF   r restore hello+example.com
//   g guard test (write to the capability container must be refused)
#include <Arduino.h>
#include <Wire.h>

#include "Ndef.h"
#include "Nfca.h"
#include "Ntag215.h"
#include "St25r3916.h"
#include "WireTransport.h"

static nfc::WireTransport transport;
static nfc::St25r3916 rf(transport);
static nfc::Ntag215 tag(rf);
static bool chipReady = false;

static uint8_t user[nfc::USER_READ_BYTES];  // static: keep big buffers off the stack
static uint8_t dumpBuf[nfc::DUMP_BYTES];

static void fail(const char* what, int rc) { Serial.printf("error: %s: %s\n", what, nfc::statusName(rc)); }

static bool select(nfc::Card& card) {
  int rc = nfc::selectCard(rf, card);
  if (rc != nfc::OK) fail("select", rc);
  return rc == nfc::OK;
}

static void cmdChip() {
  uint8_t type = 0, rev = 0;
  int rc = rf.identify(&type, &rev);
  if (rc == nfc::OK) Serial.printf("ST25R3916 present: type=0x%02X revision=%d\n", type, rev);
  else fail("chip", rc);
}

static void cmdDetect() {
  nfc::Card card;
  if (!select(card)) return;
  char uid[32];
  nfc::uidToHex(card, uid, sizeof(uid));
  Serial.printf("UID:  %s\nATQA: %02X%02X  SAK: 0x%02X\n", uid, card.atqa[0], card.atqa[1], card.sak);
  bool is215 = false;
  int rc = tag.isNtag215(&is215);
  if (rc != nfc::OK) return fail("read CC", rc);
  Serial.println(is215 ? "Type: NTAG215" : "Type: not an NTAG215 (CC differs)");
}

static void cmdDump() {
  nfc::Card card;
  if (!select(card)) return;
  int rc = tag.dump(dumpBuf);
  if (rc != nfc::OK) return fail("dump", rc);
  for (size_t page = 0; page < nfc::TOTAL_PAGES; page++) {
    const uint8_t* c = dumpBuf + page * 4;
    char text[5];
    for (int i = 0; i < 4; i++) text[i] = (c[i] >= 32 && c[i] < 127) ? c[i] : '.';
    text[4] = 0;
    Serial.printf("%3u  %02X %02X %02X %02X  %s\n", (unsigned)page, c[0], c[1], c[2], c[3], text);
  }
}

static void cmdReadNdef() {
  nfc::Card card;
  if (!select(card)) return;
  nfc::ndef::Record recs[4];
  int n = tag.readNdef(user, recs, 4);
  if (n < 0) return fail("read-ndef", n);
  if (n == 0) Serial.println("no NDEF message");
  char line[96];
  for (int i = 0; i < n; i++) {
    nfc::ndef::describe(recs[i], line, sizeof(line));
    Serial.println(line);
  }
}

static void writeRecords(const char* text, const char* uri) {
  nfc::Card card;
  if (!select(card)) return;
  uint8_t tbuf[64], ubuf[96];
  nfc::ndef::Record recs[2];
  if (!nfc::ndef::textRecord(recs[0], tbuf, sizeof(tbuf), text) ||
      !nfc::ndef::uriRecord(recs[1], ubuf, sizeof(ubuf), uri))
    return fail("build records", nfc::ERR_TOO_LARGE);
  size_t written = 0;
  int rc = tag.writeNdef(recs, 2, &written);
  if (rc != nfc::OK) return fail("write-ndef", rc);
  Serial.printf("wrote %u page(s)\n", (unsigned)written);
}

static void cmdGuard() {
  nfc::Card card;
  if (!select(card)) return;
  const uint8_t zeros[4] = {0, 0, 0, 0};
  int rc = tag.writePage(nfc::PAGE_CC, zeros);  // capability container, no force
  Serial.printf("write page 3 without force -> %s (%d)\n", nfc::statusName(rc), rc);
  rc = tag.writePage(200, zeros);
  Serial.printf("write page 200 -> %s (%d)\n", nfc::statusName(rc), rc);
}

void setup() {
  Serial.begin(115200);
  Wire.setClock(400000);
  Wire.begin();
  delay(3000);  // time to attach a serial monitor
  int rc = rf.init();
  chipReady = rc == nfc::OK;
  Serial.printf("init: %s\n", nfc::statusName(rc));
  Serial.println("commands: c d D n w r g");
}

void loop() {
  if (!Serial.available()) return;
  char c = Serial.read();
  if (!chipReady && c != 'c') return;
  switch (c) {
    case 'c': cmdChip(); break;
    case 'd': cmdDetect(); break;
    case 'D': cmdDump(); break;
    case 'n': cmdReadNdef(); break;
    case 'w': writeRecords("via Arduino", "https://example.org/arduino"); break;
    case 'r': writeRecords("hello", "https://example.com"); break;
    case 'g': cmdGuard(); break;
    default: return;
  }
  Serial.println("done");
}
