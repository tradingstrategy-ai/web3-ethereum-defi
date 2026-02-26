// Common interface for all guard helper libraries.
//
// Every external Forge library linked to the guard contract must implement
// this interface. The isDeployed() check catches missing library links at
// runtime: a DELEGATECALL to the zero address silently returns zero bytes,
// so requiring a true result produces a human-readable error instead of
// silent misbehaviour.

pragma solidity ^0.8.0;

interface IGuardLib {
    /// Returns true when the library is properly linked.
    function isDeployed() external pure returns (bool);
}
