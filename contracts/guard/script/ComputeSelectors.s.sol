// SPDX-License-Identifier: MIT
//
// Forge script to compute function selectors for GuardV0Base constants.
//
// Run with:
//   cd contracts/guard && forge script script/ComputeSelectors.s.sol -vvvv
//
// This outputs the pre-computed bytes4 selectors that should be used
// as constants in GuardV0Base.sol to avoid runtime keccak256 computation.
//
pragma solidity ^0.8.0;

import "forge-std/Script.sol";

contract ComputeSelectors is Script {
    function run() public view {
        console.log("=== Function Selectors for GuardV0Base ===");
        console.log("");

        // ERC-20
        console.log("// ERC-20");
        logSelector("transfer(address,uint256)");
        logSelector("approve(address,uint256)");
        logSelector("approveDelegation(address,uint256)");
        console.log("");

        // Uniswap V2
        console.log("// Uniswap V2");
        logSelector("swapExactTokensForTokens(uint256,uint256,address[],address,uint256)");
        logSelector("swapExactTokensForTokensSupportingFeeOnTransferTokens(uint256,uint256,address[],address,uint256)");
        console.log("");

        // Uniswap V3
        console.log("// Uniswap V3");
        logSelector("exactInput((bytes,address,uint256,uint256,uint256))");
        logSelector("exactOutput((bytes,address,uint256,uint256,uint256))");
        console.log("// SwapRouter02 exactInput: 0xb858183f (hardcoded)");
        console.log("");

        // Aave V3
        console.log("// Aave V3");
        logSelector("supply(address,uint256,address,uint16)");
        logSelector("withdraw(address,uint256,address)");
        console.log("");

        // Lagoon
        console.log("// Lagoon");
        logSelector("settleDeposit()");
        logSelector("settleRedeem()");
        logSelector("settleDeposit(uint256)");
        logSelector("settleRedeem(uint256)");
        console.log("");

        // ERC-4626
        console.log("// ERC-4626");
        logSelector("deposit(uint256,address)");
        logSelector("withdraw(uint256,address,address)");
        logSelector("redeem(uint256,address,address)");
        console.log("");

        // ERC-4626 Umami non-standard
        console.log("// ERC-4626 Umami non-standard");
        logSelector("deposit(uint256,uint256,address)");
        logSelector("redeem(uint256,uint256,address,address)");
        console.log("");

        // ERC-7540
        console.log("// ERC-7540");
        logSelector("deposit(uint256,address,address)");
        logSelector("requestRedeem(uint256,address,address)");
        logSelector("requestWithdraw(uint256,address,address)");
        logSelector("requestDeposit(uint256,address,address)");
        console.log("");

        // Gains/Ostium
        console.log("// Gains/Ostium");
        logSelector("makeWithdrawRequest(uint256,address)");
        console.log("");

        // Orderly
        console.log("// Orderly");
        logSelector("delegateSigner((bytes32,address))");
        logSelector("deposit((bytes32,bytes32,bytes32,uint128))");
        logSelector("withdraw((bytes32,bytes32,bytes32,uint128,uint128,address,address,uint64))");
    }

    function logSelector(string memory sig) internal view {
        bytes4 sel = bytes4(keccak256(bytes(sig)));
        console.log(sig);
        console.logBytes4(sel);
    }
}
