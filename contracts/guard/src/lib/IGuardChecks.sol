// Minimal interface for library callbacks into the guard contract.
//
// Libraries execute via DELEGATECALL, so address(this) points to the
// guard contract. Libraries call these view functions as regular CALLs
// via IGuardChecks(address(this)).isAllowed*() to perform cross-cutting
// permission checks without duplicating validation code.

pragma solidity ^0.8.0;

interface IGuardChecks {
    function isAllowedSender(address sender) external view returns (bool);
    function isAllowedAsset(address token) external view returns (bool);
    function isAllowedReceiver(address receiver) external view returns (bool);
}
