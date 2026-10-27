//go:build pqprecompile && (darwin || linux)

package vm

import (
	"crypto/rand"
	"github.com/trailofbits/go-slh-dsa/slh_dsa"
	"golang.org/x/sys/unix"
	"runtime"
	"testing"
)

func benchmarkSLHV6(b *testing.B, kind int) {
	runtime.LockOSThread()
	defer runtime.UnlockOSThread()
	inputs := slhV6Vectors(b)
	for _, v := range inputs {
		if kind == 3 {
			v[32] ^= 1
		}
		if kind == 1 {
			v[len(v)-1] ^= 1
		}
		if kind == 2 {
			clear(v[64:])
		}
	}
	p := &slhV6{}
	b.ReportAllocs()
	b.ResetTimer()
	start, thread := slhV2Clock(b, unix.CLOCK_PROCESS_CPUTIME_ID), slhV2Clock(b, unix.CLOCK_THREAD_CPUTIME_ID)
	defer slhV2ReportCPU(b, start, thread)
	for i := 0; i < b.N; i++ {
		out, e := p.Run(inputs[i%len(inputs)])
		if e != nil || (out[31] == 1) != (kind == 0) {
			b.Fatal("wrong result")
		}
	}
}

func benchmarkSLHSign(b *testing.B, fast bool) {
	runtime.LockOSThread()
	defer runtime.UnlockOSThread()
	ps := slh_dsa.SlhDsaSha2_128s()
	if fast {
		ps = slh_dsa.SlhDsaSha2_128f()
	}
	sk, pk, e := slh_dsa.SLHKeygen(ps)
	if e != nil {
		b.Fatal(e)
	}
	messages := slhV2Vectors(b)
	check, e := slh_dsa.SLHSign(rand.Reader, messages[0][32:64], nil, sk)
	if e != nil || !pk.Verify(check, messages[0][32:64], nil) {
		b.Fatal("signature failed")
	}
	b.ReportAllocs()
	b.ResetTimer()
	start, thread := slhV2Clock(b, unix.CLOCK_PROCESS_CPUTIME_ID), slhV2Clock(b, unix.CLOCK_THREAD_CPUTIME_ID)
	for i := 0; i < b.N; i++ {
		m := messages[i%len(messages)][32:64]
		_, e := slh_dsa.SLHSign(rand.Reader, m, nil, sk)
		if e != nil {
			b.Fatal(e)
		}
	}
	slhV2ReportCPU(b, start, thread)
}
func BenchmarkSLHV6Sign(b *testing.B) { benchmarkSLHSign(b, true) }
func BenchmarkSLHV2Sign(b *testing.B) { benchmarkSLHSign(b, false) }
