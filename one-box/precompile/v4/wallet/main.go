// Local wallet helper: secrets pass only through stdin/stdout, never argv/files.
package main

import (
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"syscall"

	"github.com/ethereum/go-ethereum/crypto/pqfalcon"
	"github.com/ethereum/go-ethereum/crypto/pqmldsa"
)

type Request struct{ Action, Algorithm, PublicKey, SecretKey, Message, Signature string }

func decode(s string) []byte {
	b, e := hex.DecodeString(s)
	if e != nil {
		panic(e)
	}
	return b
}
func main() {
	serve := len(os.Args) == 2 && os.Args[1] == "--serve"
	if len(os.Args) > 1 && !serve {
		panic("unknown option")
	}
	if serve {
		if err := syscall.Setrlimit(syscall.RLIMIT_CORE, &syscall.Rlimit{Cur: 0, Max: 0}); err != nil {
			panic("cannot disable core dumps")
		}
	}
	decoder := json.NewDecoder(os.Stdin)
	for {
		var r Request
		if err := decoder.Decode(&r); err == io.EOF && serve {
			return
		} else if err != nil {
			panic("invalid request")
		}
		handle(r)
		if !serve {
			return
		}
	}
}
func handle(r Request) {
	keygen, sign, verify := pqmldsa.Keygen, pqmldsa.Sign, pqmldsa.Verify
	if r.Algorithm == "falcon" {
		keygen, sign, verify = pqfalcon.Keygen, pqfalcon.Sign, pqfalcon.Verify
	} else if r.Algorithm != "" && r.Algorithm != "mldsa" {
		panic("unknown algorithm")
	}
	out := map[string]interface{}{}
	switch r.Action {
	case "keygen":
		p, s, e := keygen()
		if e != nil {
			panic(e)
		}
		out["publicKey"] = hex.EncodeToString(p)
		out["secretKey"] = hex.EncodeToString(s)
		defer clear(s)
	case "sign", "signVerify":
		s := decode(r.SecretKey)
		defer clear(s)
		sig, e := sign(s, decode(r.Message))
		if e != nil {
			panic(e)
		}
		if r.Action == "signVerify" && !verify(decode(r.PublicKey), decode(r.Message), sig) {
			panic("signature verification failed")
		}
		out["signature"] = hex.EncodeToString(sig)
	case "verify":
		out["valid"] = verify(decode(r.PublicKey), decode(r.Message), decode(r.Signature))
	default:
		fmt.Fprintln(os.Stderr, "unknown action")
		os.Exit(1)
	}
	if e := json.NewEncoder(os.Stdout).Encode(out); e != nil {
		panic(e)
	}
}
