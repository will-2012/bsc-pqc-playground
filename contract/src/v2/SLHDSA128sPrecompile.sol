// SPDX-License-Identifier: MIT
pragma solidity ^0.8.23;

/// v2 verifier. Same public ABI as the v1 Solidity verifier; native execution.
/// Experimental address, available only in the local v2 BSC client.
contract SLHDSA128sPrecompile {
    address public constant PRECOMPILE = address(0x101);

    function verify(bytes32 publicKey, bytes32 message, bytes calldata signature) external view returns (bool) {
        if (signature.length != 7856) return false;
        (bool ok, bytes memory output) = PRECOMPILE.staticcall(abi.encodePacked(publicKey, message, signature));
        if (!ok || output.length != 32) return false;
        uint256 result;
        assembly { result := mload(add(output, 32)) }
        return result == 1;
    }
}
