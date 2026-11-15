package nfc

import (
	"bytes"
	"encoding/hex"
	"strings"
	"testing"
)

// ISO-DEP scenarios from ISO/IEC 14443-4 Annex B (as in embassy-rs/rnfc's tests) against a scripted link.

func h(s string) []byte {
	b, err := hex.DecodeString(strings.ReplaceAll(s, " ", ""))
	if err != nil {
		panic(err)
	}
	return b
}

type step struct {
	tx      []byte
	timeout bool
	rx      []byte
}

type script struct {
	t     *testing.T
	steps []step
	pos   int
	bad   bool
}

func newScript(t *testing.T) *script { return &script{t: t} }

func (s *script) ok(tx, rx string) *script {
	s.steps = append(s.steps, step{tx: h(tx), rx: h(rx)})
	return s
}

func (s *script) timeout(tx string) *script {
	s.steps = append(s.steps, step{tx: h(tx), timeout: true})
	return s
}

func (s *script) Transceive(tx, rx []byte, _ uint32) (int, error) {
	if s.pos >= len(s.steps) {
		s.t.Errorf("unexpected extra frame % x", tx)
		s.bad = true
		return 0, ErrI2C
	}
	st := s.steps[s.pos]
	s.pos++
	if !bytes.Equal(tx, st.tx) {
		s.t.Errorf("step %d: sent % x, expected % x", s.pos-1, tx, st.tx)
		s.bad = true
		return 0, ErrI2C
	}
	if st.timeout {
		return 0, ErrTimeout
	}
	return copy(rx, st.rx), nil
}

func (s *script) SleepMs(uint32) {}

const ats = "06 77 77 81 02 80"

func activate(t *testing.T, s *script) *IsoDep {
	d := NewIsoDep(s)
	if err := d.Activate(); err != nil {
		t.Fatal(err)
	}
	return d
}

func trx(t *testing.T, d *IsoDep, tx, want string) {
	t.Helper()
	got, err := d.Transceive(h(tx))
	if err != nil {
		t.Fatalf("% s: %v", tx, err)
	}
	if !bytes.Equal(got, h(want)) {
		t.Fatalf("% s: got % x want % x", tx, got, h(want))
	}
}

func TestActivation(t *testing.T) {
	cases := []struct {
		ats       string
		fsc       int
		sfgt, fwt uint32
	}{
		{"01", 32, 256 * 16, 256 * 16 * 16},
		{"02 05", 64, 256 * 16, 256 * 16 * 16},
		{"05 67 81 02 80", 128, 8192, 1048576},
		{"06 77 77 81 02 80", 128, 8192, 1048576},
		{"06 75 77 81 02 80", 64, 8192, 1048576}, // the DESFire EV1 ATS
	}
	for _, c := range cases {
		s := newScript(t).ok("e0 80", c.ats)
		d := activate(t, s)
		if d.FSC() != c.fsc || d.SFGT1fc() != c.sfgt || d.FWT1fc() != c.fwt {
			t.Errorf("%s: fsc %d sfgt %d fwt %d", c.ats, d.FSC(), d.SFGT1fc(), d.FWT1fc())
		}
	}
	s := newScript(t).ok("e0 80", "06 75 77 81 02 80")
	d := activate(t, s)
	if !bytes.Equal(d.ATS(), h("06 75 77 81 02 80")) {
		t.Errorf("ATS % x", d.ATS())
	}
	// no ATS at all: RATS is retried, then it fails
	s = newScript(t).timeout("e0 80").timeout("e0 80").timeout("e0 80").timeout("e0 80")
	if err := NewIsoDep(s).Activate(); err != ErrTimeout {
		t.Errorf("got %v", err)
	}
}

func TestExchange(t *testing.T) {
	s := newScript(t).ok("e0 80", ats).ok("02 12 34", "02 56 78").ok("03 aa bb", "03 cc dd")
	d := activate(t, s)
	trx(t, d, "12 34", "56 78")
	trx(t, d, "aa bb", "cc dd")
}

func TestWTX(t *testing.T) { // B.2.2
	s := newScript(t).ok("e0 80", ats).ok("02 12 34", "f2 c1").ok("f2 01", "f2 c1").ok("f2 01", "02 56 78").ok("03 aa bb", "03 cc dd")
	d := activate(t, s)
	trx(t, d, "12 34", "56 78")
	trx(t, d, "aa bb", "cc dd")
}

func chainingDev(t *testing.T, s *script) *IsoDep {
	d := activate(t, s)
	d.fsc = 10
	return d
}

func TestChaining(t *testing.T) { // B.2.4 scenarios 4 and 5
	s := newScript(t).ok("e0 80", ats).ok("12 00 11 22 33 44 55 66", "a2").ok("13 77 88 99 aa bb cc dd", "a3").
		ok("02 ee ff", "02 cc dd").ok("03 aa bb", "03 cc dd")
	d := chainingDev(t, s)
	trx(t, d, "00 11 22 33 44 55 66 77 88 99 aa bb cc dd ee ff", "cc dd")
	trx(t, d, "aa bb", "cc dd")

	s = newScript(t).ok("e0 80", ats).ok("02 12 34", "12 00 11 22 33 44 55 66").ok("a3", "13 77 88 99 aa bb cc dd").
		ok("a2", "02 ee ff").ok("03 aa bb", "03 cc dd")
	d = chainingDev(t, s)
	trx(t, d, "12 34", "00 11 22 33 44 55 66 77 88 99 aa bb cc dd ee ff")
	trx(t, d, "aa bb", "cc dd")
}

func TestIBlockErrors(t *testing.T) { // B.3.1 scenarios 6 to 9
	cases := map[string]*script{
		"6": newScript(t).ok("e0 80", ats).timeout("02 11 22").ok("b2", "a3").ok("02 11 22", "02 33 44").ok("03 55 66", "03 77 88").ok("02 99 aa", "02 bb cc"),
		"7": newScript(t).ok("e0 80", ats).ok("02 11 22", "02 33 44").timeout("03 55 66").ok("b3", "a2").ok("03 55 66", "03 77 88").ok("02 99 aa", "02 bb cc"),
		"8": newScript(t).ok("e0 80", ats).timeout("02 11 22").ok("b2", "02 33 44").ok("03 55 66", "03 77 88").ok("02 99 aa", "02 bb cc"),
		"9": newScript(t).ok("e0 80", ats).timeout("02 11 22").timeout("b2").ok("b2", "02 33 44").ok("03 55 66", "03 77 88").ok("02 99 aa", "02 bb cc"),
	}
	for name, s := range cases {
		d := activate(t, s)
		trx(t, d, "11 22", "33 44")
		trx(t, d, "55 66", "77 88")
		if name == "6" || name == "7" || name == "8" || name == "9" {
			trx(t, d, "99 aa", "bb cc")
		}
	}
}

func TestWTXErrors(t *testing.T) { // B.3.2 scenarios 10 to 14
	cases := []*script{
		newScript(t).ok("e0 80", ats).timeout("02 11 22").ok("b2", "f2 c1").ok("f2 01", "02 33 44").ok("03 55 66", "03 77 88"),
		newScript(t).ok("e0 80", ats).timeout("02 11 22").timeout("b2").ok("b2", "f2 c1").ok("f2 01", "02 33 44").ok("03 55 66", "03 77 88"),
		newScript(t).ok("e0 80", ats).ok("02 11 22", "f2 c1").timeout("f2 01").ok("b2", "f2 c1").ok("f2 01", "02 33 44").ok("03 55 66", "03 77 88"),
		newScript(t).ok("e0 80", ats).ok("02 11 22", "f2 c1").timeout("f2 01").ok("b2", "02 33 44").ok("03 55 66", "03 77 88"),
		newScript(t).ok("e0 80", ats).ok("02 11 22", "f2 c1").timeout("f2 01").timeout("b2").ok("b2", "02 33 44").ok("03 55 66", "03 77 88"),
	}
	for _, s := range cases {
		d := activate(t, s)
		trx(t, d, "11 22", "33 44")
		trx(t, d, "55 66", "77 88")
		if s.bad || s.pos != len(s.steps) {
			t.Error("script not consumed exactly")
		}
	}
}

func TestChainingErrors(t *testing.T) { // B.3.4 scenarios 16 to 20
	pcd := []*script{
		newScript(t).ok("e0 80", ats).timeout("12 00 11 22 33 44 55 66").ok("b2", "a2").ok("13 77 88 99 aa bb cc dd", "a3").ok("02 ee ff", "02 cc dd").ok("03 aa bb", "03 cc dd"),
		newScript(t).ok("e0 80", ats).ok("12 00 11 22 33 44 55 66", "a2").timeout("13 77 88 99 aa bb cc dd").ok("b3", "a2").ok("13 77 88 99 aa bb cc dd", "a3").ok("02 ee ff", "02 cc dd").ok("03 aa bb", "03 cc dd"),
		newScript(t).ok("e0 80", ats).timeout("12 00 11 22 33 44 55 66").timeout("b2").ok("b2", "a2").ok("13 77 88 99 aa bb cc dd", "a3").ok("02 ee ff", "02 cc dd").ok("03 aa bb", "03 cc dd"),
	}
	for _, s := range pcd {
		d := chainingDev(t, s)
		trx(t, d, "00 11 22 33 44 55 66 77 88 99 aa bb cc dd ee ff", "cc dd")
		trx(t, d, "aa bb", "cc dd")
		if s.bad || s.pos != len(s.steps) {
			t.Error("script not consumed exactly")
		}
	}
	s := newScript(t).ok("e0 80", ats).ok("02 12 34", "12 00 11 22 33 44 55 66").timeout("a3").ok("a3", "13 77 88 99 aa bb cc dd").ok("a2", "02 ee ff").ok("03 aa bb", "03 cc dd")
	d := chainingDev(t, s)
	trx(t, d, "12 34", "00 11 22 33 44 55 66 77 88 99 aa bb cc dd ee ff")
	trx(t, d, "aa bb", "cc dd")
}

func TestRetriesGiveUp(t *testing.T) {
	s := newScript(t).ok("e0 80", ats).timeout("02 12 34")
	for i := 0; i < 9; i++ {
		s.timeout("b2")
	}
	d := activate(t, s)
	if _, err := d.Transceive(h("12 34")); err != ErrTimeout {
		t.Fatalf("got %v", err)
	}
	if s.bad || s.pos != len(s.steps) {
		t.Error("the 10th failure must end the exchange")
	}
}

func TestProtocolErrors(t *testing.T) {
	cases := map[string]string{
		"unknown block":      "c2",
		"bad WTX length":     "f2 c1 00",
		"ACK on last block":  "a2",
		"zero-length answer": "",
	}
	for name, rx := range cases {
		s := newScript(t).ok("e0 80", ats).ok("02 12", rx)
		d := activate(t, s)
		if _, err := d.Transceive(h("12")); err != ErrProtocol {
			t.Errorf("%s: got %v", name, err)
		}
	}
}

func TestDeselect(t *testing.T) {
	s := newScript(t).ok("e0 80", ats).ok("c2", "c2")
	if err := activate(t, s).Deselect(); err != nil {
		t.Fatal(err)
	}
	s = newScript(t).ok("e0 80", ats).ok("c2", "02")
	if err := activate(t, s).Deselect(); err != ErrProtocol {
		t.Fatalf("got %v", err)
	}
}

func TestDesfireGetVersion(t *testing.T) { // three frames, as the ORCA card answers it
	s := newScript(t).ok("e0 80", "06 75 77 81 02 80").
		ok("02 90 60 00 00 00", "02 04 01 01 01 00 18 05 91 af").
		ok("03 90 af 00 00 00", "03 04 01 01 01 04 18 05 91 af").
		ok("02 90 af 00 00 00", "02 04 82 81 fa e2 40 80 ba 64 95 c6 40 25 15 91 00")
	d := activate(t, s)
	data, sw1, sw2, err := DesfireCommand(d, 0x60)
	if err != nil || sw1 != 0x91 || sw2 != 0x00 || len(data) != 28 {
		t.Fatal(err, sw1, sw2, len(data))
	}
	if data[0] != 0x04 || data[1] != 0x01 || data[5] != 0x18 || data[26] != 0x25 || data[27] != 0x15 {
		t.Errorf("% x", data)
	}
}
