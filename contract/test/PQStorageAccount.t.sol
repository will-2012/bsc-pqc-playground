// SPDX-License-Identifier: GPL-3.0
pragma solidity ^0.8.23;
import {PQStorageAccountFactory, PQStorageAccount, IEntryPoint, ISLHDSA128s} from "../src/v7/PQStorageAccountFactory.sol";
import {PQAccountFactory} from "../src/common/PQAccountFactory.sol";

interface StorageVm { function load(address,bytes32) external view returns(bytes32); }
contract StorageVerifierMock {
    function verify(bytes32,bytes32,bytes calldata) external pure returns(bool) { return true; }
}
contract PQStorageAccountTest {
    // This contract serves as the EntryPoint only for authorization tests.
    function testStorageKeySharesCodeAndFactoryIsIdempotent() public {
        StorageVerifierMock v = new StorageVerifierMock();
        PQStorageAccountFactory f = new PQStorageAccountFactory(IEntryPoint(address(this)), ISLHDSA128s(address(v)));
        bytes32 pk = bytes32(uint256(1));
        address predicted = f.getAddress(pk, 0);
        PQStorageAccount a = f.createAccount(pk, 0);
        PQStorageAccount b = f.createAccount(bytes32(uint256(2)), 0);
        require(address(a) == predicted && address(a) != address(b));
        require(address(f.createAccount(pk,0)) == address(a));
        require(StorageVm(address(uint160(uint256(keccak256("hevm cheat code"))))).load(address(a), bytes32(0)) == pk, "key must occupy slot zero");
        require(a.publicKey() == pk && b.publicKey() == bytes32(uint256(2)));
        require(address(a).codehash == address(b).codehash, "v7 code must be shared");
        a.execute(address(b), 0, "");
        // The original immutable variant still generates different runtime code.
        PQAccountFactory old = new PQAccountFactory(IEntryPoint(address(this)), ISLHDSA128s(address(v)));
        address oldA = address(old.createAccount(pk,0));
        address oldB = address(old.createAccount(bytes32(uint256(2)),0));
        require(oldA.codehash != oldB.codehash, "original layout changed");
    }
    function testRejectUntrustedExecutionAndKeyMutation() public {
        StorageVerifierMock other = new StorageVerifierMock();
        PQStorageAccount a = new PQStorageAccount(IEntryPoint(address(other)), bytes32(uint256(1)), ISLHDSA128s(address(other)));
        (bool ok,) = address(a).call(abi.encodeCall(a.execute,(address(this),0,bytes(""))));
        require(!ok,"unauthorized execution");
        (ok,) = address(a).call(abi.encodeWithSignature("setPublicKey(bytes32)",bytes32(uint256(2))));
        require(!ok && a.publicKey()==bytes32(uint256(1)),"key mutation");
    }
}
