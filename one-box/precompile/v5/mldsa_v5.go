//go:build pqprecompile

package vm

import (
	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/crypto/pqmldsa"
	"github.com/ethereum/go-ethereum/params"
	"maps"
)

const MLDSAV5Gas uint64 = 10000 // Recalibrated by the same measured pricing rule as v2.
var mldsaV5Address = common.HexToAddress("0x0104")

type mldsaV5 struct{}

func (*mldsaV5) Name() string              { return "MLDSA65_V5" }
func (*mldsaV5) RequiredGas([]byte) uint64 { return MLDSAV5Gas }

// ABI: public key[1952] || message[32] || signature[3309]; Pure ML-DSA, empty context.
func (*mldsaV5) Run(input []byte) ([]byte, error) {
	out := make([]byte, 32)
	if len(input) == 1952+32+3309 && pqmldsa.Verify65(input[:1952], input[1952:1984], input[1984:]) {
		out[31] = 1
	}
	return out, nil
}
func v5Precompiles(cfg *params.ChainConfig, rules params.Rules, base PrecompiledContracts) PrecompiledContracts {
	r := v4Precompiles(cfg, rules, base)
	if r[mldsaV4Address] == nil {
		if r[mldsaV5Address] != nil {
			r = maps.Clone(r)
			delete(r, mldsaV5Address)
		}
		return r
	}
	r = maps.Clone(r)
	r[mldsaV5Address] = &mldsaV5{}
	return r
}
