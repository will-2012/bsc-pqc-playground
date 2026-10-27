//go:build pqprecompile

package vm

import (
	"bytes"
	"crypto/rand"
	"encoding/hex"
	"github.com/ethereum/go-ethereum/params"
	"github.com/trailofbits/go-slh-dsa/slh_dsa"
	"math/big"
	"os"
	"strings"
	"testing"
)

func slhV6Verify(pk, msg, sig []byte) bool {
	ps := slh_dsa.SlhDsaSha2_128f()
	p, e := slh_dsa.LoadPublicKey(ps, pk)
	if e != nil {
		return false
	}
	s, e := slh_dsa.LoadSignature(ps, sig)
	return e == nil && p.Verify(s, msg, nil)
}
func slhV6Vectors(t testing.TB) [][]byte {
	t.Helper()
	raw, e := os.ReadFile("testdata/slh_v6.tsv")
	if e != nil {
		t.Fatal(e)
	}
	var out [][]byte
	for _, line := range strings.Split(strings.TrimSpace(string(raw)), "\n") {
		f := strings.Split(line, "\t")
		if len(f) != 6 {
			t.Fatal("bad KAT")
		}
		if f[0] != "SLH-DSA-SHA2-128f" || f[4] != "-" {
			continue
		}
		p, e := hex.DecodeString(f[1])
		if e != nil {
			t.Fatal(e)
		}
		m, e := hex.DecodeString(f[2])
		if e != nil {
			t.Fatal(e)
		}
		sig, e := hex.DecodeString(f[3])
		if e != nil {
			t.Fatal(e)
		}
		if slhV6Verify(p, m, sig) != (f[5] == "1") {
			t.Fatal("independent FIPS 205 KAT failed")
		}
	}
	for key := 0; key < 2; key++ {
		s, public, e := slh_dsa.SLHKeygen(slh_dsa.SlhDsaSha2_128f())
		p := public.Bytes()
		if e != nil {
			t.Fatal(e)
		}
		for _, vector := range slhV2Vectors(t) {
			m := vector[32:64]
			signed, e := slh_dsa.SLHSign(rand.Reader, m, nil, s)
			sig := signed.Bytes()
			if e != nil {
				t.Fatal(e)
			}
			out = append(out, append(append(bytes.Clone(p), m...), sig...))
		}
	}
	return out
}
func TestSLHV6Verify(t *testing.T) {
	p := &slhV6{}
	for _, in := range slhV6Vectors(t) {
		before := bytes.Clone(in)
		out, e := p.Run(in)
		if e != nil || out[31] != 1 || !bytes.Equal(in, before) {
			t.Fatal("valid/input mutation")
		}
		for _, idx := range []int{0, 32, 64, len(in) - 1} {
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
		_, _, e = RunPrecompiledContract(nil, p, slhV6Address, in, NewGasBudget(SLHV6Gas-1), nil, params.Rules{}, BlockContext{})
		if e != ErrOutOfGas {
			t.Fatal("gas boundary")
		}
		out, _, e = RunPrecompiledContract(nil, p, slhV6Address, in, NewGasBudget(SLHV6Gas), nil, params.Rules{}, BlockContext{})
		if e != nil || out[31] != 1 {
			t.Fatal("exact gas")
		}
	}
	for _, n := range []int{0, 1, 17151, 17153, 10000} {
		out, _ := p.Run(make([]byte, n))
		if out[31] != 0 {
			t.Fatal("bad size")
		}
	}
}
func TestSLHV6CommonFork(t *testing.T) {
	zero, pq := uint64(0), uint64(100)
	cfg := &params.ChainConfig{ChainID: big.NewInt(714), Parlia: &params.ParliaConfig{}, LondonBlock: big.NewInt(0), JennerTime: &zero, PQTime: &pq}
	base := PrecompiledContracts{}
	for _, time := range []uint64{99, 100, 101} {
		rules := cfg.Rules(big.NewInt(1), false, time)
		r := v6Precompiles(cfg, rules, base)
		want := time >= pq
		if (r[slhV2Address] != nil) != want || (r[slhV6Address] != nil) != want || (r[mldsaV5Address] != nil) != want || (r[mldsaV4Address] != nil) != want || (r[falconV3Address] != nil) != want || len(base) != 0 {
			t.Fatal("fork/mutation", time)
		}
		evm := NewEVM(BlockContext{BlockNumber: big.NewInt(1), Time: time}, nil, cfg, Config{})
		evm.SetPrecompiles(base)
		if (evm.precompiles[slhV2Address] != nil) != want || (evm.precompiles[slhV6Address] != nil) != want || (evm.precompiles[mldsaV5Address] != nil) != want || (evm.precompiles[mldsaV4Address] != nil) != want || (evm.precompiles[falconV3Address] != nil) != want {
			t.Fatal("RPC gate", time)
		}
		evm.Release()
	}
	for _, id := range []int64{-714, 1, 56, 97} {
		other := *cfg
		other.ChainID = big.NewInt(id)
		r := v6Precompiles(&other, other.Rules(big.NewInt(1), false, 101), base)
		if len(r) != 0 {
			t.Fatal("wrong chain")
		}
	}
	other := *cfg
	other.PQTime = nil
	if len(v6Precompiles(&other, other.Rules(big.NewInt(1), false, 101), base)) != 0 {
		t.Fatal("nil fork")
	}
	other = *cfg
	later := uint64(101)
	other.PQTime = &later
	if cfg.CheckCompatible(&other, 1, 100) == nil {
		t.Fatal("activated fork changed")
	}
}
func BenchmarkSLHV6Valid(b *testing.B)         { benchmarkSLHV6(b, 0) }
func BenchmarkSLHV6Invalid(b *testing.B)       { benchmarkSLHV6(b, 1) }
func BenchmarkSLHV6WrongMessage(b *testing.B)  { benchmarkSLHV6(b, 3) }
func BenchmarkSLHV6ZeroSignature(b *testing.B) { benchmarkSLHV6(b, 2) }
