// Package pqmldsa implements Pure FIPS 204 ML-DSA-65, empty context.
package pqmldsa

/*
#cgo CFLAGS: -O3 -I${SRCDIR}/backend
#cgo LDFLAGS: ${SRCDIR}/libmldsa65.a
#include "bridge65.h"
*/
import "C"
import (
	"errors"
)

func Keygen65() (pk, sk []byte, err error) {
	pk = make([]byte, 1952)
	sk = make([]byte, 4032)
	if C.pq_ml65_keygen(ptr(pk), ptr(sk)) != 0 {
		clear(sk)
		return nil, nil, errors.New("ML-DSA keygen failed")
	}
	return
}
func Sign65(sk, msg []byte) ([]byte, error) {
	if len(sk) != 4032 {
		return nil, errors.New("ML-DSA private key must be 4032 bytes")
	}
	sig := make([]byte, 3309)
	if C.pq_ml65_sign(ptr(sk), ptr(msg), C.size_t(len(msg)), ptr(sig)) != 0 {
		return nil, errors.New("ML-DSA signing failed")
	}
	return sig, nil
}
func Verify65(pk, msg, sig []byte) bool {
	return len(pk) == 1952 && len(sig) == 3309 && C.pq_ml65_verify(ptr(pk), ptr(msg), C.size_t(len(msg)), ptr(sig)) == 0
}
