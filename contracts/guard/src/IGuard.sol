pragma solidity ^0.8.0;

interface IGuard {

    /**
     * Revert if the smart contract call is not allowed
     */
    function validateCall(address sender, address target, bytes memory callDataWithSelector) external view;
}