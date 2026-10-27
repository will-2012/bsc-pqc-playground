// SPDX-License-Identifier: GPL-3.0
pragma solidity ^0.8.23;
import {FalconAccount,IFalconVerifier,IEntryPoint} from "./FalconAccount.sol";
contract FalconAccountFactory {
    IEntryPoint public immutable entryPoint;
    IFalconVerifier public immutable verifier;
    constructor(IEntryPoint ep,IFalconVerifier v){require(address(ep).code.length!=0 && address(v).code.length!=0,"invalid configuration");entryPoint=ep;verifier=v;}
    function getAddress(bytes calldata pk,uint256 salt)public view returns(address){
        require(pk.length==896,"invalid public key");
        bytes32 h=keccak256(abi.encodePacked(type(FalconAccount).creationCode,abi.encode(entryPoint,pk,verifier)));
        return address(uint160(uint256(keccak256(abi.encodePacked(bytes1(0xff),address(this),bytes32(salt),h)))));
    }
    function createAccount(bytes calldata pk,uint256 salt)external returns(FalconAccount account){
        account=FalconAccount(payable(getAddress(pk,salt)));
        if(address(account).code.length==0)account=new FalconAccount{salt:bytes32(salt)}(entryPoint,pk,verifier);
    }
}
