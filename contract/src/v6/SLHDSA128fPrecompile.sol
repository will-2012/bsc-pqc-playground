// SPDX-License-Identifier: MIT
pragma solidity ^0.8.23;

/// v6 verifier. Same public ABI as the v1 Solidity verifier; native execution.
/// Experimental address, available only in the local v6 BSC client.
contract SLHDSA128fPrecompile {
    address public constant PRECOMPILE = address(0x105);

    function verify(bytes32 publicKey, bytes32 message, bytes calldata signature) external view returns (bool) {
        if (signature.length != 17088) return false;
        (bool ok, bytes memory output) = PRECOMPILE.staticcall(abi.encodePacked(publicKey, message, signature));
        if (!ok || output.length != 32) return false;
        uint256 result;
        assembly { result := mload(add(output, 32)) }
        return result == 1;
    }
}
