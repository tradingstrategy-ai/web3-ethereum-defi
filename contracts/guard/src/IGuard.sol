pragma solidity ^0.8.0;

interface IGuard {
    function validateCall(address sender, address target, bytes memory callDataWithSelector) external;
}