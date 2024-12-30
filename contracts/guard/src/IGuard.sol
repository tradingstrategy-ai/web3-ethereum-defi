pragma solidity ^0.8.0;

/**
 * Trade execution guard.
 *
 * - Check that we cannot do trades for which we do not have permission for
 */
interface IGuard {

    /**
     * Revert if the smart contract call is not allowed
     */
    function validateCall(address sender, address target, bytes memory callDataWithSelector) external view;
}