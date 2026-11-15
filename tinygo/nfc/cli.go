package nfc

import (
	"bytes"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"strconv"
	"strings"
)

// NowMs exposes the transport clock.
func (r *ST25R3916) NowMs() uint32 { return r.t.NowMs() }

// Cli is the serial command interface, same commands as python/nfc/cli.py and arduino/.
// Every command ends with a line "ok" or "error: <reason>".
type Cli struct {
	Out io.Writer
	RF  *ST25R3916
}

const helpText = "commands: chip | detect | dump | read-ndef | write-ndef [--text T] [--lang L] [--uri U] |\n" +
	"          write-page <page> <8 hex> [--force] | ident | cycles [N] | help\n"

// tokenize splits on whitespace; double quotes group words.
func tokenize(line string) []string {
	var out []string
	for i := 0; i < len(line); {
		switch c := line[i]; {
		case c == ' ' || c == '\t':
			i++
		case c == '"':
			j := strings.IndexByte(line[i+1:], '"')
			if j < 0 {
				j = len(line) - i - 1
			}
			out = append(out, line[i+1:i+1+j])
			i += j + 2
		default:
			j := i
			for j < len(line) && line[j] != ' ' && line[j] != '\t' {
				j++
			}
			out = append(out, line[i:j])
			i = j
		}
	}
	return out
}

func (c *Cli) printf(f string, a ...any) { fmt.Fprintf(c.Out, f, a...) }

// Run executes one command line.
func (c *Cli) Run(line string) {
	argv := tokenize(line)
	if len(argv) == 0 {
		return
	}
	var err error
	switch argv[0] {
	case "chip":
		err = c.chip()
	case "detect":
		err = c.detect()
	case "dump":
		err = c.dump()
	case "read-ndef":
		err = c.readNDEF()
	case "write-ndef":
		err = c.writeNDEF(argv[1:])
	case "write-page":
		err = c.writePage(argv[1:])
	case "ident":
		err = c.ident()
	case "cycles":
		err = c.cycles(argv[1:])
	case "help":
		io.WriteString(c.Out, helpText)
	default:
		c.printf("unknown command: %s\n", argv[0])
		io.WriteString(c.Out, helpText)
		err = ErrParam
	}
	if err == nil {
		c.printf("ok\n")
	} else {
		c.printf("error: %s\n", err)
	}
}

func (c *Cli) chip() error {
	t, rev, err := c.RF.Identify()
	if err == nil {
		c.printf("ST25R3916 present: type=0x%02X revision=%d\n", t, rev)
	}
	return err
}

func (c *Cli) detect() error {
	card, err := SelectCard(c.RF)
	if err != nil {
		return err
	}
	c.printf("UID:  %s\n", card.UIDHex())
	c.printf("ATQA: %02X%02X  SAK: 0x%02X\n", card.ATQA[0], card.ATQA[1], card.SAK)
	is215, err := Ntag215{c.RF}.IsNtag215()
	if err != nil {
		return err
	}
	if is215 {
		c.printf("Type: NTAG215\n")
	} else {
		c.printf("Type: not an NTAG215 (CC differs)\n")
	}
	return nil
}

func (c *Cli) dump() error {
	if _, err := SelectCard(c.RF); err != nil {
		return err
	}
	d, err := Ntag215{c.RF}.Dump()
	if err != nil {
		return err
	}
	for p := 0; p < TotalPages; p++ {
		b := d[p*4 : p*4+4]
		t := make([]byte, 4)
		for i, x := range b {
			t[i] = '.'
			if x >= 32 && x < 127 {
				t[i] = x
			}
		}
		c.printf("%3d  %02X %02X %02X %02X  %s\n", p, b[0], b[1], b[2], b[3], t)
	}
	return nil
}

func (c *Cli) readNDEF() error {
	if _, err := SelectCard(c.RF); err != nil {
		return err
	}
	recs, err := Ntag215{c.RF}.ReadNDEF()
	if err != nil {
		return err
	}
	if len(recs) == 0 {
		c.printf("no NDEF message\n")
	}
	for _, r := range recs {
		c.printf("%s\n", r.Describe())
	}
	return nil
}

func (c *Cli) writeNDEF(args []string) error {
	var text, uri *string
	lang := "en"
	for i := 0; i < len(args); i++ {
		if i+1 >= len(args) {
			c.printf("bad option: %s\n", args[i])
			return ErrParam
		}
		v := args[i+1]
		switch args[i] {
		case "--text":
			text = &v
		case "--lang":
			lang = v
		case "--uri":
			uri = &v
		default:
			c.printf("bad option: %s\n", args[i])
			return ErrParam
		}
		i++
	}
	if text == nil && uri == nil {
		c.printf("give --text and/or --uri\n")
		return ErrParam
	}
	var recs []Record
	if text != nil {
		r, err := TextRecord(*text, lang)
		if err != nil {
			return ErrTooLarge
		}
		recs = append(recs, r)
	}
	if uri != nil {
		r, err := URIRecord(*uri)
		if err != nil {
			return ErrTooLarge
		}
		recs = append(recs, r)
	}
	if _, err := SelectCard(c.RF); err != nil {
		return err
	}
	n, err := Ntag215{c.RF}.WriteNDEF(recs)
	if err != nil {
		return err
	}
	c.printf("wrote %d page(s)\n", n)
	return nil
}

func (c *Cli) writePage(args []string) error {
	force := false
	var pos []string
	for _, a := range args {
		if a == "--force" {
			force = true
		} else {
			pos = append(pos, a)
		}
	}
	var data [4]byte
	b, herr := hex.DecodeString("")
	if len(pos) == 2 {
		b, herr = hex.DecodeString(pos[1])
	}
	if len(pos) != 2 || herr != nil || len(b) != 4 {
		c.printf("usage: write-page <page> <8 hex digits> [--force]\n")
		return ErrParam
	}
	copy(data[:], b)
	page, err := strconv.Atoi(pos[0])
	if err != nil || page < 0 || page > 255 {
		return ErrParam
	}
	if _, err := SelectCard(c.RF); err != nil {
		return err
	}
	if err := (Ntag215{c.RF}).WritePage(byte(page), data, force, true); err != nil {
		return err
	}
	c.printf("page %d = %02X (verified where readable)\n", page, data)
	return nil
}

func (c *Cli) ident() error {
	if _, err := SelectCard(c.RF); err != nil {
		return err
	}
	v, err := IdentCmd(c.RF, IdentVersion, 0)
	if err != nil {
		return err
	}
	c.printf("Version: % X\nType: %s\n", v, NtagTypeName(v))
	sig, err := IdentCmd(c.RF, IdentSignature, 0x00)
	if err != nil {
		return err
	}
	c.printf("Signature: %X\n", sig)
	cnt, err := IdentCmd(c.RF, IdentCounter, 0x02)
	switch {
	case err == ErrRefused:
		c.printf("Counter: not readable (NAK)\n")
	case err != nil:
		return err
	default:
		n, ok := CounterValue(cnt)
		if !ok {
			return ErrBadResponse
		}
		c.printf("Counter: %d\n", n)
	}
	return nil
}

func (c *Cli) cycles(args []string) error {
	n := 50
	if len(args) > 0 {
		v, err := strconv.Atoi(args[0])
		if err != nil {
			return ErrParam
		}
		n = v
	}
	if n < 1 || n > 1000 {
		return ErrParam
	}
	var first []byte
	ok, mism, errs := 0, 0, 0
	var lastErr error
	var total, worst uint32
	for i := 0; i < n; i++ {
		t0 := c.RF.NowMs()
		_, err := SelectCard(c.RF)
		var d []byte
		if err == nil {
			d, err = Ntag215{c.RF}.Dump()
		}
		if err != nil {
			errs++
			lastErr = err
			continue
		}
		if first == nil {
			first = d
		} else if !bytes.Equal(first, d) {
			mism++
			continue
		}
		ok++
		dt := c.RF.NowMs() - t0
		total += dt
		if dt > worst {
			worst = dt
		}
	}
	c.printf("%d/%d cycles OK", ok, n)
	if ok > 0 {
		c.printf("; per cycle avg %d ms, max %d ms", total/uint32(ok), worst)
	}
	c.printf("\n")
	if mism > 0 {
		c.printf("  %d dump mismatch(es)\n", mism)
	}
	if errs > 0 {
		c.printf("  %d error(s), last: %s\n", errs, lastErr)
	}
	if ok != n {
		return errors.New("not all cycles succeeded")
	}
	return nil
}
