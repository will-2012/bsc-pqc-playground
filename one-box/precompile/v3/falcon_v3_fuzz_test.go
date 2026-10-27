//go:build pqprecompile

package vm

import (
	"bytes"
	"testing"
)

func FuzzFalconV3(f *testing.F) {
	f.Add([]byte{})
	f.Add(make([]byte, 1595))
	for _, in := range falconV3Vectors(f) {
		f.Add(in)
	}
	f.Fuzz(func(t *testing.T, in []byte) {
		before := bytes.Clone(in)
		p := &falconV3{}
		a, e := p.Run(in)
		b, e2 := p.Run(in)
		if e != nil || e2 != nil || len(a) != 32 || a[31] > 1 || !bytes.Equal(a, b) || !bytes.Equal(in, before) {
			t.Fatal("invalid result, nondeterminism or mutation")
		}
		if (len(in) < 1545 || len(in) > 1595) && a[31] != 0 {
			t.Fatal("bad length accepted")
		}
	})
}
