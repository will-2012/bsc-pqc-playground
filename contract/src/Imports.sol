// SPDX-License-Identifier: MIT
pragma solidity ^0.8.23;

// Compile the upstream EntryPoint and sample account factory without changing
// their validation, nonce or fee logic. Local deployment supplies their address.
import "@account-abstraction/contracts/core/EntryPoint.sol";
import "@account-abstraction/contracts/samples/SimpleAccountFactory.sol";
