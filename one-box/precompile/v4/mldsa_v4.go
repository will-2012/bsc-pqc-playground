//go:build pqprecompile

package vm

import (
	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/crypto/pqmldsa"
	"github.com/ethereum/go-ethereum/params"
	"maps"
)

const MLDSAV4Gas uint64 = 10000 // Recalibrated by the same measured pricing rule as v2.
var mldsaV4Address = common.HexToAddress("0x0103")

type mldsaV4 struct{}

func (*mldsaV4) Name() string              { return "MLDSA44_V4" }
func (*mldsaV4) RequiredGas([]byte) uint64 { return MLDSAV4Gas }

// ABI: public key[1312] || message[32] || signature[2420]; Pure ML-DSA, empty context.
func (*mldsaV4) Run(input []byte) ([]byte, error) {
	out := make([]byte, 32)
	if len(input) == 1312+32+2420 && pqmldsa.Verify(input[:1312], input[1312:1344], input[1344:]) {
		out[31] = 1
	}
	return out, nil
}
func v4Precompiles(cfg *params.ChainConfig, rules params.Rules, base PrecompiledContracts) PrecompiledContracts {
	r := v3Precompiles(cfg, rules, base)
	if r[slhV2Address] == nil || r[falconV3Address] == nil {
		if r[mldsaV4Address] != nil {
			r = maps.Clone(r)
			delete(r, mldsaV4Address)
		}
		return r
	}
	r = maps.Clone(r)
	r[mldsaV4Address] = &mldsaV4{}
	return r
}
