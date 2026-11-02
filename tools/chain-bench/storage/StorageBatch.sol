// SPDX-License-Identifier: MIT
pragma solidity ^0.8.23;
interface ISlhFactory { function createAccount(bytes32,uint256) external returns(address); }
interface IMlFactory { function createAccount(bytes calldata,uint256) external returns(address); }
/// Experiment-only batching; no state, owner, proxy, or changes to production accounts.
contract StorageBatch {
    event Created(uint256 indexed start, uint256 count, bytes32 addressesHash);
    function eoa(uint256 start, address[] calldata accounts) external payable {
        require(msg.value == accounts.length, "funding");
        for(uint256 i; i<accounts.length; i++) {
            require(accounts[i].code.length == 0 && accounts[i].balance == 0, "not fresh");
            (bool ok,) = accounts[i].call{value:1}(""); require(ok);
        }
        emit Created(start, accounts.length, keccak256(abi.encode(accounts)));
    }
    function slh(address factory, uint256 start, bytes32[] calldata keys) external payable {
        require(msg.value == keys.length, "funding");
        address[] memory accounts = new address[](keys.length);
        for(uint256 i; i<keys.length; i++) {
            accounts[i] = ISlhFactory(factory).createAccount(keys[i],start+i);
            require(accounts[i].balance == 0, "not fresh");
            (bool ok,) = accounts[i].call{value:1}(""); require(ok);
        }
        emit Created(start, keys.length, keccak256(abi.encode(accounts)));
    }
    function ml(address factory, uint256 start, bytes[] calldata keys) external payable {
        require(msg.value == keys.length, "funding");
        address[] memory accounts = new address[](keys.length);
        for(uint256 i; i<keys.length; i++) {
            accounts[i] = IMlFactory(factory).createAccount(keys[i],start+i);
            require(accounts[i].balance == 0, "not fresh");
            (bool ok,) = accounts[i].call{value:1}(""); require(ok);
        }
        emit Created(start, keys.length, keccak256(abi.encode(accounts)));
    }
}
