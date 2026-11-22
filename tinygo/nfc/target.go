package nfc

// What a card-emulation target answers to one command frame (see listener.go). No hardware.

// TargetReply says what to send back for one frame.
type TargetReply struct {
	Len    int  // reply bytes (the chip adds the CRC); 0 with Ack4 == false: no reply
	Ack4   bool // send the 4-bit frame Nibble instead (Type 2: ACK = 0xA, NAK = 0x0)
	Nibble byte
	Halt   bool // go to the halt state after sending the reply (HLTA, DESELECT)
}

// Target is a tag or card model driven by the Listener.
type Target interface {
	// Handle takes one frame from the reader (CRC removed) and fills out; len(out) is at least 260.
	Handle(frame, out []byte) TargetReply
	// Reset is called when a new session starts: forget any protocol state.
	Reset()
}
