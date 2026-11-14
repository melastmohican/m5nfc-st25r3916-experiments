// Feather RP2350 + M5Stack NFC unit: serial CLI over USB serial (see Cli.h for the commands).
#include <Arduino.h>
#include <Wire.h>

#include "Cli.h"
#include "CcidDriver.h"
#include "CcidProto.h"
#include "NfcBackend.h"
#include "St25r3916.h"
#include "WireTransport.h"

static nfc::WireTransport transport;
static nfc::St25r3916 rf(transport);
static nfc::Cli serialCli(Serial, rf);
static nfc::NfcBackend ccidBackend(rf);
static nfc::ccid::Slot ccidSlot(ccidBackend);
static bool chipReady = false;

// The core calls this before USB.begin() and its own Serial.begin(), i.e. before the USB
// configuration descriptor is built. Registering the CCID interface later has no effect.
void initVariant() { nfc::ccidBegin(); }

void setup() {
  Serial.begin(115200);
  Wire.setClock(400000);
  Wire.begin();
  delay(3000);  // time to attach a serial monitor
  int rc = rf.init();
  chipReady = rc == nfc::OK;
  Serial.printf("init: %s\n", nfc::statusName(rc));
  Serial.println("type 'help' for commands");
}

void loop() {
  if (!chipReady) return;
  serialCli.poll();
  nfc::ccidService(ccidSlot, ccidBackend);
}
