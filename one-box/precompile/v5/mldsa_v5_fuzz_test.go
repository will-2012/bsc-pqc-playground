//go:build pqprecompile

package vm

import (
	"bytes"
	"testing"
)

func FuzzMLDSAV5(f *testing.F) {
	f.Add([]byte{})
	f.Add(make([]byte, 5293))
	for _, in := range mldsaV5Vectors(f) {
		f.Add(in)
	}
	f.Fuzz(func(t *testing.T, in []byte) {
		before := bytes.Clone(in)
		p := &mldsaV5{}
		a, e := p.Run(in)
		b, e2 := p.Run(in)
		if e != nil || e2 != nil || len(a) != 32 || a[31] > 1 || !bytes.Equal(a, b) || !bytes.Equal(in, before) {
			t.Fatal("invalid result, nondeterminism or mutation")
		}
		if len(in) != 5293 && a[31] != 0 {
			t.Fatal("bad length accepted")
		}
	})
}
