// SPDX-License-Identifier: GPL-3.0
pragma solidity ^0.8.23;

import {BaseAccount} from "@account-abstraction/contracts/core/BaseAccount.sol";
import {IEntryPoint} from "@account-abstraction/contracts/interfaces/IEntryPoint.sol";
import {PackedUserOperation} from "@account-abstraction/contracts/interfaces/PackedUserOperation.sol";

interface ISLHDSA128s {
    function verify(bytes32 publicKey, bytes32 message, bytes calldata signature) external view returns (bool);
}

/// Experimental immutable PQ account. Only EntryPoint may execute calls.
/// No ECDSA owner, recovery key, proxy admin or key rotation.
contract PQAccount is BaseAccount {
    IEntryPoint private immutable _entryPoint;
    bytes32 public immutable publicKey;
    ISLHDSA128s public immutable verifier;

    constructor(IEntryPoint ep, bytes32 pk, ISLHDSA128s v) {
        require(address(ep).code.length != 0 && address(v).code.length != 0, "invalid configuration");
        _entryPoint = ep;
        publicKey = pk;
        verifier = v;
    }

    receive() external payable {}

    function entryPoint() public view override returns (IEntryPoint) { return _entryPoint; }

    function _validateSignature(PackedUserOperation calldata op, bytes32 userOpHash)
        internal view override returns (uint256)
    {
        // userOpHash binds sender, nonce, calldata, fees, chainId and EntryPoint.
        return verifier.verify(publicKey, userOpHash, op.signature) ? 0 : 1;
    }

    function execute(address target, uint256 value, bytes calldata data) external {
        _requireFromEntryPoint();
        (bool ok, bytes memory result) = target.call{value: value}(data);
        if (!ok) assembly { revert(add(result, 32), mload(result)) }
    }
}
