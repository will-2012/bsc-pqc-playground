//go:build pqprecompile && (darwin || linux)

// SPDX-License-Identifier: LGPL-3.0-or-later
package vm

import (
	"bytes"
	"runtime"
	"testing"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/crypto"
	"golang.org/x/sys/unix"
)

func slhV2Clock(b *testing.B, id int32) int64 {
	b.Helper()
	var t unix.Timespec
	if err := unix.ClockGettime(id, &t); err != nil {
		b.Fatal(err)
	}
	return t.Nano()
}
func slhV2ReportCPU(b *testing.B, processStart, threadStart int64) {
	threadEnd := slhV2Clock(b, unix.CLOCK_THREAD_CPUTIME_ID)
	processEnd := slhV2Clock(b, unix.CLOCK_PROCESS_CPUTIME_ID)
	b.ReportMetric(float64(processEnd-processStart)/float64(b.N), "cpu-ns/op")
	b.ReportMetric(float64(threadEnd-threadStart)/float64(b.N), "thread-cpu-ns/op")
}

func BenchmarkSLHV2Valid(b *testing.B)         { benchmarkSLHV2(b, 0) }
func BenchmarkSLHV2Invalid(b *testing.B)       { benchmarkSLHV2(b, 1) }
func BenchmarkSLHV2ZeroSignature(b *testing.B) { benchmarkSLHV2(b, 2) }
func benchmarkSLHV2(b *testing.B, kind int) {
	runtime.LockOSThread()
	defer runtime.UnlockOSThread()
	inputs := slhV2Vectors(b)
	for _, in := range inputs {
		if kind == 1 {
			in[len(in)-1] ^= 1
		}
		if kind == 2 {
			clear(in[64:])
		}
	}
	p := &slhV2{}
	b.ReportAllocs()
	b.ResetTimer()
	processStart, threadStart := slhV2Clock(b, unix.CLOCK_PROCESS_CPUTIME_ID), slhV2Clock(b, unix.CLOCK_THREAD_CPUTIME_ID)
	defer slhV2ReportCPU(b, processStart, threadStart)
	for i := 0; i < b.N; i++ {
		out, err := p.Run(inputs[i%len(inputs)])
		if err != nil || len(out) != 32 || (out[31] == 1) != (kind == 0) {
			b.Fatal("wrong result")
		}
	}
}

func BenchmarkSLHV2ECRecover(b *testing.B) {
	runtime.LockOSThread()
	defer runtime.UnlockOSThread()
	vectors := slhV2Vectors(b)
	key, err := crypto.GenerateKey()
	if err != nil {
		b.Fatal(err)
	}
	expected := common.LeftPadBytes(crypto.PubkeyToAddress(key.PublicKey).Bytes(), 32)
	inputs := make([][]byte, len(vectors))
	for i, v := range vectors {
		sig, err := crypto.Sign(v[32:64], key)
		if err != nil {
			b.Fatal(err)
		}
		inputs[i] = make([]byte, 128)
		copy(inputs[i][:32], v[32:64])
		inputs[i][63] = sig[64] + 27
		copy(inputs[i][64:], sig[:64])
	}
	p := &ecrecover{}
	b.ReportAllocs()
	b.ResetTimer()
	processStart, threadStart := slhV2Clock(b, unix.CLOCK_PROCESS_CPUTIME_ID), slhV2Clock(b, unix.CLOCK_THREAD_CPUTIME_ID)
	defer slhV2ReportCPU(b, processStart, threadStart)
	for i := 0; i < b.N; i++ {
		out, err := p.Run(inputs[i%len(inputs)])
		if err != nil || !bytes.Equal(out, expected) {
			b.Fatal("wrong recovery")
		}
	}
}
