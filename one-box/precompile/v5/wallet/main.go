// Local wallet helper: secrets pass only through stdin/stdout, never argv/files.
package main

import (
	"encoding/hex"
	"encoding/json"
	"fmt"
	"github.com/ethereum/go-ethereum/crypto/pqfalcon"
	"github.com/ethereum/go-ethereum/crypto/pqmldsa"
	"os"
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
	var r Request
	if e := json.NewDecoder(os.Stdin).Decode(&r); e != nil {
		panic(e)
	}
	keygen, sign, verify := pqmldsa.Keygen65, pqmldsa.Sign65, pqmldsa.Verify65
	if r.Algorithm == "falcon" {
		keygen, sign, verify = pqfalcon.Keygen, pqfalcon.Sign, pqfalcon.Verify
	} else if r.Algorithm == "mldsa44" {
		keygen, sign, verify = pqmldsa.Keygen, pqmldsa.Sign, pqmldsa.Verify
	} else if r.Algorithm != "" && r.Algorithm != "mldsa65" {
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
	case "sign":
		s := decode(r.SecretKey)
		defer clear(s)
		sig, e := sign(s, decode(r.Message))
		if e != nil {
			panic(e)
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
