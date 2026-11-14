// Phase C bring-up: chip identity, then `detect` once a second.
// Mirrors `python -m nfc.cli chip` and `detect`.
#include <Arduino.h>
#include <Wire.h>

#include "Nfca.h"
#include "St25r3916.h"
#include "WireTransport.h"

static nfc::WireTransport transport;
static nfc::St25r3916 rf(transport);
static bool chipReady = false;

static void chip() {
  uint8_t type = 0, rev = 0;
  int rc = rf.identify(&type, &rev);
  if (rc == nfc::OK)
    Serial.printf("ST25R3916 present: type=0x%02X revision=%d\n", type, rev);
  else
    Serial.printf("chip: %s\n", nfc::statusName(rc));
}

static void detect() {
  nfc::Card card;
  int rc = nfc::selectCard(rf, card);
  if (rc != nfc::OK) {
    Serial.printf("detect: %s\n", nfc::statusName(rc));
    return;
  }
  char uid[32];
  nfc::uidToHex(card, uid, sizeof(uid));
  Serial.printf("UID:  %s\n", uid);
  Serial.printf("ATQA: %02X%02X  SAK: 0x%02X\n", card.atqa[0], card.atqa[1], card.sak);
  uint8_t cc[16];
  rc = nfc::t2Read(rf, 3, cc);
  if (rc != nfc::OK) {
    Serial.printf("read page 3: %s\n", nfc::statusName(rc));
    return;
  }
  bool ntag215 = cc[0] == 0xE1 && cc[1] == 0x10 && cc[2] == 0x3E && cc[3] == 0x00;
  Serial.printf("Type: %s (CC %02X %02X %02X %02X)\n",
                ntag215 ? "NTAG215" : "not an NTAG215", cc[0], cc[1], cc[2], cc[3]);
}

void setup() {
  Serial.begin(115200);
  Wire.setClock(400000);
  Wire.begin();
  delay(3000);  // time to attach a serial monitor
  chip();
  int rc = rf.init();
  chipReady = rc == nfc::OK;
  Serial.printf("init: %s\n", nfc::statusName(rc));
}

void loop() {
  if (chipReady) detect();
  delay(1000);
}
