// SPDX-License-Identifier: GPL-3.0
pragma solidity ^0.8.23;
import {MLDSA65Account,IMLDSA65Verifier,IEntryPoint} from "./MLDSA65Account.sol";
contract MLDSA65AccountFactory {
    IEntryPoint public immutable entryPoint;
    IMLDSA65Verifier public immutable verifier;
    constructor(IEntryPoint ep,IMLDSA65Verifier v){require(address(ep).code.length!=0 && address(v).code.length!=0,"invalid configuration");entryPoint=ep;verifier=v;}
    function getAddress(bytes calldata pk,uint256 salt)public view returns(address){
        require(pk.length==1952,"invalid public key");
        bytes32 h=keccak256(abi.encodePacked(type(MLDSA65Account).creationCode,abi.encode(entryPoint,pk,verifier)));
        return address(uint160(uint256(keccak256(abi.encodePacked(bytes1(0xff),address(this),bytes32(salt),h)))));
    }
    function createAccount(bytes calldata pk,uint256 salt)external returns(MLDSA65Account account){
        account=MLDSA65Account(payable(getAddress(pk,salt)));
        if(address(account).code.length==0)account=new MLDSA65Account{salt:bytes32(salt)}(entryPoint,pk,verifier);
    }
}
