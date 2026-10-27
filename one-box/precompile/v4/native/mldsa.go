// Package pqmldsa implements Pure FIPS 204 ML-DSA-44, empty context.
package pqmldsa

/*
#cgo CFLAGS: -O3 -I${SRCDIR}/backend
#cgo LDFLAGS: ${SRCDIR}/libmldsa44.a
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
	pk = make([]byte, 1312)
	sk = make([]byte, 2560)
	if C.pq_ml_keygen(ptr(pk), ptr(sk)) != 0 {
		clear(sk)
		return nil, nil, errors.New("ML-DSA keygen failed")
	}
	return
}
func Sign(sk, msg []byte) ([]byte, error) {
	if len(sk) != 2560 {
		return nil, errors.New("ML-DSA private key must be 2560 bytes")
	}
	sig := make([]byte, 2420)
	if C.pq_ml_sign(ptr(sk), ptr(msg), C.size_t(len(msg)), ptr(sig)) != 0 {
		return nil, errors.New("ML-DSA signing failed")
	}
	return sig, nil
}
func Verify(pk, msg, sig []byte) bool {
	return len(pk) == 1312 && len(sig) == 2420 && C.pq_ml_verify(ptr(pk), ptr(msg), C.size_t(len(msg)), ptr(sig)) == 0
}
