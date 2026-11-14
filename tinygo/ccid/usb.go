//go:build tinygo

package ccid

import (
	"machine"
	"machine/usb"
	"machine/usb/descriptor"
)

// CCID functional descriptor (54 bytes, rev 1.10, short-APDU level); identical to
// kClassDesc in ../../arduino/src/CcidDriver.cpp.
var classDesc = []byte{
	0x36, 0x21, 0x10, 0x01, 0x00, 0x07, 0x03, 0x00, 0x00, 0x00, 0xA0, 0x0F, 0x00, 0x00,
	0xA0, 0x0F, 0x00, 0x00, 0x00, 0x00, 0x2A, 0x00, 0x00, 0x00, 0x40, 0x05, 0x00, 0x00,
	0xFE, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0xBA, 0x00,
	0x02, 0x00, 0x0F, 0x01, 0x00, 0x00, 0xFF, 0xFF, 0x00, 0x00, 0x00, 0x01}

// Endpoint indices (TinyGo's CDC uses 1-3). Addresses: IN = 0x80|n, OUT = n.
const (
	epInt   = 4 // interrupt IN: slot change notifications
	epOut   = 5 // bulk OUT: PC_to_RDR
	epIn    = 6 // bulk IN: RDR_to_PC
	ccidItf = 2 // after the two CDC interfaces
	pktSize = 64
)

func ep(addr, attr byte, size uint16, interval byte) []byte {
	return []byte{7, descriptor.TypeEndpoint, addr, attr, byte(size), byte(size >> 8), interval}
}

func buildDescriptor() descriptor.Descriptor {
	itf := []byte{9, descriptor.TypeInterface, ccidItf, 0, 3, 0x0B, 0, 0, 0}
	return descriptor.Descriptor{
		Device: descriptor.DeviceCDC.Bytes(),
		Configuration: descriptor.Append([][]byte{
			descriptor.ConfigurationMSC.Bytes(), // 3 interfaces; total length is filled in later
			descriptor.InterfaceAssociationCDC.Bytes(),
			descriptor.InterfaceCDCControl.Bytes(),
			descriptor.ClassSpecificCDCHeader.Bytes(),
			descriptor.ClassSpecificCDCACM.Bytes(),
			descriptor.ClassSpecificCDCUnion.Bytes(),
			descriptor.ClassSpecificCDCCallManagement.Bytes(),
			descriptor.EndpointEP1IN.Bytes(),
			descriptor.InterfaceCDCData.Bytes(),
			descriptor.EndpointEP2OUT.Bytes(),
			descriptor.EndpointEP3IN.Bytes(),
			itf,
			classDesc,
			ep(0x80|epInt, 0x03, 8, 16),
			ep(epOut, 0x02, pktSize, 0),
			ep(0x80|epIn, 0x02, pktSize, 0),
		}),
	}
}

// Reader is the USB side. Call Begin once at start-up (after the CDC serial is up), then
// Service from the main loop: all NFC/I2C work happens there, never in the USB interrupt.
type Reader struct {
	slot Slot

	rx      [MaxMessage + pktSize]byte
	rxLen   int
	pending bool // a complete message waits in rx
	txDone  bool // last IN packet finished

	lastPresent int // -1 unknown
	lastPoll    int64
}

// Service pollMs: card presence poll period.
const pollMs = 300

func Begin(be Backend) *Reader {
	r := &Reader{slot: Slot{BE: be}, lastPresent: -1, txDone: true}
	machine.ConfigureUSBEndpoint(buildDescriptor(),
		[]usb.EndpointConfig{
			{Index: epInt, IsIn: true, Type: usb.ENDPOINT_TYPE_INTERRUPT, TxHandler: r.onTx},
			{Index: epOut, IsIn: false, Type: usb.ENDPOINT_TYPE_BULK, DelayRxHandler: r.onRx},
			{Index: epIn, IsIn: true, Type: usb.ENDPOINT_TYPE_BULK, TxHandler: r.onTx},
		},
		[]usb.SetupConfig{{Index: ccidItf, Handler: func(usb.Setup) bool { return false }}},
	)
	return r
}

func (r *Reader) onTx() { r.txDone = true }

// onRx runs in the USB interrupt: collect packets, hand a complete message to Service.
// Returning false withholds the ACK until Service has handled the message.
func (r *Reader) onRx(b []byte) bool {
	if r.pending {
		return true
	}
	if r.rxLen+len(b) > len(r.rx) {
		r.rxLen = 0
		return true
	}
	r.rxLen += copy(r.rx[r.rxLen:], b)
	if r.rxLen < HeaderLen {
		return true
	}
	want := HeaderLen + (int(r.rx[1]) | int(r.rx[2])<<8)
	if r.rxLen >= want {
		r.pending = true
		return false
	}
	return true
}

func (r *Reader) sendPacket(data []byte, nowMs func() int64) {
	r.txDone = false
	machine.SendUSBInPacket(epIn, data)
	for t := nowMs(); !r.txDone && nowMs()-t < 100; {
	}
}

func (r *Reader) sendBulk(data []byte, nowMs func() int64) {
	for off := 0; off < len(data); off += pktSize {
		end := off + pktSize
		if end > len(data) {
			end = len(data)
		}
		r.sendPacket(data[off:end], nowMs)
	}
	if len(data) > 0 && len(data)%pktSize == 0 {
		r.sendPacket(nil, nowMs) // ZLP ends the transfer
	}
}

// Service handles a pending message or sends a slot-change notification. nowMs is a
// millisecond clock; present reports whether the backend sees a card.
func (r *Reader) Service(nowMs func() int64) {
	if r.pending {
		resp := r.slot.Handle(r.rx[:r.rxLen])
		r.rxLen = 0
		if resp != nil {
			r.sendBulk(resp, nowMs)
		}
		r.pending = false
		machine.AckUsbOutTransfer(epOut)
		return
	}
	now := nowMs()
	if now-r.lastPoll < pollMs {
		return
	}
	r.lastPoll = now
	st, err := r.slot.BE.Status()
	present := 0
	if err == nil && st != IccAbsent {
		present = 1
	}
	if present != r.lastPresent {
		r.lastPresent = present
		msg := SlotChangeMessage(present == 1, true)
		r.txDone = false
		machine.SendUSBInPacket(epInt, msg[:])
	}
}
