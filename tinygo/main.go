// Feather RP2350 + M5Stack NFC unit, TinyGo: serial CLI (see nfc/cli.go).
// Build: GOTOOLCHAIN=go1.26.0 tinygo build -target=pico2 -o nfc.uf2 .
package main

import (
	"machine"
	"time"

	"github.com/melastmohican/m5nfc-st25r3916-experiments/tinygo/ccid"
	"github.com/melastmohican/m5nfc-st25r3916-experiments/tinygo/nfc"
)

const unitAddr = 0x50

// i2cTransport adapts machine.I2C to nfc.Transport. On the Feather RP2350 the Qwiic port
// is I2C1 on GP2 (SDA) / GP3 (SCL).
type i2cTransport struct{ bus *machine.I2C }

func (t i2cTransport) Write(d []byte) bool { return t.bus.Tx(unitAddr, d, nil) == nil }
func (t i2cTransport) Exchange(out, in []byte) bool {
	return t.bus.Tx(unitAddr, out, in) == nil
}
func (t i2cTransport) SleepMs(ms uint32) { time.Sleep(time.Duration(ms) * time.Millisecond) }
func (t i2cTransport) NowMs() uint32     { return uint32(time.Now().UnixNano() / 1e6) }

func main() {
	i2c := machine.I2C1
	i2c.Configure(machine.I2CConfig{SDA: machine.GPIO2, SCL: machine.GPIO3, Frequency: 400 * machine.KHz})
	rf := nfc.New(i2cTransport{i2c})
	// The CCID interface must be registered before the host reads the configuration descriptor.
	reader := ccid.Begin(&nfc.Backend{RF: rf})
	time.Sleep(3 * time.Second) // time to attach a serial monitor
	err := rf.Init()
	if err != nil {
		println("init:", err.Error())
	} else {
		println("init: ok")
		println("type 'help' for commands")
	}
	cli := &nfc.Cli{Out: machine.Serial, RF: rf}
	nowMs := func() int64 { return time.Now().UnixNano() / 1e6 }
	var line []byte
	for {
		if err == nil {
			reader.Service(nowMs)
		}
		for machine.Serial.Buffered() > 0 {
			b, _ := machine.Serial.ReadByte()
			if b == '\r' || b == '\n' {
				if len(line) > 0 && err == nil {
					cli.Run(string(line))
				}
				line = line[:0]
			} else if len(line) < 200 {
				line = append(line, b)
			}
		}
		time.Sleep(time.Millisecond)
	}
}
