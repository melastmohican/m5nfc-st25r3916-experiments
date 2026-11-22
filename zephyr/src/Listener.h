// NFC-A passive target (card emulation) on the ST25R3916, driving a Type2Tag.
//
// Ported from M5Stack's MIT-licensed emulation layer (M5Unit-NFC,
// src/nfc/layer/a/emulation_layer_a_ST25R3916.cpp) and from ../../python/nfc/listen.py. The chip
// answers REQA/WUPA and the whole anticollision by itself from the PT memory (UID, ATQA, SAK);
// this code follows the target state, reads the frames that arrive once a reader has selected us
// and answers them from the tag model.
//
// Timing: a reader gives a tag about 5 ms to answer, so poll() has to be called back to back, and
// nothing may print while it runs (a serial write can stall for longer than that). Events are kept
// in a small ring buffer and printed afterwards with printLog().
#pragma once
#include <stddef.h>
#include <stdint.h>

#include "Io.h"

#include "St25r3916.h"
#include "Target.h"

namespace nfc {

class Listener {
 public:
  Listener(St25r3916& rf, Target& target) : rf_(rf), target_(&target) {}
  void setTarget(Target& target) { target_ = &target; }

  // Puts the chip in target mode with the given 7-byte UID. Returns a Status.
  int start(const uint8_t uid[7], uint16_t atqa = 0x0044, uint8_t sak = 0x00);
  void poll();           // one step of the state machine; call it back to back
  int stop();            // back to reader mode (calls St25r3916::init)
  void printLog(Io& io);
  uint32_t fieldCount() const { return fields_; }
  uint32_t frameCount() const { return frames_; }

 private:
  enum State : uint8_t { OFF, IDLE, READY, ACTIVE, HALT };
  enum Kind : uint8_t { EV_STATE, EV_FIELD, EV_RATE, EV_RX, EV_TX, EV_ERR, EV_IRQ };
  struct Event {
    uint32_t us;
    uint8_t kind;
    uint8_t len;
    uint8_t data[12];
  };
  static constexpr size_t LOG_SIZE = 256;

  void log(Kind kind, const uint8_t* data = nullptr, size_t len = 0);
  void logState(State s);
  void change(uint8_t reg, uint8_t value, uint8_t mask);
  void enableIrqs(uint32_t bits);
  void disableIrqs(uint32_t bits);
  uint32_t irq(uint32_t bits);

  void gotoOff();
  void gotoIdle();
  void gotoReady();
  void gotoActive();
  void gotoHalt();
  void pollOff();
  void pollIdle();
  void pollReady();
  void pollActive();
  void pollHalt();
  void readBitrate();
  bool readFrame(uint8_t* out, size_t cap, size_t* n);
  void transmit(const TargetReply& r, const uint8_t* data);

  St25r3916& rf_;
  Target* target_;
  State state_ = OFF;
  bool wakeup_ = false;
  int8_t bitrate_ = -1;  // 0 = 106 kbit/s, -1 unknown
  uint32_t pending_ = 0;
  uint32_t fields_ = 0;
  uint32_t frames_ = 0;
  Event log_[LOG_SIZE];
  size_t logHead_ = 0;
  size_t logCount_ = 0;
  size_t logDropped_ = 0;
};

}  // namespace nfc
