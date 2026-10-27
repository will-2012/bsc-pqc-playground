//go:build pqprecompile

// SPDX-License-Identifier: LGPL-3.0-or-later
package vm

import (
	"bytes"
	"encoding/hex"
	"encoding/json"
	"math/big"
	"os"
	"testing"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/params"
)

type slhV2Vector struct {
	Input  string `json:"input"`
	Source string `json:"source"`
}

func slhV2Vectors(tb testing.TB) [][]byte {
	tb.Helper()
	raw, err := os.ReadFile("testdata/slh_v2.json")
	if err != nil {
		tb.Fatal(err)
	}
	var vectors []slhV2Vector
	if err = json.Unmarshal(raw, &vectors); err != nil {
		tb.Fatal(err)
	}
	if len(vectors) < 4 {
		tb.Fatal("need multiple independent messages and keys")
	}
	out := make([][]byte, len(vectors))
	for i, v := range vectors {
		out[i], err = hex.DecodeString(v.Input)
		if err != nil || len(out[i]) != slhV2InputSize {
			tb.Fatal("invalid fixture")
		}
	}
	return out
}

func TestSLHV2Verify(t *testing.T) {
	p := &slhV2{}
	for _, input := range slhV2Vectors(t) {
		original := bytes.Clone(input)
		result, err := p.Run(input)
		if err != nil || len(result) != 32 || result[31] != 1 {
			t.Fatal("valid signature rejected", err)
		}
		if !bytes.Equal(original, input) {
			t.Fatal("input modified")
		}
		for _, index := range []int{0, 32, 64, len(input) - 1} {
			bad := bytes.Clone(input)
			bad[index] ^= 1
			result, err = p.Run(bad)
			if err != nil || !bytes.Equal(result, make([]byte, 32)) {
				t.Fatal("mutation accepted", index, err)
			}
		}
	}
	for _, size := range []int{0, 1, 7919, 7921, 10000} {
		result, err := p.Run(make([]byte, size))
		if err != nil || !bytes.Equal(result, make([]byte, 32)) {
			t.Fatal("malformed length accepted")
		}
	}
	input := slhV2Vectors(t)[0]
	_, _, err := RunPrecompiledContract(nil, p, slhV2Address, input, NewGasBudget(SLHV2Gas-1), nil, params.Rules{}, BlockContext{})
	if err != ErrOutOfGas {
		t.Fatal("missing gas boundary", err)
	}
	result, _, err := RunPrecompiledContract(nil, p, slhV2Address, input, NewGasBudget(SLHV2Gas), nil, params.Rules{}, BlockContext{})
	if err != nil || result[31] != 1 {
		t.Fatal("exact gas rejected", err)
	}
}

func TestSLHV2Activation(t *testing.T) {
	cfg := &params.ChainConfig{ChainID: big.NewInt(714), Parlia: &params.ParliaConfig{}}
	rules := params.Rules{IsInBSC: true, IsJenner: true}
	base := PrecompiledContracts{common.BytesToAddress([]byte{1}): &ecrecover{}}
	if v2Precompiles(cfg, rules, base)[slhV2Address] == nil || len(base) != 1 {
		t.Fatal("activation or map mutation")
	}
	for _, id := range []int64{-714, 1, 56, 97} {
		other := *cfg
		other.ChainID = big.NewInt(id)
		if v2Precompiles(&other, rules, base)[slhV2Address] != nil {
			t.Fatal("wrong chain enabled")
		}
	}
	for _, r := range []params.Rules{{IsJenner: true}, {IsInBSC: true}, {IsInBSC: true, IsJenner: true, IsUBT: true}} {
		if v2Precompiles(cfg, r, base)[slhV2Address] != nil {
			t.Fatal("wrong fork enabled")
		}
	}
	noParlia := *cfg
	noParlia.Parlia = nil
	if v2Precompiles(&noParlia, rules, base)[slhV2Address] != nil {
		t.Fatal("non-Parlia enabled")
	}
	evm := &EVM{chainConfig: cfg, chainRules: rules}
	evm.SetPrecompiles(base)
	if evm.precompiles[slhV2Address] == nil {
		t.Fatal("RPC removed precompile")
	}
	zero := uint64(0)
	full := &params.ChainConfig{ChainID: big.NewInt(714), Parlia: &params.ParliaConfig{}, LondonBlock: big.NewInt(0), JennerTime: &zero}
	constructed := NewEVM(BlockContext{BlockNumber: big.NewInt(1), Time: 1}, nil, full, Config{})
	defer constructed.Release()
	if constructed.precompiles[slhV2Address] == nil {
		t.Fatal("EVM constructor missing precompile")
	}
}

func FuzzSLHV2(f *testing.F) {
	f.Add([]byte{})
	f.Add(make([]byte, slhV2InputSize))
	for _, input := range slhV2Vectors(f) {
		f.Add(input)
	}
	f.Fuzz(func(t *testing.T, input []byte) {
		before := bytes.Clone(input)
		a, err := (&slhV2{}).Run(input)
		if err != nil || len(a) != 32 || a[31] > 1 {
			t.Fatal("invalid result")
		}
		if len(input) != slhV2InputSize && a[31] != 0 {
			t.Fatal("malformed input accepted")
		}
		b, err := (&slhV2{}).Run(input)
		if err != nil || !bytes.Equal(a, b) || !bytes.Equal(input, before) {
			t.Fatal("nondeterminism or mutation")
		}
	})
}
