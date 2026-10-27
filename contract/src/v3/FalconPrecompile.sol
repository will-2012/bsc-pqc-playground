// SPDX-License-Identifier: MIT
pragma solidity ^0.8.23;
contract FalconPrecompile {
    address public constant PRECOMPILE=address(0x102);
    function verify(bytes calldata pk,bytes32 message,bytes calldata signature)external view returns(bool){
        if(pk.length!=896 || signature.length<617 || signature.length>667)return false;
        (bool ok,bytes memory result)=PRECOMPILE.staticcall(abi.encodePacked(pk,message,signature));
        if(!ok || result.length!=32)return false;
        uint256 value;assembly{value:=mload(add(result,32))}return value==1;
    }
}
