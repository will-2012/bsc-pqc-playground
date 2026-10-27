// SPDX-License-Identifier: GPL-3.0
pragma solidity ^0.8.23;
import {BaseAccount} from "@account-abstraction/contracts/core/BaseAccount.sol";
import {IEntryPoint} from "@account-abstraction/contracts/interfaces/IEntryPoint.sol";
import {PackedUserOperation} from "@account-abstraction/contracts/interfaces/PackedUserOperation.sol";
interface IMLDSA65Verifier {function verify(bytes calldata pk,bytes32 message,bytes calldata signature) external view returns(bool);}
/// v5 immutable authorization policy; dynamic public key is stored once, without a setter.
contract MLDSA65Account is BaseAccount {
    IEntryPoint private immutable _entryPoint;
    IMLDSA65Verifier public immutable verifier;
    bytes public publicKey;
    constructor(IEntryPoint ep,bytes memory pk,IMLDSA65Verifier v){
        require(address(ep).code.length!=0 && address(v).code.length!=0 && pk.length==1952,"invalid configuration");
        _entryPoint=ep;verifier=v;publicKey=pk;
    }
    receive() external payable {}
    function entryPoint() public view override returns(IEntryPoint){return _entryPoint;}
    function _validateSignature(PackedUserOperation calldata op,bytes32 userOpHash)internal view override returns(uint256){
        return verifier.verify(publicKey,userOpHash,op.signature)?0:1;
    }
    function execute(address target,uint256 value,bytes calldata data)external{
        _requireFromEntryPoint();(bool ok,bytes memory result)=target.call{value:value}(data);
        if(!ok)assembly{revert(add(result,32),mload(result))}
    }
}
