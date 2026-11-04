//go:build pqprecompile && linux

package vm

import (
	"bytes"
	"crypto/rand"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"runtime"
	"testing"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/crypto"
	"github.com/ethereum/go-ethereum/crypto/pqmldsa"
	"github.com/trailofbits/go-slh-dsa/slh_dsa"
	"golang.org/x/sys/cpu"
	"golang.org/x/sys/unix"
)

// This file is copied into the built client's core/vm test package. No Run
// implementation is replaced; the same private precompile types are exercised.
func pricingInput(tb testing.TB, family, kind string) [][]byte {
	tb.Helper()
	inputs := slhV2Vectors(tb)
	if family == "ML" {
		inputs = mldsaV4Vectors(tb)
	}
	for i, original := range inputs {
		in := bytes.Clone(original)
		messageOffset, signatureOffset := 32, 64
		if family == "ML" {
			messageOffset, signatureOffset = 1312, 1344
		}
		switch kind {
		case "Valid":
		case "WrongMessage":
			in[messageOffset] ^= 1
		case "Invalid":
			in[len(in)-1] ^= 1
		case "Zero":
			clear(in[signatureOffset:])
		case "Random":
			for off := signatureOffset; off < len(in); off += 32 {
				block := sha256.Sum256([]byte{byte(i), byte(off), byte(off >> 8)})
				copy(in[off:], block[:])
			}
		case "Short":
			in = in[:len(in)-1]
		case "Long":
			in = append(in, 0)
		default:
			tb.Fatal("unknown case")
		}
		inputs[i] = in
	}
	return inputs
}

func pricingPrecompile(family string) PrecompiledContract {
	if family == "ML" {
		return &mldsaV4{}
	}
	return &slhV2{}
}

func TestPricingPaths(t *testing.T) {
	for _, family := range []string{"ML", "SLH"} {
		p := pricingPrecompile(family)
		for _, kind := range []string{"Valid", "WrongMessage", "Invalid", "Zero", "Random", "Short", "Long"} {
			for _, in := range pricingInput(t, family, kind) {
				before := bytes.Clone(in)
				out, err := p.Run(in)
				if err != nil || len(out) != 32 || (out[31] == 1) != (kind == "Valid") || !bytes.Equal(before, in) {
					t.Fatalf("%s/%s result or mutation", family, kind)
				}
			}
		}
	}
}

func BenchmarkPricing(b *testing.B) {
	for _, family := range []string{"ML", "SLH"} {
		for _, kind := range []string{"Valid", "WrongMessage", "Invalid", "Zero", "Random", "Short", "Long"} {
			b.Run(family+"_"+kind, func(b *testing.B) {
				runtime.LockOSThread()
				defer runtime.UnlockOSThread()
				inputs, p := pricingInput(b, family, kind), pricingPrecompile(family)
				b.ReportAllocs()
				b.ResetTimer()
				process, thread := slhV2Clock(b, unix.CLOCK_PROCESS_CPUTIME_ID), slhV2Clock(b, unix.CLOCK_THREAD_CPUTIME_ID)
				for i := 0; i < b.N; i++ {
					out, err := p.Run(inputs[i%len(inputs)])
					if err != nil || len(out) != 32 || (out[31] == 1) != (kind == "Valid") {
						b.Fatal("incorrect timed result")
					}
				}
				b.StopTimer()
				slhV2ReportCPU(b, process, thread)
				b.ReportMetric(float64(p.RequiredGas(inputs[0])), "gas/op")
				b.ReportMetric(float64(len(inputs)), "vectors")
			})
		}
	}
}

func BenchmarkPricingECDSA(b *testing.B) {
	vectors := slhV2Vectors(b)
	key, err := crypto.GenerateKey()
	if err != nil {
		b.Fatal(err)
	}
	messages, signatures, recovery := make([][]byte, len(vectors)), make([][]byte, len(vectors)), make([][]byte, len(vectors))
	for i, v := range vectors {
		messages[i] = v[32:64]
		signatures[i], err = crypto.Sign(messages[i], key)
		if err != nil {
			b.Fatal(err)
		}
		in := make([]byte, 128)
		copy(in, messages[i])
		in[63] = signatures[i][64] + 27
		copy(in[64:], signatures[i][:64])
		recovery[i] = in
	}
	public := crypto.FromECDSAPub(&key.PublicKey)
	expected := common.LeftPadBytes(crypto.PubkeyToAddress(key.PublicKey).Bytes(), 32)
	for _, kind := range []string{"ECRecover", "Verify", "Sign"} {
		b.Run(kind, func(b *testing.B) {
			runtime.LockOSThread()
			defer runtime.UnlockOSThread()
			p := &ecrecover{}
			var last []byte
			b.ReportAllocs()
			b.ResetTimer()
			process, thread := slhV2Clock(b, unix.CLOCK_PROCESS_CPUTIME_ID), slhV2Clock(b, unix.CLOCK_THREAD_CPUTIME_ID)
			for i := 0; i < b.N; i++ {
				j := i % len(vectors)
				switch kind {
				case "ECRecover":
					out, e := p.Run(recovery[j])
					if e != nil || !bytes.Equal(out, expected) {
						b.Fatal("recovery mismatch")
					}
				case "Verify":
					if !crypto.VerifySignature(public, messages[j], signatures[j][:64]) {
						b.Fatal("verify mismatch")
					}
				case "Sign":
					var e error
					last, e = crypto.Sign(messages[j], key)
					if e != nil {
						b.Fatal(e)
					}
				}
			}
			b.StopTimer()
			slhV2ReportCPU(b, process, thread)
			if kind == "Sign" && !crypto.VerifySignature(public, messages[(b.N-1)%len(vectors)], last[:64]) {
				b.Fatal("sign mismatch")
			}
		})
	}
}

func BenchmarkPricingSign(b *testing.B) {
	messages := slhV2Vectors(b)
	for _, family := range []string{"ML", "SLH"} {
		b.Run(family, func(b *testing.B) {
			runtime.LockOSThread()
			defer runtime.UnlockOSThread()
			var sign func([]byte) error
			var check func([]byte) bool
			if family == "ML" {
				pk, sk, e := pqmldsa.Keygen()
				if e != nil {
					b.Fatal(e)
				}
				defer clear(sk)
				var last []byte
				sign = func(m []byte) error { var e error; last, e = pqmldsa.Sign(sk, m); return e }
				check = func(m []byte) bool { return pqmldsa.Verify(pk, m, last) }
			} else {
				sk, pk, e := slh_dsa.SLHKeygen(slh_dsa.SlhDsaSha2_128s())
				if e != nil {
					b.Fatal(e)
				}
				var last slh_dsa.Signature
				sign = func(m []byte) error { var e error; last, e = slh_dsa.SLHSign(rand.Reader, m, nil, sk); return e }
				check = func(m []byte) bool { return pk.Verify(last, m, nil) }
			}
			if e := sign(messages[0][32:64]); e != nil || !check(messages[0][32:64]) {
				b.Fatal("sign preflight")
			}
			b.ReportAllocs()
			b.ResetTimer()
			process, thread := slhV2Clock(b, unix.CLOCK_PROCESS_CPUTIME_ID), slhV2Clock(b, unix.CLOCK_THREAD_CPUTIME_ID)
			for i := 0; i < b.N; i++ {
				if e := sign(messages[i%len(messages)][32:64]); e != nil {
					b.Fatal(e)
				}
			}
			b.StopTimer()
			slhV2ReportCPU(b, process, thread)
			if !check(messages[(b.N-1)%len(messages)][32:64]) {
				b.Fatal("sign postflight")
			}
		})
	}
}

func TestPricingEnvironment(t *testing.T) {
	data, _ := json.Marshal(map[string]interface{}{"go": runtime.Version(), "arch": runtime.GOARCH, "avx2": cpu.X86.HasAVX2, "bmi2": cpu.X86.HasBMI2, "avx512f": cpu.X86.HasAVX512F})
	fmt.Println("PRICING_ENV " + string(data))
}
