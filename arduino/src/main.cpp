// Feather RP2350 + M5Stack NFC unit: serial CLI over USB serial (see Cli.h for the commands).
#include <Arduino.h>
#include <Wire.h>

#include "Cli.h"
#include "St25r3916.h"
#include "WireTransport.h"

static nfc::WireTransport transport;
static nfc::St25r3916 rf(transport);
static nfc::Cli serialCli(Serial, rf);
static bool chipReady = false;

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
  if (chipReady) serialCli.poll();
}
