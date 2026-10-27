// SPDX-License-Identifier: MIT
pragma solidity ^0.8.23;

contract Counter {
    uint256 public count;
    address public lastCaller;

    function increment() external {
        count += 1;
        lastCaller = msg.sender;
    }
}
