#include "Io.h"

#include <stdio.h>

namespace nfc {

int Io::printf(const char* fmt, ...) {
  char buf[256];
  va_list ap;
  va_start(ap, fmt);
  int n = vsnprintf(buf, sizeof(buf), fmt, ap);
  va_end(ap);
  if (n > 0) write((const uint8_t*)buf, n < (int)sizeof(buf) ? n : sizeof(buf) - 1);
  return n;
}

}  // namespace nfc
