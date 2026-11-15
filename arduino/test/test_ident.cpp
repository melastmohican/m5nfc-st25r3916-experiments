#include <stdio.h>
#include <string.h>

#include "../src/Ident.h"

static int failures = 0;
#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); failures++; } } while (0)

int main() {
  const uint8_t n215[8] = {0, 4, 4, 2, 1, 0, 0x11, 3};
  const uint8_t n213[8] = {0, 4, 4, 2, 1, 0, 0x0F, 3};
  const uint8_t n216[8] = {0, 4, 4, 2, 1, 0, 0x13, 3};
  const uint8_t odd[8] = {0, 4, 4, 2, 1, 0, 0x55, 3};
  const uint8_t desfire[8] = {0, 4, 1, 1, 1, 0, 0x18, 5};
  CHECK(!strcmp(nfc::ntagTypeName(n215, 8), "NTAG215 (NXP)"));
  CHECK(!strcmp(nfc::ntagTypeName(n213, 8), "NTAG213 (NXP)"));
  CHECK(!strcmp(nfc::ntagTypeName(n216, 8), "NTAG216 (NXP)"));
  CHECK(!strcmp(nfc::ntagTypeName(odd, 8), "NTAG, unknown size (NXP)"));
  CHECK(!strcmp(nfc::ntagTypeName(desfire, 8), "NXP, not an NTAG"));
  CHECK(!strcmp(nfc::ntagTypeName(n215, 2), "unknown"));
  uint32_t v = 0;
  const uint8_t c[3] = {1, 2, 3};
  CHECK(nfc::counterValue(c, 3, &v) && v == 0x030201);
  CHECK(!nfc::counterValue(c, 2, &v));
  CHECK(strstr(nfc::nakText(5), "EEPROM") != nullptr);
  CHECK(strstr(nfc::nakText(0), "invalid argument") != nullptr);
  CHECK(strstr(nfc::nakText(7), "unknown") != nullptr);
  if (failures) return 1;
  printf("all ident checks passed\n");
  return 0;
}
