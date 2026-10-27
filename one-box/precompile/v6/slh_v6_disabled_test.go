//go:build !pqprecompile

package vm

import (
	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/params"
	"math/big"
	"testing"
)

func TestSLHV6Disabled(t *testing.T) {
	zero := uint64(0)
	cfg := &params.ChainConfig{ChainID: big.NewInt(714), Parlia: &params.ParliaConfig{}, LondonBlock: big.NewInt(0), JennerTime: &zero, PQTime: &zero}
	evm := NewEVM(BlockContext{BlockNumber: big.NewInt(1), Time: 1}, nil, cfg, Config{})
	defer evm.Release()
	for _, address := range []string{"0x0101", "0x0102", "0x0103", "0x0104", "0x0105"} {
		if evm.precompiles[common.HexToAddress(address)] != nil {
			t.Fatal("ordinary build enabled PQ")
		}
	}
}
