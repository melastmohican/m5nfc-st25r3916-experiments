#include "CcidService.h"

#include <zephyr/kernel.h>

#include "ccid_class.h"

namespace nfc {

namespace {
constexpr uint32_t POLL_MS = 300;

uint8_t rxBuf[CCID_MAX_MESSAGE + 1];
uint8_t txBuf[CCID_MAX_MESSAGE + 1];
int lastPresent = -1;  // -1 = unknown
bool notifyPending = false;
bool notifyValue = false;
uint32_t lastPoll = 0;

bool trySendNotify(bool present) {
  uint8_t msg[2];
  ccid::slotChangeMessage(present, true, msg);
  return ccid_class_notify(msg);
}
}  // namespace

void ccidService(ccid::Slot& slot, ccid::Backend& backend) {
  if (!ccid_class_is_open()) return;

  size_t n = ccid_class_poll_command(rxBuf, sizeof(rxBuf));
  if (n) {
    size_t rl = slot.handle(rxBuf, n, txBuf, sizeof(txBuf));
    if (rl) ccid_class_send(txBuf, rl);
    ccid_class_arm_out();
    return;
  }

  if (ccid_class_take_opened()) {  // host just (re)configured the device: report the current state
    lastPresent = -1;
    notifyPending = false;
  }

  if (notifyPending) {  // a notification could not be queued earlier
    if (trySendNotify(notifyValue)) notifyPending = false;
    return;
  }

  uint32_t now = k_uptime_get_32();
  if (now - lastPoll < POLL_MS) return;
  lastPoll = now;
  int st = backend.status();
  bool present = st >= 0 && st != ccid::ICC_ABSENT;
  if ((int)present != lastPresent) {
    lastPresent = present;
    notifyValue = present;
    notifyPending = !trySendNotify(present);
  }
}

}  // namespace nfc
