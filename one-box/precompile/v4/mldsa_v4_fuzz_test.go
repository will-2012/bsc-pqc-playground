//go:build pqprecompile

package vm

import (
	"bytes"
	"testing"
)

func FuzzMLDSAV4(f *testing.F) {
	f.Add([]byte{})
	f.Add(make([]byte, 3764))
	for _, in := range mldsaV4Vectors(f) {
		f.Add(in)
	}
	f.Fuzz(func(t *testing.T, in []byte) {
		before := bytes.Clone(in)
		p := &mldsaV4{}
		a, e := p.Run(in)
		b, e2 := p.Run(in)
		if e != nil || e2 != nil || len(a) != 32 || a[31] > 1 || !bytes.Equal(a, b) || !bytes.Equal(in, before) {
			t.Fatal("invalid result, nondeterminism or mutation")
		}
		if len(in) != 3764 && a[31] != 0 {
			t.Fatal("bad length accepted")
		}
	})
}
