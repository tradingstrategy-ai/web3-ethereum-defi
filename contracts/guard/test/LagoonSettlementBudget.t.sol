// SPDX-License-Identifier: MIT
pragma solidity ^0.8.26;

import "forge-std/Test.sol";

import "../src/lib/LagoonLib.sol";
import "../src/testing/Mock.sol";

contract MockSettlementSilo {
    function approve(GuardMockERC20 asset, address spender) external {
        asset.approve(spender, type(uint256).max);
    }
}

contract MockLagoonSettlementVault {
    GuardMockERC20 internal immutable _asset;
    address internal immutable _silo;
    address internal immutable _safe;

    constructor(GuardMockERC20 asset_, address silo_, address safe_) {
        _asset = asset_;
        _silo = silo_;
        _safe = safe_;
    }

    function asset() external view returns (address) {
        return address(_asset);
    }

    function settle(uint256 depositAssets, uint256 redeemAssets) external {
        if (depositAssets != 0) require(_asset.transferFrom(_silo, _safe, depositAssets));
        if (redeemAssets != 0) require(_asset.transferFrom(_safe, address(this), redeemAssets));
    }
}

/// Runs both sides of the actual linked LagoonLib validation lifecycle.
contract LagoonSettlementHarness {
    function configure(address vault, address asset, address silo, uint256 cap, uint256 window) external {
        LagoonLib.whitelistVaultWithSettlementLimitAndCooldown(vault, asset, silo, cap, window, "test budget");
    }

    function approveVault(GuardMockERC20 asset, address vault) external {
        asset.approve(vault, type(uint256).max);
    }

    function executeSettlement(MockLagoonSettlementVault vault, uint256 depositAssets, uint256 redeemAssets) external {
        bytes memory context = LagoonLib.capturePostCallContext(address(vault));
        vault.settle(depositAssets, redeemAssets);
        LagoonLib.validatePostCall(context);
    }

    function settlementState(address vault) external view returns (uint256, uint256, uint256) {
        return LagoonLib.getSettlementCooldownConfig(vault);
    }
}

contract LagoonSettlementBudgetTest is Test {
    uint256 internal constant USDC = 1e6;
    uint256 internal constant CAP = 5_000 * USDC;
    uint256 internal constant WINDOW = 1 days;

    GuardMockERC20 internal asset;
    MockSettlementSilo internal silo;
    LagoonSettlementHarness internal harness;
    MockLagoonSettlementVault internal vault;

    function setUp() public {
        asset = new GuardMockERC20("Mock USD Coin", "USDC", 6);
        silo = new MockSettlementSilo();
        harness = new LagoonSettlementHarness();
        vault = new MockLagoonSettlementVault(asset, address(silo), address(harness));
        silo.approve(asset, address(vault));
        harness.approveVault(asset, address(vault));
        harness.configure(address(vault), address(asset), address(silo), CAP, WINDOW);
        asset.mint(address(silo), 10_000 * USDC);
        asset.mint(address(harness), 10_000 * USDC);
        vm.warp(1_000_000);
    }

    function _state() internal view returns (uint256 window, uint256 used, uint256 end) {
        return harness.settlementState(address(vault));
    }

    function testMultipleSettlementsAccumulateBelowCap() public {
        harness.executeSettlement(vault, 1 * USDC, 0);
        (uint256 window, uint256 used, uint256 end) = _state();
        assertEq(window, WINDOW);
        assertEq(used, 1 * USDC);
        assertEq(end, block.timestamp + WINDOW);

        harness.executeSettlement(vault, 20 * USDC, 0);
        (window, used, end) = _state();
        assertEq(window, WINDOW);
        assertEq(used, 21 * USDC);
        assertEq(end, 1_000_000 + WINDOW);
    }

    function testExactCapSucceedsAndNextAmountRevertsAtomically() public {
        harness.executeSettlement(vault, 3_000 * USDC, 0);
        harness.executeSettlement(vault, 2_000 * USDC, 0);
        (uint256 window, uint256 used, uint256 end) = _state();
        assertEq(window, WINDOW);
        assertEq(used, CAP);

        uint256 siloBefore = asset.balanceOf(address(silo));
        uint256 safeBefore = asset.balanceOf(address(harness));
        vm.expectRevert(abi.encodeWithSelector(LagoonLib.LagoonSettlementWindowLimitExceeded.selector, CAP, 1, CAP));
        harness.executeSettlement(vault, 1, 0);
        assertEq(asset.balanceOf(address(silo)), siloBefore);
        assertEq(asset.balanceOf(address(harness)), safeBefore);
        (window, used, end) = _state();
        assertEq(window, WINDOW);
        assertEq(used, CAP);
        assertEq(end, 1_000_000 + WINDOW);
    }

    function testDepositAndRedemptionUseGrossFlow() public {
        uint256 siloBefore = asset.balanceOf(address(silo));
        uint256 safeBefore = asset.balanceOf(address(harness));
        uint256 vaultBefore = asset.balanceOf(address(vault));
        vm.expectRevert(
            abi.encodeWithSelector(LagoonLib.LagoonSettlementWindowLimitExceeded.selector, 0, 6_000 * USDC, CAP)
        );
        harness.executeSettlement(vault, 4_000 * USDC, 2_000 * USDC);
        assertEq(asset.balanceOf(address(silo)), siloBefore);
        assertEq(asset.balanceOf(address(harness)), safeBefore);
        assertEq(asset.balanceOf(address(vault)), vaultBefore);
        (, uint256 used,) = _state();
        assertEq(used, 0);
    }

    function testExpiredWindowStartsFreshBudget() public {
        harness.executeSettlement(vault, CAP, 0);
        (, uint256 used, uint256 firstEnd) = _state();
        assertEq(used, CAP);
        vm.warp(firstEnd);
        harness.executeSettlement(vault, 20 * USDC, 0);
        uint256 secondEnd;
        (, used, secondEnd) = _state();
        assertEq(used, 20 * USDC);
        assertEq(secondEnd, firstEnd + WINDOW);
    }

    function testEmptySettlementDoesNotCreateExtendOrResetWindow() public {
        harness.executeSettlement(vault, 0, 0);
        (uint256 window, uint256 used, uint256 end) = _state();
        assertEq(window, WINDOW);
        assertEq(used, 0);
        assertEq(end, 0);

        harness.executeSettlement(vault, 1 * USDC, 0);
        (, used, end) = _state();
        assertEq(used, 1 * USDC);
        harness.executeSettlement(vault, 0, 0);
        (, uint256 usedAfterEmpty, uint256 endAfterEmpty) = _state();
        assertEq(usedAfterEmpty, used);
        assertEq(endAfterEmpty, end);

        vm.warp(end);
        harness.executeSettlement(vault, 0, 0);
        (window, usedAfterEmpty, endAfterEmpty) = _state();
        assertEq(window, WINDOW);
        assertEq(usedAfterEmpty, 0);
        assertEq(endAfterEmpty, 0);
    }

    function testMaximumWindowDoesNotOverflowSettlementAccounting() public {
        harness.configure(address(vault), address(asset), address(silo), CAP, type(uint256).max);
        vm.warp(type(uint256).max - 1);

        harness.executeSettlement(vault, 1 * USDC, 0);
        (uint256 window, uint256 used, uint256 end) = _state();
        assertEq(window, type(uint256).max);
        assertEq(used, 1 * USDC);
        assertEq(end, type(uint256).max);

        harness.executeSettlement(vault, 1 * USDC, 0);
        (, used, end) = _state();
        assertEq(used, 2 * USDC);
        assertEq(end, type(uint256).max);
    }

    function testDirectSettlementDoesNotChangeAssetManagerBudget() public {
        vault.settle(20 * USDC, 0);
        (uint256 window, uint256 used, uint256 end) = _state();
        assertEq(window, WINDOW);
        assertEq(used, 0);
        assertEq(end, 0);
    }
}
