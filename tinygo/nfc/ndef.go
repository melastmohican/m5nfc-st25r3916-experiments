package nfc

import (
	"fmt"
	"strings"
)

const (
	tlvNull       = 0x00
	tlvNDEF       = 0x03
	tlvTerminator = 0xFE
)

var uriPrefixes = []string{
	"", "http://www.", "https://www.", "http://", "https://", "tel:", "mailto:",
	"ftp://anonymous:anonymous@", "ftp://ftp.", "ftps://", "sftp://", "smb://",
	"nfs://", "ftp://", "dav://", "news:", "telnet://", "imap:", "rtsp://",
	"urn:", "pop:", "sip:", "sips:", "tftp:", "btspp://", "btl2cap://",
	"btgoep://", "tcpobex://", "irdaobex://", "file://", "urn:epc:id:",
	"urn:epc:tag:", "urn:epc:pat:", "urn:epc:raw:", "urn:epc:", "urn:nfc:",
}

// Record is one NDEF record.
type Record struct {
	TNF     byte
	Type    []byte
	Payload []byte
}

// TextRecord builds a well-known "T" record (UTF-8).
func TextRecord(text, lang string) (Record, error) {
	if len(lang) > 0x3F || 1+len(lang)+len(text) > 0xFFFF {
		return Record{}, ErrParam
	}
	p := append([]byte{byte(len(lang))}, lang...)
	p = append(p, text...)
	return Record{TNF: 1, Type: []byte("T"), Payload: p}, nil
}

// URIRecord builds a well-known "U" record using the longest matching prefix code.
func URIRecord(uri string) (Record, error) {
	best := 0
	for i := 1; i < len(uriPrefixes); i++ {
		if strings.HasPrefix(uri, uriPrefixes[i]) && len(uriPrefixes[i]) > len(uriPrefixes[best]) {
			best = i
		}
	}
	rest := uri[len(uriPrefixes[best]):]
	if 1+len(rest) > 0xFFFF {
		return Record{}, ErrParam
	}
	return Record{TNF: 1, Type: []byte("U"), Payload: append([]byte{byte(best)}, rest...)}, nil
}

// EncodeMessage returns nil if recs is empty.
func EncodeMessage(recs []Record) []byte {
	var out []byte
	for i, r := range recs {
		short := len(r.Payload) < 256
		h := r.TNF & 7
		if i == 0 {
			h |= 0x80
		}
		if i == len(recs)-1 {
			h |= 0x40
		}
		if short {
			h |= 0x10
		}
		out = append(out, h, byte(len(r.Type)))
		if short {
			out = append(out, byte(len(r.Payload)))
		} else {
			n := len(r.Payload)
			out = append(out, 0, 0, byte(n>>8), byte(n))
		}
		out = append(out, r.Type...)
		out = append(out, r.Payload...)
	}
	return out
}

// DecodeMessage stops after the ME record; ErrNDEF if malformed.
func DecodeMessage(data []byte) ([]Record, error) {
	var recs []Record
	i := 0
	for i < len(data) {
		if i+2 > len(data) {
			return nil, ErrNDEF
		}
		h := data[i]
		sr, il := h&0x10 != 0, h&0x08 != 0
		typeLen := int(data[i+1])
		i += 2
		var plen int
		if sr {
			if i+1 > len(data) {
				return nil, ErrNDEF
			}
			plen = int(data[i])
			i++
		} else {
			if i+4 > len(data) {
				return nil, ErrNDEF
			}
			plen = int(data[i])<<24 | int(data[i+1])<<16 | int(data[i+2])<<8 | int(data[i+3])
			i += 4
		}
		idLen := 0
		if il {
			if i+1 > len(data) {
				return nil, ErrNDEF
			}
			idLen = int(data[i])
			i++
		}
		if typeLen > 8 || plen > 0xFFFF || i+typeLen+idLen+plen > len(data) {
			return nil, ErrNDEF
		}
		r := Record{TNF: h & 7, Type: append([]byte(nil), data[i:i+typeLen]...)}
		i += typeLen + idLen
		r.Payload = append([]byte(nil), data[i:i+plen]...)
		i += plen
		recs = append(recs, r)
		if h&0x40 != 0 {
			break
		}
	}
	return recs, nil
}

// WrapTLV returns NDEF TLV + terminator, as stored from page 4. nil if it exceeds capacity.
func WrapTLV(msg []byte, capacity int) []byte {
	lenBytes := 1
	if len(msg) >= 0xFF {
		lenBytes = 3
	}
	if 1+lenBytes+len(msg)+1 > capacity {
		return nil
	}
	out := []byte{tlvNDEF}
	if lenBytes == 1 {
		out = append(out, byte(len(msg)))
	} else {
		out = append(out, 0xFF, byte(len(msg)>>8), byte(len(msg)))
	}
	out = append(out, msg...)
	return append(out, tlvTerminator)
}

// FindMessage locates the NDEF TLV in raw user memory.
func FindMessage(user []byte) ([]byte, bool) {
	i := 0
	for i < len(user) {
		tag := user[i]
		switch tag {
		case tlvNull:
			i++
			continue
		case tlvTerminator:
			return nil, false
		}
		if i+1 >= len(user) {
			return nil, false
		}
		length, start := int(user[i+1]), i+2
		if length == 0xFF {
			if i+4 > len(user) {
				return nil, false
			}
			length, start = int(user[i+2])<<8|int(user[i+3]), i+4
		}
		if tag == tlvNDEF {
			if start+length > len(user) {
				return nil, false
			}
			return user[start : start+length], true
		}
		i = start + length
	}
	return nil, false
}

// Describe renders "text[en]: hello", "uri: https://example.com" or "tnf=.. type=.. payload=..".
func (r Record) Describe() string {
	switch {
	case r.TNF == 1 && string(r.Type) == "T" && len(r.Payload) > 0:
		l := int(r.Payload[0] & 0x3F)
		if 1+l > len(r.Payload) {
			l = len(r.Payload) - 1
		}
		return fmt.Sprintf("text[%s]: %s", r.Payload[1:1+l], r.Payload[1+l:])
	case r.TNF == 1 && string(r.Type) == "U" && len(r.Payload) > 0:
		p := ""
		if int(r.Payload[0]) < len(uriPrefixes) {
			p = uriPrefixes[r.Payload[0]]
		}
		return "uri: " + p + string(r.Payload[1:])
	}
	return fmt.Sprintf("tnf=%d type=%x payload=%x", r.TNF, r.Type, r.Payload)
}
