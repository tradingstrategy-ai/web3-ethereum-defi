// SPDX-License-Identifier: MIT

pragma solidity 0.6.12;

// Used in unit testing
contract RevertTest2 {

    function boom() external {
        revert("Big bada boom");
    }

}
