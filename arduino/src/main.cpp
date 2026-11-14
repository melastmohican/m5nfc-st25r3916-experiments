// Toolchain check: prints a line over USB serial once a second.
#include <Arduino.h>

void setup() {
  Serial.begin(115200);
}

void loop() {
  Serial.printf("hello from Arduino-Pico, uptime %lu ms\n", millis());
  delay(1000);
}
