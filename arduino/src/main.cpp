// I2C scan on the Qwiic bus and ST25R3916 identity read.
// On this board variant `Wire` is I2C1 on GP2 (SDA) / GP3 (SCL), the Qwiic/STEMMA QT port,
// already the default. `Wire1` is I2C0 on an unconnected pin: do not use it
// (Wire1.setSDA(2) panics).
#include <Arduino.h>
#include <Wire.h>

static const uint8_t NFC_ADDR = 0x50;
static const uint8_t REG_IC_IDENTITY = 0x3F;
static const uint8_t OP_READ = 0x40;

static int readIdentity() {
  Wire.beginTransmission(NFC_ADDR);
  Wire.write(REG_IC_IDENTITY | OP_READ);
  if (Wire.endTransmission(false) != 0) return -1;  // repeated start, like the Python driver
  if (Wire.requestFrom((int)NFC_ADDR, 1) != 1) return -2;
  return Wire.read();
}

void setup() {
  Serial.begin(115200);
  Wire.setClock(400000);
  Wire.begin();
}

void loop() {
  Serial.print("scan:");
  for (uint8_t a = 0x08; a < 0x78; a++) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) Serial.printf(" 0x%02X", a);
  }
  int id = readIdentity();
  if (id >= 0)
    Serial.printf("\nidentity 0x%02X type %d revision %d\n", id, (id >> 3) & 0x1F, id & 7);
  else
    Serial.printf("\nidentity read failed (%d)\n", id);
  delay(2000);
}
