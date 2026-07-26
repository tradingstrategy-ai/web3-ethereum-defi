// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "forge-std/Test.sol";

import "../src/GuardV0.sol";
import "../src/SimpleVaultV0.sol";
import "../src/testing/Mock.sol";

contract GuardVaultProtocolTest is Test {
    address internal constant ASSET_MANAGER = address(0xA55E7);
    address internal constant OUTSIDER = address(0xBAD);
    uint256 internal constant AMOUNT = 100e6;

    GuardMockERC20 internal asset;
    SimpleVaultV0 internal simpleVault;
    GuardV0 internal guard;

    function setUp() public {
        asset = new GuardMockERC20("Mock USD", "mUSD", 6);
        simpleVault = new SimpleVaultV0(ASSET_MANAGER);
        simpleVault.initialiseOwnership(address(this));
        guard = simpleVault.guard();
    }

    function _call(address target, bytes memory callData) internal {
        vm.prank(ASSET_MANAGER);
        simpleVault.performCall(target, callData);
    }

    function _approve(address spender, uint256 amount) internal {
        _call(address(asset), abi.encodeCall(GuardMockERC20.approve, (spender, amount)));
    }

    function testERC7540AcceptsSelfControlledRequestAndClaim() public {
        MockERC7540Vault vault = new MockERC7540Vault(asset);
        guard.whitelistERC4626(address(vault), "ERC-7540 test vault");
        asset.mint(address(simpleVault), AMOUNT);
        _approve(address(vault), AMOUNT);

        _call(
            address(vault),
            abi.encodeCall(MockERC7540Vault.requestDeposit, (AMOUNT, address(simpleVault), address(simpleVault)))
        );
        assertEq(vault.lastReceiver(), address(simpleVault));
        assertEq(vault.lastOwner(), address(simpleVault));

        _call(
            address(vault),
            abi.encodeCall(MockERC7540Vault.deposit, (AMOUNT, address(simpleVault), address(simpleVault)))
        );
        assertEq(vault.balanceOf(address(simpleVault)), AMOUNT);
    }

    function testERC7540RejectsUnapprovedControllerAndOwner() public {
        MockERC7540Vault vault = new MockERC7540Vault(asset);
        guard.whitelistERC4626(address(vault), "ERC-7540 test vault");

        vm.expectRevert(bytes("Receiver not whitelisted"));
        _call(address(vault), abi.encodeCall(MockERC7540Vault.requestRedeem, (AMOUNT, OUTSIDER, address(simpleVault))));

        vm.expectRevert(bytes("Owner not whitelisted"));
        _call(address(vault), abi.encodeCall(MockERC7540Vault.requestRedeem, (AMOUNT, address(simpleVault), OUTSIDER)));

        vm.expectRevert(bytes("Owner not whitelisted"));
        _call(address(vault), abi.encodeCall(MockERC7540Vault.requestDeposit, (AMOUNT, address(simpleVault), OUTSIDER)));

        vm.expectRevert(bytes("Owner not whitelisted"));
        _call(address(vault), abi.encodeCall(MockERC7540Vault.deposit, (AMOUNT, address(simpleVault), OUTSIDER)));
    }

    function testCsigmaMockPreservesTheGuardedStandardERC4626Flow() public {
        MockCsigmaV2Pool vault = new MockCsigmaV2Pool(asset);
        guard.whitelistERC4626(address(vault), "cSigma test vault");
        asset.mint(address(simpleVault), AMOUNT);
        _approve(address(vault), AMOUNT);
        _call(address(vault), abi.encodeCall(MockERC4626Vault.deposit, (AMOUNT, address(simpleVault))));

        _call(
            address(vault),
            abi.encodeCall(MockERC4626Vault.redeem, (AMOUNT, address(simpleVault), address(simpleVault)))
        );
        assertEq(asset.balanceOf(address(simpleVault)), AMOUNT);

        vault.setWithdrawalPending(true);
        vm.expectRevert(MockCsigmaV2Pool.WithdrawalPending.selector);
        _call(address(vault), abi.encodeCall(MockERC4626Vault.redeem, (1, address(simpleVault), address(simpleVault))));
    }

    function testEmberAndGainsUseTheirNarrowWhitelists() public {
        MockEmberVault ember = new MockEmberVault(asset);
        guard.whitelistERC4626(address(ember), "Ember test vault");
        asset.mint(address(simpleVault), AMOUNT * 2);
        _approve(address(ember), AMOUNT);
        _call(address(ember), abi.encodeCall(MockERC4626Vault.deposit, (AMOUNT, address(simpleVault))));
        _call(address(ember), abi.encodeCall(MockEmberVault.redeemShares, (AMOUNT, address(simpleVault))));

        vm.expectRevert(bytes("Receiver not whitelisted"));
        _call(address(ember), abi.encodeCall(MockEmberVault.redeemShares, (1, OUTSIDER)));

        MockGainsV1Vault gains = new MockGainsV1Vault(asset);
        guard.whitelistERC4626(address(gains), "Gains test vault");
        _approve(address(gains), AMOUNT);
        _call(address(gains), abi.encodeCall(MockERC4626Vault.deposit, (AMOUNT, address(simpleVault))));
        _call(address(gains), abi.encodeCall(MockGainsV1Vault.makeWithdrawRequest, (AMOUNT, address(simpleVault))));
        assertEq(gains.pendingWithdrawalShares(address(simpleVault)), AMOUNT);
    }

    function testOstiumAsyncSelectorsReachTheConfiguredTarget() public {
        MockOstiumV15Vault vault = new MockOstiumV15Vault(asset);
        guard.whitelistERC4626(address(vault), "Ostium test vault");
        asset.mint(address(simpleVault), AMOUNT);
        _approve(address(vault), AMOUNT);

        _call(address(vault), abi.encodeCall(MockOstiumV15Vault.requestDeposit, (AMOUNT)));
        _call(address(vault), abi.encodeCall(MockOstiumV15Vault.claimDeposit, (uint32(1))));
        assertEq(vault.balanceOf(address(simpleVault)), AMOUNT);
        _call(address(vault), abi.encodeCall(MockOstiumV15Vault.requestWithdraw, (AMOUNT)));
        _call(address(vault), abi.encodeCall(MockOstiumV15Vault.claimWithdraw, (uint32(2))));
        assertEq(asset.balanceOf(address(simpleVault)), AMOUNT);
    }

    function testNaraCooldownAndUnstakeValidateThePayoutReceiver() public {
        MockNaraVault vault = new MockNaraVault(asset);
        guard.whitelistERC4626(address(vault), "Nara test vault");
        asset.mint(address(simpleVault), AMOUNT);
        _approve(address(vault), AMOUNT);
        _call(address(vault), abi.encodeCall(MockERC4626Vault.deposit, (AMOUNT, address(simpleVault))));
        _call(address(vault), abi.encodeCall(MockNaraVault.cooldownShares, (AMOUNT)));

        vm.expectRevert(bytes("Receiver not whitelisted"));
        _call(address(vault), abi.encodeCall(MockNaraVault.unstake, (OUTSIDER)));

        _call(address(vault), abi.encodeCall(MockNaraVault.unstake, (address(simpleVault))));
        assertEq(asset.balanceOf(address(simpleVault)), AMOUNT);
    }

    function testUpshiftDepositChecksAssetAndReceiver() public {
        MockUpshiftVault vault = new MockUpshiftVault();
        guard.whitelistUpshift(address(vault), address(asset), "Upshift test vault");
        asset.mint(address(simpleVault), AMOUNT);
        _approve(address(vault), AMOUNT);

        _call(address(vault), abi.encodeCall(MockUpshiftVault.deposit, (address(asset), AMOUNT, address(simpleVault))));
        assertEq(vault.lastAsset(), address(asset));
        assertEq(vault.lastReceiver(), address(simpleVault));

        vm.expectRevert(bytes("Token not allowed"));
        _call(address(vault), abi.encodeCall(MockUpshiftVault.deposit, (OUTSIDER, 1, address(simpleVault))));

        vm.expectRevert(bytes("Receiver not whitelisted"));
        _call(address(vault), abi.encodeCall(MockUpshiftVault.deposit, (address(asset), 1, OUTSIDER)));
    }
}
