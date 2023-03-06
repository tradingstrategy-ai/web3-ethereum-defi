// SPDX-License-Identifier: MIT

pragma solidity 0.6.12;

import "./RevertTest2.sol";

// Used in unit testing
contract RevertTest {

    function revert1() external {
        revert("foobar");
    }

    function revert2(address second) external {
        RevertTest2 reverter = RevertTest2(second);
        reverter.boom();
    }
}
