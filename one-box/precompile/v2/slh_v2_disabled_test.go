//go:build !pqprecompile

// SPDX-License-Identifier: LGPL-3.0-or-later
package vm

import (
	"math/big"
	"testing"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/params"
)

func TestSLHV2Disabled(t *testing.T) {
	cfg := &params.ChainConfig{ChainID: big.NewInt(714), Parlia: &params.ParliaConfig{}}
	rules := params.Rules{IsInBSC: true, IsJenner: true}
	base := PrecompiledContracts{common.BytesToAddress([]byte{1}): &ecrecover{}}
	result := v2Precompiles(cfg, rules, base)
	if len(result) != 1 || result[common.HexToAddress("0x101")] != nil {
		t.Fatal("ordinary build enabled v2")
	}
	evm := &EVM{chainConfig: cfg, chainRules: rules}
	evm.SetPrecompiles(base)
	if len(evm.precompiles) != 1 {
		t.Fatal("RPC enabled v2 without build tag")
	}
}
