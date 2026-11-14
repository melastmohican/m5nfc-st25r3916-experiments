package nfc

import (
	"bytes"
	"testing"
)

func TestCRCA(t *testing.T) {
	// ISO14443-3 example: CRC_A of 00 00 is A0 1E
	if c := CRCA([]byte{0, 0}); c != 0x1EA0 {
		t.Fatalf("got %04x", c)
	}
}

func TestNDEFRoundTrip(t *testing.T) {
	tr, _ := TextRecord("hello", "en")
	ur, _ := URIRecord("https://example.com/x")
	msg := EncodeMessage([]Record{tr, ur})
	blob := WrapTLV(msg, UserBytes)
	got, ok := FindMessage(blob)
	if !ok || !bytes.Equal(got, msg) {
		t.Fatal("find")
	}
	recs, err := DecodeMessage(got)
	if err != nil || len(recs) != 2 {
		t.Fatal(err, recs)
	}
	if recs[0].Describe() != "text[en]: hello" || recs[1].Describe() != "uri: https://example.com/x" {
		t.Fatal(recs[0].Describe(), recs[1].Describe())
	}
	if ur.Payload[0] != 4 {
		t.Fatal("prefix code")
	}
}

func TestNDEFLongPayload(t *testing.T) {
	r, _ := TextRecord(string(bytes.Repeat([]byte("a"), 300)), "en")
	msg := EncodeMessage([]Record{r})
	recs, err := DecodeMessage(msg)
	if err != nil || len(recs[0].Payload) != 303 {
		t.Fatal(err)
	}
}

func TestTokenize(t *testing.T) {
	g := tokenize(`write-ndef --text "hello world" --lang en`)
	if len(g) != 5 || g[2] != "hello world" {
		t.Fatal(g)
	}
}
