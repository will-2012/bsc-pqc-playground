// SPDX-License-Identifier: MIT
pragma solidity ^0.8.23;
contract MLDSAPrecompile {
    address public constant PRECOMPILE=address(0x103);
    function verify(bytes calldata pk,bytes32 message,bytes calldata signature)external view returns(bool){
        if(pk.length!=1312 || signature.length!=2420)return false;
        (bool ok,bytes memory result)=PRECOMPILE.staticcall(abi.encodePacked(pk,message,signature));
        if(!ok || result.length!=32)return false;
        uint256 value;assembly{value:=mload(add(result,32))}return value==1;
    }
}
