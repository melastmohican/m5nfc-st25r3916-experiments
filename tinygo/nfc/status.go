// Package nfc ports the ST25R3916 driver, ISO14443-A layer, NTAG215 model and NDEF
// from ../../arduino/src. The chip logic talks only to a Transport, so it runs on the
// host (go test, against a fake chip) and on the board (machine.I2C).
package nfc

import "errors"

var (
	ErrTimeout     = errors.New("no response from card")
	ErrCollision   = errors.New("collision")
	ErrFrame       = errors.New("receive error")
	ErrI2C         = errors.New("I2C error")
	ErrBadResponse = errors.New("bad response")
	ErrNoChip      = errors.New("no ST25R3916 on the bus")
	ErrOsc         = errors.New("oscillator did not start")
	ErrParam       = errors.New("bad parameter")
	ErrNoAck       = errors.New("no ACK")
	ErrProtected   = errors.New("page is protected (read-only, out of range, or one-way lock/config)")
	ErrVerify      = errors.New("verify failed")
	ErrTooLarge    = errors.New("message does not fit the tag")
	ErrNoCC        = errors.New("capability container missing")
	ErrNDEF        = errors.New("malformed NDEF data")
	ErrRefused     = errors.New("the tag refused the command or does not support it")

	// 4-bit NAK a tag sends after a refused WRITE (NTAG21x datasheet 9.3, Table 23)
	ErrNakArg    = errors.New("tag refused the write: invalid argument (bad page address)")
	ErrNakCRC    = errors.New("tag refused the write: parity or CRC error in the command")
	ErrNakAuth   = errors.New("tag refused the write: authentication counter overflow")
	ErrNakEEPROM = errors.New("tag refused the write: EEPROM write error")
	ErrNakOther  = errors.New("tag refused the write: unknown NAK code")
)

// Transport is the I2C link to the unit (address 0x50).
type Transport interface {
	Write(data []byte) bool
	// Exchange writes out, issues a repeated start, then reads len(in) bytes.
	Exchange(out, in []byte) bool
	SleepMs(ms uint32)
	NowMs() uint32
}
