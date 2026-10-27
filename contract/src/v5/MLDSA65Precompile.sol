// SPDX-License-Identifier: MIT
pragma solidity ^0.8.23;
contract MLDSA65Precompile {
    address public constant PRECOMPILE=address(0x104);
    function verify(bytes calldata pk,bytes32 message,bytes calldata signature)external view returns(bool){
        if(pk.length!=1952 || signature.length!=3309)return false;
        (bool ok,bytes memory result)=PRECOMPILE.staticcall(abi.encodePacked(pk,message,signature));
        if(!ok || result.length!=32)return false;
        uint256 value;assembly{value:=mload(add(result,32))}return value==1;
    }
}
