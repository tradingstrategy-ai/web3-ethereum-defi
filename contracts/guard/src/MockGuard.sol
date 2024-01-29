/**
 * A unit test guard implementation without checks.
 *
 */

pragma solidity ^0.8.0;

import "@openzeppelin/access/Ownable.sol";
import "./lib/Path.sol";
import "./IGuard.sol";

contract MockGuard is IGuard {

    function validateCall(
        address sender,
        address target,
        bytes calldata callDataWithSelector
    ) external view {
        // Don't revert
    }

}