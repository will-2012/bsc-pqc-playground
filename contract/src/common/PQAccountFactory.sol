// SPDX-License-Identifier: GPL-3.0
pragma solidity ^0.8.23;

import {PQAccount, ISLHDSA128s, IEntryPoint} from "./PQAccount.sol";

/// Public, deterministic deployment; identity is bound to public key and salt.
contract PQAccountFactory {
    IEntryPoint public immutable entryPoint;
    ISLHDSA128s public immutable verifier;

    constructor(IEntryPoint ep, ISLHDSA128s v) {
        require(address(ep).code.length != 0 && address(v).code.length != 0, "invalid configuration");
        entryPoint = ep;
        verifier = v;
    }

    function getAddress(bytes32 pk, uint256 salt) public view returns (address) {
        bytes32 codeHash = keccak256(abi.encodePacked(type(PQAccount).creationCode, abi.encode(entryPoint, pk, verifier)));
        return address(uint160(uint256(keccak256(abi.encodePacked(bytes1(0xff), address(this), bytes32(salt), codeHash)))));
    }

    function createAccount(bytes32 pk, uint256 salt) external returns (PQAccount account) {
        account = PQAccount(payable(getAddress(pk, salt)));
        if (address(account).code.length == 0) account = new PQAccount{salt: bytes32(salt)}(entryPoint, pk, verifier);
    }
}
