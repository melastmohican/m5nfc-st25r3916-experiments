package nfc

import "testing"

func TestNtagTypeName(t *testing.T) {
	cases := map[string][]byte{
		"NTAG215 (NXP)":            {0, 4, 4, 2, 1, 0, 0x11, 3},
		"NTAG213 (NXP)":            {0, 4, 4, 2, 1, 0, 0x0F, 3},
		"NTAG216 (NXP)":            {0, 4, 4, 2, 1, 0, 0x13, 3},
		"NTAG, unknown size (NXP)": {0, 4, 4, 2, 1, 0, 0x55, 3},
		"NXP, not an NTAG":         {0, 4, 1, 1, 1, 0, 0x18, 5},
		"unknown":                  {1, 2},
	}
	for want, v := range cases {
		if got := NtagTypeName(v); got != want {
			t.Errorf("%x: got %q want %q", v, got, want)
		}
	}
}

func TestCounterValue(t *testing.T) {
	if v, ok := CounterValue([]byte{1, 2, 3}); !ok || v != 0x030201 {
		t.Fatal(v, ok)
	}
	if _, ok := CounterValue([]byte{1, 2}); ok {
		t.Fatal("short counter accepted")
	}
}

func TestNakError(t *testing.T) {
	if NakError(0x0A) != nil {
		t.Fatal("ACK is not an error")
	}
	want := map[byte]error{0: ErrNakArg, 1: ErrNakCRC, 4: ErrNakAuth, 5: ErrNakEEPROM, 7: ErrNakOther}
	for code, e := range want {
		if NakError(code) != e {
			t.Errorf("code %x: got %v want %v", code, NakError(code), e)
		}
	}
}
