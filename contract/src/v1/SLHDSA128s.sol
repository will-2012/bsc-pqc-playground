// SPDX-License-Identifier: MIT
pragma solidity ^0.8.23;

/// Experimental FIPS 205 SLH-DSA-SHA2-128s verifier, pure mode, empty context.
/// Fixed parameters: n=16, h=63, d=7, h'=9, a=12, k=14, w=16.
contract SLHDSA128s {
    // FIPS 205 SHA2 compressed address: 1 + 8 + 1 + 4 + 4 + 4 = 22 bytes.
    function adr(uint8 layer, uint64 tree, uint8 kind, uint32 keypair, uint32 height, uint32 index)
        private pure returns (bytes memory)
    { return abi.encodePacked(layer, tree, kind, keypair, height, index); }

    function h(bytes16 seed, bytes memory address_, bytes memory input) private pure returns (bytes16) {
        // Pad the 16-byte public seed to one SHA-256 block, then apply the tweak.
        return bytes16(sha256(abi.encodePacked(seed, bytes32(0), bytes16(0), address_, input)));
    }

    function node(bytes calldata sig, uint256 offset) private pure returns (bytes16) {
        return bytes16(sig[offset:offset + 16]);
    }

    function put(bytes memory target, uint256 offset, bytes16 value) private pure {
        // Only replace this 16-byte element, preserving adjacent packed nodes.
        for (uint256 i; i < 16; ++i) target[offset+i] = value[i];
    }

    function fors(bytes calldata sig, bytes16 seed, bytes32 digest, uint64 tree, uint32 leaf)
        private pure returns (bytes16 root)
    {
        bytes memory roots = new bytes(224); // 14 FORS roots, each 16 bytes.
        uint256 offset = 16;
        for (uint32 i; i < 14; ++i) {
            uint32 idx = uint32((uint256(digest) >> (256 - 12 * (i+1))) & 4095) + (i << 12);
            bytes16 n = h(seed, adr(0, tree, 3, leaf, 0, idx), abi.encodePacked(node(sig,offset)));
            offset += 16;
            for (uint32 j; j < 12; ++j) {
                bytes16 sibling = node(sig,offset); offset += 16;
                bytes memory pair = idx & 1 == 0 ? abi.encodePacked(n,sibling) : abi.encodePacked(sibling,n);
                idx >>= 1;
                n = h(seed,adr(0,tree,3,leaf,j+1,idx),pair);
            }
            put(roots,16*i,n);
        }
        root = h(seed,adr(0,tree,4,leaf,0,0),roots);
    }

    function xmss(bytes calldata sig, uint256 offset, bytes16 seed, bytes16 message,
        uint8 layer, uint64 tree, uint32 leaf) private pure returns (bytes16 root)
    {
        uint32[35] memory lengths; // 32 message digits + 3 checksum digits, base 16.
        uint32 checksum;
        for (uint32 i; i < 32; ++i) {
            uint32 digit = uint32((uint128(message) >> (124 - 4*i)) & 15);
            lengths[i] = digit; checksum += 15-digit;
        }
        lengths[32] = checksum >> 8;
        lengths[33] = (checksum >> 4) & 15;
        lengths[34] = checksum & 15;
        bytes memory endpoints = new bytes(560);
        for (uint32 i; i < 35; ++i) {
            bytes16 n = node(sig,offset); offset += 16;
            for (uint32 j=lengths[i]; j<15; ++j)
                n = h(seed,adr(layer,tree,0,leaf,i,j),abi.encodePacked(n));
            put(endpoints,16*i,n);
        }
        root = h(seed,adr(layer,tree,1,leaf,0,0),endpoints);
        for (uint32 j; j<9; ++j) {
            bytes16 sibling = node(sig,offset); offset += 16;
            bytes memory pair = leaf & 1 == 0 ? abi.encodePacked(root,sibling) : abi.encodePacked(sibling,root);
            leaf >>= 1;
            root = h(seed,adr(layer,tree,2,0,j+1,leaf),pair);
        }
    }

    function verify(bytes32 publicKey, bytes32 message, bytes calldata signature) external pure returns (bool) {
        if (signature.length != 7856) return false;
        bytes16 seed = bytes16(publicKey);
        bytes16 randomizer = node(signature,0);
        // FIPS 205 pure-mode envelope M' = 0x00 || len(ctx)=0x00 || M.
        bytes32 hashed = sha256(abi.encodePacked(randomizer,publicKey,bytes2(0),message));
        // 30-byte H_msg output fits in one MGF1-SHA256 block.
        bytes32 digest = sha256(abi.encodePacked(randomizer,seed,hashed,uint32(0)));
        uint64 tree = uint64((uint256(digest) >> 32) & ((uint256(1)<<54)-1));
        uint32 leaf = uint32((uint256(digest) >> 16) & 511);
        bytes16 root = fors(signature,seed,digest,tree,leaf);
        // R + FORS occupies 16 + 14 * (1 + 12) * 16 = 2,928 bytes.
        // Each XMSS signature occupies (35 + 9) * 16 = 704 bytes.
        for (uint8 layer; layer<7; ++layer) {
            root = xmss(signature,2928+uint256(layer)*704,seed,root,layer,tree,leaf);
            leaf = uint32(tree & 511); tree >>= 9;
        }
        return root == bytes16(uint128(uint256(publicKey)));
    }
}
