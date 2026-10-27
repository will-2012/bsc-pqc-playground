//go:build pqprecompile

package vm

import (
	"bytes"
	"encoding/hex"
	"github.com/ethereum/go-ethereum/crypto/pqfalcon"
	"github.com/ethereum/go-ethereum/params"
	"math/big"
	"os"
	"strings"
	"testing"
)

func falconV3Vectors(t testing.TB) [][]byte {
	t.Helper()
	raw, e := os.ReadFile("testdata/falcon_v3.tsv")
	if e != nil {
		t.Fatal(e)
	}
	var out [][]byte
	for _, line := range strings.Split(strings.TrimSpace(string(raw)), "\n") {
		f := strings.Split(line, "\t")
		if len(f) != 3 {
			t.Fatal("bad KAT")
		}
		p, _ := hex.DecodeString(f[0])
		m, _ := hex.DecodeString(f[1])
		s, _ := hex.DecodeString(f[2])
		if !pqfalcon.Verify(p, m, s) {
			t.Fatal("independent BC KAT failed")
		}
	}
	for key := 0; key < 2; key++ {
		p, s, e := pqfalcon.Keygen()
		if e != nil {
			t.Fatal(e)
		}
		for _, vector := range slhV2Vectors(t) {
			m := vector[32:64]
			sig, e := pqfalcon.Sign(s, m)
			if e != nil {
				t.Fatal(e)
			}
			out = append(out, append(append(bytes.Clone(p), m...), sig...))
		}
		clear(s)
	}
	return out
}
func TestFalconV3Verify(t *testing.T) {
	p := &falconV3{}
	for _, in := range falconV3Vectors(t) {
		before := bytes.Clone(in)
		out, e := p.Run(in)
		if e != nil || out[31] != 1 || !bytes.Equal(in, before) {
			t.Fatal("valid/input mutation")
		}
		for _, idx := range []int{0, 896, 928, len(in) - 1} {
			bad := bytes.Clone(in)
			bad[idx] ^= 1
			out, _ := p.Run(bad)
			if out[31] != 0 {
				t.Fatal("mutation accepted", idx)
			}
		}
		for _, bad := range [][]byte{in[:len(in)-1], append(bytes.Clone(in), 0)} {
			out, _ := p.Run(bad)
			if out[31] != 0 {
				t.Fatal("noncanonical length accepted")
			}
		}
		_, _, e = RunPrecompiledContract(nil, p, falconV3Address, in, NewGasBudget(FalconV3Gas-1), nil, params.Rules{}, BlockContext{})
		if e != ErrOutOfGas {
			t.Fatal("gas boundary")
		}
		out, _, e = RunPrecompiledContract(nil, p, falconV3Address, in, NewGasBudget(FalconV3Gas), nil, params.Rules{}, BlockContext{})
		if e != nil || out[31] != 1 {
			t.Fatal("exact gas")
		}
	}
	for _, n := range []int{0, 1, 1544, 1596, 10000} {
		out, _ := p.Run(make([]byte, n))
		if out[31] != 0 {
			t.Fatal("bad size")
		}
	}
}
func TestFalconV3CommonFork(t *testing.T) {
	zero, pq := uint64(0), uint64(100)
	cfg := &params.ChainConfig{ChainID: big.NewInt(714), Parlia: &params.ParliaConfig{}, LondonBlock: big.NewInt(0), JennerTime: &zero, PQTime: &pq}
	base := PrecompiledContracts{}
	for _, time := range []uint64{99, 100, 101} {
		rules := cfg.Rules(big.NewInt(1), false, time)
		r := v3Precompiles(cfg, rules, base)
		want := time >= pq
		if (r[slhV2Address] != nil) != want || (r[falconV3Address] != nil) != want || len(base) != 0 {
			t.Fatal("fork/mutation", time)
		}
		evm := NewEVM(BlockContext{BlockNumber: big.NewInt(1), Time: time}, nil, cfg, Config{})
		evm.SetPrecompiles(base)
		if (evm.precompiles[slhV2Address] != nil) != want || (evm.precompiles[falconV3Address] != nil) != want {
			t.Fatal("RPC gate", time)
		}
		evm.Release()
	}
	for _, id := range []int64{-714, 1, 56, 97} {
		other := *cfg
		other.ChainID = big.NewInt(id)
		r := v3Precompiles(&other, other.Rules(big.NewInt(1), false, 101), base)
		if len(r) != 0 {
			t.Fatal("wrong chain")
		}
	}
	other := *cfg
	other.PQTime = nil
	if len(v3Precompiles(&other, other.Rules(big.NewInt(1), false, 101), base)) != 0 {
		t.Fatal("nil fork")
	}
	other = *cfg
	later := uint64(101)
	other.PQTime = &later
	if cfg.CheckCompatible(&other, 1, 100) == nil {
		t.Fatal("activated fork changed")
	}
}
func BenchmarkFalconV3Valid(b *testing.B)         { benchmarkFalconV3(b, 0) }
func BenchmarkFalconV3Invalid(b *testing.B)       { benchmarkFalconV3(b, 1) }
func BenchmarkFalconV3WrongMessage(b *testing.B)  { benchmarkFalconV3(b, 3) }
func BenchmarkFalconV3ZeroSignature(b *testing.B) { benchmarkFalconV3(b, 2) }
