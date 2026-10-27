//go:build pqprecompile && (darwin || linux)

package vm

import (
	"github.com/ethereum/go-ethereum/crypto/pqfalcon"
	"golang.org/x/sys/unix"
	"runtime"
	"testing"
)

func benchmarkFalconV3(b *testing.B, kind int) {
	runtime.LockOSThread()
	defer runtime.UnlockOSThread()
	inputs := falconV3Vectors(b)
	for _, v := range inputs {
		if kind == 3 {
			v[896] ^= 1
		}
		if kind == 1 {
			v[len(v)-1] ^= 1
		}
		if kind == 2 {
			clear(v[928:])
		}
	}
	p := &falconV3{}
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

func BenchmarkFalconV3Sign(b *testing.B) {
	runtime.LockOSThread()
	defer runtime.UnlockOSThread()
	pk, sk, e := pqfalcon.Keygen()
	if e != nil {
		b.Fatal(e)
	}
	defer clear(sk)
	messages := slhV2Vectors(b)
	b.ReportAllocs()
	b.ResetTimer()
	start, thread := slhV2Clock(b, unix.CLOCK_PROCESS_CPUTIME_ID), slhV2Clock(b, unix.CLOCK_THREAD_CPUTIME_ID)
	for i := 0; i < b.N; i++ {
		sig, e := pqfalcon.Sign(sk, messages[i%len(messages)][32:64])
		if e != nil || len(sig) < 617 || len(sig) > 667 {
			b.Fatal("sign failed")
		}
		if i == 0 && !pqfalcon.Verify(pk, messages[0][32:64], sig) {
			b.Fatal("verify failed")
		}
	}
	slhV2ReportCPU(b, start, thread)
}
