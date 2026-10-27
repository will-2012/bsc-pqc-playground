//go:build !pqprecompile

package vm

import "github.com/ethereum/go-ethereum/params"

func v4Precompiles(_ *params.ChainConfig, _ params.Rules, base PrecompiledContracts) PrecompiledContracts {
	return base
}
