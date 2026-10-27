//go:build !pqprecompile

// SPDX-License-Identifier: LGPL-3.0-or-later
package vm

import "github.com/ethereum/go-ethereum/params"

func v2Precompiles(_ *params.ChainConfig, _ params.Rules, base PrecompiledContracts) PrecompiledContracts {
	return base
}
