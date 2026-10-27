// Local wallet helper: secrets pass only through stdin/stdout, never argv/files.
package main

import (
	"encoding/hex"
	"encoding/json"
	"fmt"
	"github.com/ethereum/go-ethereum/crypto/pqfalcon"
	"os"
)

type Request struct{ Action, PublicKey, SecretKey, Message, Signature string }

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
	out := map[string]interface{}{}
	switch r.Action {
	case "keygen":
		p, s, e := pqfalcon.Keygen()
		if e != nil {
			panic(e)
		}
		out["publicKey"] = hex.EncodeToString(p)
		out["secretKey"] = hex.EncodeToString(s)
		defer clear(s)
	case "sign":
		s := decode(r.SecretKey)
		defer clear(s)
		sig, e := pqfalcon.Sign(s, decode(r.Message))
		if e != nil {
			panic(e)
		}
		out["signature"] = hex.EncodeToString(sig)
	case "verify":
		out["valid"] = pqfalcon.Verify(decode(r.PublicKey), decode(r.Message), decode(r.Signature))
	default:
		fmt.Fprintln(os.Stderr, "unknown action")
		os.Exit(1)
	}
	if e := json.NewEncoder(os.Stdout).Encode(out); e != nil {
		panic(e)
	}
}
