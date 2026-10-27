// One Go API and one CGO boundary per operation for every algorithm.
package main

/*
#include "native.h"
#include <stdlib.h>
*/
import "C"

import (
	"bufio"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"runtime"
	"runtime/debug"
	"strconv"
	"strings"
	"time"
	"unsafe"
)

const pool = 32
const maxSignature = 18000

var names = []string{"ECDSA-secp256k1", "Falcon-512", "ML-DSA-44", "ML-DSA-65", "SLH-DSA-SHA2-128s", "SLH-DSA-SHA2-128f"}
var pkBytes = []int{33, 896, 1312, 1952, 32, 32}
var skBytes = []int{32, 1280, 2560, 4032, 64, 64}
var sigBytes = []int{64, 0, 2420, 3309, 7856, 17088}

type key struct{ native *C.BenchKey }

func newKey(id int) *key {
	k := &key{C.bench_new(C.int(id))}
	need(k.native != nil, "key setup")
	return k
}
func (k *key) Close() { C.bench_free(k.native); k.native = nil }
func (k *key) Sign(msg, out []byte) int {
	n := int(C.bench_sign_into(k.native, (*C.uchar)(unsafe.Pointer(&msg[0])), C.size_t(len(msg)), (*C.uchar)(unsafe.Pointer(&out[0])), C.size_t(len(out))))
	runtime.KeepAlive(msg)
	runtime.KeepAlive(out)
	return n
}
func (k *key) Verify(msg, sig []byte) bool {
	if len(msg) == 0 || len(sig) == 0 {
		return false
	}
	valid := C.bench_verify(k.native, (*C.uchar)(unsafe.Pointer(&msg[0])), C.size_t(len(msg)), (*C.uchar)(unsafe.Pointer(&sig[0])), C.size_t(len(sig))) == 1
	runtime.KeepAlive(msg)
	runtime.KeepAlive(sig)
	return valid
}
func (k *key) CrossVerify(msg, sig []byte) bool {
	return C.bench_cross_verify(k.native, (*C.uchar)(unsafe.Pointer(&msg[0])), C.size_t(len(msg)), (*C.uchar)(unsafe.Pointer(&sig[0])), C.size_t(len(sig))) == 1
}
func need(ok bool, why string) {
	if !ok {
		panic(why)
	}
}
func emit(v any) { need(json.NewEncoder(os.Stdout).Encode(v) == nil, "JSON output") }
func number(i int) int {
	n, e := strconv.Atoi(os.Args[i])
	need(e == nil, "integer argument")
	return n
}
func cpu() uint64        { return uint64(C.bench_thread_cpu_ns()) }
func processCPU() uint64 { return uint64(C.bench_process_cpu_ns()) }

func standardVectors(path string) {
	file, err := os.Open(path)
	need(err == nil, "standard vectors")
	defer file.Close()
	scan := bufio.NewScanner(file)
	scan.Buffer(make([]byte, 65536), 1024*1024)
	count := 0
	decode := func(s string) []byte {
		if s == "-" {
			return []byte{}
		}
		b, e := hex.DecodeString(s)
		need(e == nil, "vector hex")
		return b
	}
	ptr := func(b []byte) *C.uchar {
		if len(b) == 0 {
			return nil
		}
		return (*C.uchar)(unsafe.Pointer(&b[0]))
	}
	for scan.Scan() {
		f := strings.Split(scan.Text(), "\t")
		need(len(f) == 6, "vector fields")
		pk, msg, sig, ctx := decode(f[1]), decode(f[2]), decode(f[3]), decode(f[4])
		algo := C.CString(f[0])
		valid := C.bench_standard_verify(algo, ptr(pk), C.size_t(len(pk)), ptr(msg), C.size_t(len(msg)), ptr(sig), C.size_t(len(sig)), ptr(ctx), C.size_t(len(ctx))) == 1
		C.free(unsafe.Pointer(algo))
		need(valid == (f[5] == "1"), fmt.Sprintf("public standard vector %d %s expected=%s actual=%v", count, f[0], f[5], valid))
		count++
	}
	need(scan.Err() == nil && count == 24, "standard vector count")
	emit(map[string]any{"standard_vectors": "PASS", "cases": count})
}

func main() {
	if len(os.Args) == 3 && os.Args[1] == "--kat" {
		standardVectors(os.Args[2])
		return
	}
	need(len(os.Args) == 7, "bench ROUND SAMPLES SIGN_COUNT VERIFY_COUNT CORPUS ALGORITHM_ID")
	round, samples, signCount, verifyCount, id := number(1), number(2), number(3), number(4), number(6)
	need(round >= 0 && round < 100 && samples > 0 && samples <= 100 && id >= 0 && id < len(names) && signCount >= pool && verifyCount >= pool && signCount%pool == 0 && verifyCount%pool == 0, "argument range")
	runtime.GOMAXPROCS(1)
	runtime.LockOSThread()
	defer runtime.UnlockOSThread()
	// This is a preallocated API benchmark. No Go allocations are required by
	// Sign/Verify; disabling GC removes unrelated collections from the window.
	previousGC := debug.SetGCPercent(-1)
	defer debug.SetGCPercent(previousGC)
	need(C.bench_setup_thread() == 1, "thread QoS")
	corpus, e := os.ReadFile(os.Args[5])
	need(e == nil && len(corpus) >= pool*32 && len(corpus)%32 == 0, "corpus")
	messages := len(corpus) / 32
	setup := cpu()
	k := newKey(id)
	setup = cpu() - setup
	defer k.Close()
	pk := make([]byte, 2000)
	n := int(C.bench_public_key(k.native, (*C.uchar)(unsafe.Pointer(&pk[0])), C.size_t(len(pk))))
	need(n == pkBytes[id], "public key size")
	other := newKey(id)
	defer other.Close()
	var msg [pool][32]byte
	var sig [pool][maxSignature]byte
	var lengths [pool]int
	sequence := 0
	signOne := func(slot int) {
		copy(msg[slot][:], corpus[(sequence%messages)*32:(sequence%messages+1)*32])
		sequence++
		lengths[slot] = k.Sign(msg[slot][:], sig[slot][:])
		length := lengths[slot]
		if id == 1 {
			need(length >= 617 && length <= 667 && sig[slot][0] == 0x39, "Falcon encoding")
		} else {
			need(length == sigBytes[id], "signature size")
		}
	}
	for i := 0; i < pool; i++ {
		signOne(i)
		s := sig[i][:lengths[i]]
		need(k.Verify(msg[i][:], s), "valid signature")
		need(k.CrossVerify(msg[i][:], s), "cross verification")
		msg[i][31] ^= 1
		need(!k.Verify(msg[i][:], s), "changed message")
		msg[i][31] ^= 1
		sig[i][lengths[i]-1] ^= 1
		need(!k.Verify(msg[i][:], s), "changed signature")
		sig[i][lengths[i]-1] ^= 1
		need(!k.Verify(msg[i][:], s[:len(s)-1]), "truncated signature")
		need(!other.Verify(msg[i][:], s), "wrong key")
	}
	batch := func(signing bool, count int) (uint64, int64, uint64) {
		wallStart := time.Now()
		processStart := processCPU()
		start := cpu()
		for i := 0; i < count; i++ {
			slot := i % pool
			if signing {
				signOne(slot)
			} else {
				need(k.Verify(msg[slot][:], sig[slot][:lengths[slot]]), "timed verification")
			}
		}
		elapsed := cpu() - start
		processElapsed := processCPU() - processStart
		wall := time.Since(wallStart).Nanoseconds()
		if signing {
			for i := 0; i < pool; i++ {
				need(k.Verify(msg[i][:], sig[i][:lengths[i]]), "post-batch verification")
			}
		}
		return elapsed, wall, processElapsed
	}
	// Same untimed warm-up batches for all six ahead-of-time compiled backends.
	batch(true, signCount)
	batch(false, verifyCount)
	order := (id - round%len(names) + len(names)) % len(names)
	emit(map[string]any{"kind": "check", "round": round, "order": order, "algorithm": names[id], "private_key_bytes": skBytes[id], "public_key_bytes": pkBytes[id], "signature_min_bytes": func() int {
		if id == 1 {
			return 617
		}
		return sigBytes[id]
	}(), "signature_max_bytes": func() int {
		if id == 1 {
			return 667
		}
		return sigBytes[id]
	}(), "vectors": pool, "checks_per_vector": 5, "key_setup_cpu_ns": setup, "cpu_features": C.GoString(C.bench_cpu_features()), "warmup_sign_count": signCount, "warmup_verify_count": verifyCount, "go_version": runtime.Version()})
	sequence = round * samples * signCount
	for _, signing := range []bool{true, false} {
		operation, count := "verify", verifyCount
		if signing {
			operation, count = "sign", signCount
		}
		for sample := 0; sample < samples; sample++ {
			thread, wall, process := batch(signing, count)
			start := (round*samples*signCount + samples*signCount - pool) % messages
			if signing {
				start = (round*samples*signCount + sample*signCount) % messages
			}
			low, high := maxSignature, 0
			for _, n := range lengths {
				if n < low {
					low = n
				}
				if n > high {
					high = n
				}
			}
			emit(map[string]any{"kind": "timing", "round": round, "order": order, "algorithm": names[id], "operation": operation, "sample": sample, "iterations": count, "message_start_index": start, "cpu_ns": thread, "wall_ns": wall, "process_cpu_ns": process, "signature_observed_min_bytes": low, "signature_observed_max_bytes": high})
		}
	}
	// Keep preallocated storage alive across every C call, without a Go finalizer.
	runtime.KeepAlive(k)
	runtime.KeepAlive(corpus)
}
