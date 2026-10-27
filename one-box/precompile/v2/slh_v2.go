//go:build pqprecompile

// SPDX-License-Identifier: LGPL-3.0-or-later
package vm

import (
	"maps"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/params"
	"github.com/trailofbits/go-slh-dsa/slh_dsa"
)

// Experimental ABI: publicKey[32] || message[32] || signature[7856].
// Pure SLH-DSA-SHA2-128s, empty context; returns a 32-byte boolean.
const SLHV2Gas uint64 = 60000
const slhV2InputSize = 7920

var slhV2Address = common.HexToAddress("0x0000000000000000000000000000000000000101")

type slhV2 struct{}

func (*slhV2) Name() string              { return "SLH128S_V2" }
func (*slhV2) RequiredGas([]byte) uint64 { return SLHV2Gas }
func (*slhV2) Run(input []byte) ([]byte, error) {
	result := make([]byte, 32)
	if len(input) != slhV2InputSize {
		return result, nil
	}
	ps := slh_dsa.SlhDsaSha2_128s()
	pk, err := slh_dsa.LoadPublicKey(ps, input[:32])
	if err != nil {
		return result, nil
	}
	sig, err := slh_dsa.LoadSignature(ps, input[64:])
	if err != nil {
		return result, nil
	}
	if pk.Verify(sig, input[32:64], nil) {
		result[31] = 1
	}
	return result, nil
}

// Do not mutate public fork maps or enable this extension on other networks.
// RPC SetPrecompiles must apply the same gate as ordinary EVM construction.
func v2Precompiles(cfg *params.ChainConfig, rules params.Rules, base PrecompiledContracts) PrecompiledContracts {
	active := cfg != nil && cfg.ChainID != nil && cfg.ChainID.Sign() > 0 && cfg.ChainID.Uint64() == 714 && cfg.ChainID.BitLen() <= 64 &&
		cfg.Parlia != nil && rules.IsInBSC && rules.IsJenner && !rules.IsUBT
	if !active && base[slhV2Address] == nil {
		return base
	}
	result := maps.Clone(base)
	if result == nil {
		result = make(PrecompiledContracts)
	}
	if active {
		result[slhV2Address] = &slhV2{}
	} else {
		delete(result, slhV2Address)
	}
	return result
}
