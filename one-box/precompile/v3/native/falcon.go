// Package pqfalcon exposes the pinned original Falcon-512 ARMv8 backend.
// Public/private keys omit the fixed Falcon header (TRON body encoding).
package pqfalcon

/*
#cgo CFLAGS: -O3 -DFALCON_LOGN=9 -DAPPLE_M1=0 -Dfalcon_verify=falcon_neon_verify -I${SRCDIR}/backend -fno-fast-math -ffp-contract=off
#cgo darwin,arm64 CFLAGS: -mcpu=apple-m1
#include "bridge.h"
*/
import "C"
import (
	"errors"
	"unsafe"
)

func ptr(b []byte) *C.uchar {
	if len(b) == 0 {
		return nil
	}
	return (*C.uchar)(unsafe.Pointer(&b[0]))
}
func Keygen() (pk, sk []byte, err error) {
	pk, sk = make([]byte, 896), make([]byte, 1280)
	if C.pq_keygen(ptr(pk), ptr(sk)) != 1 {
		return nil, nil, errors.New("Falcon keygen failed")
	}
	return
}
func Sign(sk, msg []byte) ([]byte, error) {
	if len(sk) != 1280 {
		return nil, errors.New("Falcon private key must be 1280 bytes")
	}
	sig := make([]byte, 667)
	n := C.size_t(len(sig))
	if C.pq_sign(ptr(sk), ptr(msg), C.size_t(len(msg)), ptr(sig), &n) != 1 {
		return nil, errors.New("Falcon signing failed")
	}
	return sig[:int(n)], nil
}
func Verify(pk, msg, sig []byte) bool {
	if len(pk) != 896 || len(sig) < 617 || len(sig) > 667 {
		return false
	}
	return C.pq_verify(ptr(pk), ptr(msg), C.size_t(len(msg)), ptr(sig), C.size_t(len(sig))) == 1
}
