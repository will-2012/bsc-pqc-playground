//go:build pqprecompile

// SPDX-License-Identifier: LGPL-3.0-or-later
package vm

import (
	"maps"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/params"
	"github.com/trailofbits/go-slh-dsa/slh_dsa"
)

// Experimental ABI: publicKey[32] || message[32] || signature[17088].
// Pure SLH-DSA-SHA2-128f, empty context; returns a 32-byte boolean.
const SLHV6Gas uint64 = 160000
const slhV6InputSize = 17152

var slhV6Address = common.HexToAddress("0x0000000000000000000000000000000000000105")

type slhV6 struct{}

func (*slhV6) Name() string              { return "SLH128F_V6" }
func (*slhV6) RequiredGas([]byte) uint64 { return SLHV6Gas }
func (*slhV6) Run(input []byte) ([]byte, error) {
	result := make([]byte, 32)
	if len(input) != slhV6InputSize {
		return result, nil
	}
	ps := slh_dsa.SlhDsaSha2_128f()
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

func v6Precompiles(cfg *params.ChainConfig, rules params.Rules, base PrecompiledContracts) PrecompiledContracts {
	r := v5Precompiles(cfg, rules, base)
	if r[mldsaV5Address] == nil {
		if r[slhV6Address] != nil {
			r = maps.Clone(r)
			delete(r, slhV6Address)
		}
		return r
	}
	r = maps.Clone(r)
	r[slhV6Address] = &slhV6{}
	return r
}
