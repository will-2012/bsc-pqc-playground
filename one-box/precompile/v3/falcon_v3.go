//go:build pqprecompile

package vm

import (
	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/crypto/pqfalcon"
	"github.com/ethereum/go-ethereum/params"
	"maps"
)

const FalconV3Gas uint64 = 10000 // Recalibrated by the same measured pricing rule as v2.
var falconV3Address = common.HexToAddress("0x0102")

type falconV3 struct{}

func (*falconV3) Name() string              { return "FALCON512_V3" }
func (*falconV3) RequiredGas([]byte) uint64 { return FalconV3Gas }

// ABI: pk body[896] || message[32] || compressed signature[617..667].
func (*falconV3) Run(input []byte) ([]byte, error) {
	out := make([]byte, 32)
	if len(input) >= 896+32+617 && len(input) <= 896+32+667 && pqfalcon.Verify(input[:896], input[896:928], input[928:]) {
		out[31] = 1
	}
	return out, nil
}
func v3Precompiles(cfg *params.ChainConfig, rules params.Rules, base PrecompiledContracts) PrecompiledContracts {
	active := cfg != nil && cfg.ChainID != nil && cfg.ChainID.Sign() > 0 && cfg.ChainID.BitLen() <= 64 && cfg.ChainID.Uint64() == 714 && cfg.Parlia != nil && rules.IsInBSC && rules.IsPQ && !rules.IsUBT
	if !active {
		if base[slhV2Address] == nil && base[falconV3Address] == nil {
			return base
		}
		r := maps.Clone(base)
		delete(r, slhV2Address)
		delete(r, falconV3Address)
		return r
	}
	r := v2Precompiles(cfg, rules, base)
	r[falconV3Address] = &falconV3{}
	return r
}
