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

    bytes32 internal constant WITHDRAW_EVENT = keccak256("Withdraw(address,address,address,uint256,uint256)");
    bytes32 internal constant TRANSFER_EVENT = keccak256("Transfer(address,address,uint256)");
    bytes32 internal constant DEPOSIT_EVENT = keccak256("Deposit(address,address,uint256,uint256)");
    bytes32 internal constant ERC7540_DEPOSIT_REQUEST_EVENT =
        keccak256("DepositRequest(address,address,uint256,address,uint256)");
    bytes32 internal constant ERC7540_DEPOSIT_CLAIMABLE_EVENT =
        keccak256("DepositClaimable(uint256,address,uint256,uint256)");
    bytes32 internal constant ERC7540_REDEEM_REQUEST_EVENT =
        keccak256("RedeemRequest(address,address,uint256,address,uint256)");
    bytes32 internal constant PLUTUS_REDEEM_REQUESTED_EVENT =
        keccak256("RedeemRequested(address,address,uint256,address,uint256)");
    bytes32 internal constant PLUTUS_REDEEM_FULFILLED_EVENT =
        keccak256("RedeemFulfilled(uint256,address,uint256,uint256)");
    bytes32 internal constant ERC7540_REDEEM_CLAIMABLE_EVENT =
        keccak256("RedeemClaimable(address,uint256,uint256,uint256)");
    bytes32 internal constant EMBER_REDEEM_REQUESTED_EVENT =
        keccak256("RequestRedeemed(address,address,address,uint256,uint256,uint256,uint256,uint256)");
    bytes32 internal constant EMBER_REQUEST_PROCESSED_EVENT = keccak256(
        "RequestProcessed(address,address,address,uint256,uint256,uint256,uint256,bool,bool,uint256,uint256,uint256,uint256)"
    );
    bytes32 internal constant GAINS_WITHDRAW_REQUESTED_EVENT =
        keccak256("WithdrawRequested(address,address,uint256,uint256,uint256)");
    bytes32 internal constant GAINS_EPOCH_ADVANCED_EVENT = keccak256("EpochAdvanced(uint16,uint16)");
    bytes32 internal constant OSTIUM_WITHDRAW_CLAIMED_EVENT = keccak256("WithdrawClaimedV2(address,uint32,uint256)");
    bytes32 internal constant OSTIUM_SETTLEMENT_EVENT =
        keccak256("AsyncDepositWithdrawExecuted(uint32,int256,uint256,uint256,uint256)");
    bytes32 internal constant UPSHIFT_WITHDRAWAL_REQUESTED_EVENT =
        keccak256("WithdrawalRequested(uint256,address,address)");
    bytes32 internal constant UPSHIFT_WITHDRAWAL_PROCESSED_EVENT = keccak256("WithdrawalProcessed(uint256,address)");

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

    function _findEvent(Vm.Log[] memory logs, address emitter, bytes32 eventSignature)
        internal
        pure
        returns (Vm.Log memory found)
    {
        for (uint256 i; i < logs.length; ++i) {
            Vm.Log memory log = logs[i];
            if (log.emitter == emitter && log.topics.length != 0 && log.topics[0] == eventSignature) {
                return log;
            }
        }
        revert("Expected event not emitted");
    }

    function _assertWithdrawEvent(Vm.Log[] memory logs, address vault, uint256 expectedAssets, uint256 expectedShares)
        internal
    {
        Vm.Log memory withdrawLog = _findEvent(logs, vault, WITHDRAW_EVENT);
        (uint256 assets, uint256 shares) = abi.decode(withdrawLog.data, (uint256, uint256));
        assertEq(assets, expectedAssets, "Withdraw event assets");
        assertEq(shares, expectedShares, "Withdraw event shares");
    }

    function _assertDepositEvent(Vm.Log[] memory logs, address vault, uint256 expectedAssets, uint256 expectedShares)
        internal
    {
        Vm.Log memory depositLog = _findEvent(logs, vault, DEPOSIT_EVENT);
        (uint256 assets, uint256 shares) = abi.decode(depositLog.data, (uint256, uint256));
        assertEq(assets, expectedAssets, "Deposit event assets");
        assertEq(shares, expectedShares, "Deposit event shares");
    }

    function _assertAssetTransfer(
        Vm.Log[] memory logs,
        address expectedSender,
        address expectedReceiver,
        uint256 expectedAmount
    ) internal {
        Vm.Log memory transferLog = _findEvent(logs, address(asset), TRANSFER_EVENT);
        assertEq(address(uint160(uint256(transferLog.topics[1]))), expectedSender, "Asset transfer sender");
        assertEq(address(uint160(uint256(transferLog.topics[2]))), expectedReceiver, "Asset transfer receiver");
        assertEq(abi.decode(transferLog.data, (uint256)), expectedAmount, "Asset transfer amount");
    }

    function testERC7540AcceptsSelfControlledRequestAndClaim() public {
        MockERC7540Vault vault = new MockERC7540Vault(asset);
        guard.whitelistERC4626(address(vault), "ERC-7540 test vault");
        asset.mint(address(simpleVault), AMOUNT);
        _approve(address(vault), AMOUNT);

        vm.recordLogs();
        _call(
            address(vault),
            abi.encodeCall(MockERC7540Vault.requestDeposit, (AMOUNT, address(simpleVault), address(simpleVault)))
        );
        Vm.Log memory depositRequestLog =
            _findEvent(vm.getRecordedLogs(), address(vault), ERC7540_DEPOSIT_REQUEST_EVENT);
        assertEq(depositRequestLog.topics[3], bytes32(uint256(1)), "ERC-7540 deposit request id");
        (address depositSender, uint256 requestedAssets) = abi.decode(depositRequestLog.data, (address, uint256));
        assertEq(depositSender, address(simpleVault), "ERC-7540 deposit request sender");
        assertEq(requestedAssets, AMOUNT, "ERC-7540 deposit request assets");
        assertEq(vault.lastReceiver(), address(simpleVault));
        assertEq(vault.lastOwner(), address(simpleVault));
        assertEq(vault.pendingDepositRequest(1, address(simpleVault)), AMOUNT, "ERC-7540 deposit remains pending");

        vm.recordLogs();
        vault.fulfillDepositRequest(1);
        Vm.Log memory depositClaimableLog =
            _findEvent(vm.getRecordedLogs(), address(vault), ERC7540_DEPOSIT_CLAIMABLE_EVENT);
        (uint256 claimableDepositAssets, uint256 claimableDepositShares) =
            abi.decode(depositClaimableLog.data, (uint256, uint256));
        assertEq(claimableDepositAssets, AMOUNT, "ERC-7540 deposit settlement assets");
        assertEq(claimableDepositShares, AMOUNT, "ERC-7540 deposit settlement shares");
        assertEq(vault.claimableDepositRequest(1, address(simpleVault)), AMOUNT, "ERC-7540 deposit is claimable");

        vm.recordLogs();
        _call(
            address(vault),
            abi.encodeCall(MockERC7540Vault.deposit, (AMOUNT, address(simpleVault), address(simpleVault)))
        );
        _assertDepositEvent(vm.getRecordedLogs(), address(vault), AMOUNT, AMOUNT);
        assertEq(vault.balanceOf(address(simpleVault)), AMOUNT);
        assertEq(vault.claimableDepositRequest(1, address(simpleVault)), 0, "ERC-7540 deposit was consumed");

        vm.recordLogs();
        _call(
            address(vault),
            abi.encodeCall(MockERC7540Vault.requestRedeem, (AMOUNT, address(simpleVault), address(simpleVault)))
        );
        Vm.Log memory redeemRequestLog = _findEvent(vm.getRecordedLogs(), address(vault), ERC7540_REDEEM_REQUEST_EVENT);
        assertEq(redeemRequestLog.topics[3], bytes32(uint256(2)), "ERC-7540 redeem request id");
        (address redeemSender, uint256 requestedShares) = abi.decode(redeemRequestLog.data, (address, uint256));
        assertEq(redeemSender, address(simpleVault), "ERC-7540 redeem request sender");
        assertEq(requestedShares, AMOUNT, "ERC-7540 redeem request shares");
        assertEq(vault.claimableRedeemShares(address(simpleVault)), 0, "ERC-7540 request remains pending");
        assertEq(vault.pendingRedeemRequest(2, address(simpleVault)), AMOUNT, "ERC-7540 redeem remains pending");

        vm.recordLogs();
        vault.fulfillRedeemRequest(2);
        Vm.Log memory claimableLog = _findEvent(vm.getRecordedLogs(), address(vault), ERC7540_REDEEM_CLAIMABLE_EVENT);
        assertEq(
            address(uint160(uint256(claimableLog.topics[1]))), address(simpleVault), "ERC-7540 settlement controller"
        );
        assertEq(claimableLog.topics[2], bytes32(uint256(2)), "ERC-7540 settlement request id");
        (uint256 claimableAssets, uint256 claimableShares) = abi.decode(claimableLog.data, (uint256, uint256));
        assertEq(claimableAssets, AMOUNT, "ERC-7540 settlement assets");
        assertEq(claimableShares, AMOUNT, "ERC-7540 settlement shares");
        assertEq(vault.claimableRedeemShares(address(simpleVault)), AMOUNT, "ERC-7540 claimable shares");
        assertEq(vault.claimableRedeemRequest(2, address(simpleVault)), AMOUNT, "ERC-7540 redeem is claimable");

        vm.recordLogs();
        _call(
            address(vault),
            abi.encodeCall(MockERC7540Vault.redeem, (AMOUNT, address(simpleVault), address(simpleVault)))
        );
        Vm.Log[] memory redeemClaimLogs = vm.getRecordedLogs();
        _assertWithdrawEvent(redeemClaimLogs, address(vault), AMOUNT, AMOUNT);
        _assertAssetTransfer(redeemClaimLogs, address(vault), address(simpleVault), AMOUNT);
        assertEq(asset.balanceOf(address(simpleVault)), AMOUNT, "ERC-7540 redeemed assets");
        assertEq(vault.claimableRedeemRequest(2, address(simpleVault)), 0, "ERC-7540 redeem was consumed");
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

        vm.expectRevert(bytes("Receiver not whitelisted"));
        _call(address(vault), abi.encodeCall(MockERC7540Vault.requestDeposit, (AMOUNT, OUTSIDER, address(simpleVault))));

        vm.expectRevert(bytes("Receiver not whitelisted"));
        _call(address(vault), abi.encodeCall(MockERC7540Vault.deposit, (AMOUNT, OUTSIDER, address(simpleVault))));

        vm.expectRevert(bytes("Owner not whitelisted"));
        _call(address(vault), abi.encodeCall(MockERC7540Vault.deposit, (AMOUNT, address(simpleVault), OUTSIDER)));

        vm.expectRevert(bytes("Receiver not whitelisted"));
        _call(address(vault), abi.encodeCall(MockERC7540Vault.redeem, (AMOUNT, OUTSIDER, address(simpleVault))));

        vm.expectRevert(bytes("Owner not whitelisted"));
        _call(address(vault), abi.encodeCall(MockERC7540Vault.redeem, (AMOUNT, address(simpleVault), OUTSIDER)));
    }

    function testCCTPRejectsExclusiveDestinationCaller() public {
        address tokenMessenger = address(0xCC71);
        uint32 destinationDomain = 6;

        guard.whitelistCCTP(tokenMessenger, "CCTP test messenger");
        guard.whitelistCCTPDestination(destinationDomain, "CCTP test destination");
        guard.whitelistToken(address(asset), "CCTP test asset");
        guard.allowReceiver(address(simpleVault), "CCTP test mint recipient");

        // The permissionless delivery form remains valid: the allowlisted Safe
        // is still the destination mint recipient.
        _call(
            tokenMessenger,
            abi.encodeWithSelector(
                bytes4(0x8e0250ee),
                AMOUNT,
                destinationDomain,
                bytes32(uint256(uint160(address(simpleVault)))),
                address(asset),
                bytes32(0),
                0,
                uint32(2000)
            )
        );

        // A non-zero destinationCaller would give OUTSIDER exclusive permission
        // to finalise receiveMessage() after the Safe has burned its USDC.
        bytes memory exclusiveCallerCallData = abi.encodeWithSelector(
            bytes4(0x8e0250ee),
            AMOUNT,
            destinationDomain,
            bytes32(uint256(uint160(address(simpleVault)))),
            address(asset),
            bytes32(uint256(uint160(OUTSIDER))),
            0,
            uint32(2000)
        );

        vm.expectRevert(bytes("CCTP destination caller must be zero"));
        _call(tokenMessenger, exclusiveCallerCallData);
    }

    function testCsigmaMockPreservesTheGuardedStandardERC4626Flow() public {
        MockCsigmaV2Pool vault = new MockCsigmaV2Pool(asset);
        guard.whitelistERC4626(address(vault), "cSigma test vault");
        asset.mint(address(simpleVault), AMOUNT);
        _approve(address(vault), AMOUNT);
        _call(address(vault), abi.encodeCall(MockERC4626Vault.deposit, (AMOUNT, address(simpleVault))));

        vm.recordLogs();
        _call(
            address(vault),
            abi.encodeCall(MockERC4626Vault.redeem, (AMOUNT, address(simpleVault), address(simpleVault)))
        );
        _assertWithdrawEvent(vm.getRecordedLogs(), address(vault), AMOUNT, AMOUNT);
        assertEq(asset.balanceOf(address(simpleVault)), AMOUNT);

        vault.setWithdrawalPending(true);
        vm.expectRevert(MockCsigmaV2Pool.WithdrawalPending.selector);
        _call(address(vault), abi.encodeCall(MockERC4626Vault.redeem, (1, address(simpleVault), address(simpleVault))));
    }

    function testPlutusAsyncClaimUsesTheGuardedReceiverAndTransfersTheFulfilledAmount() public {
        MockPlutusVault vault = new MockPlutusVault(asset);
        guard.whitelistERC4626(address(vault), "Plutus test vault");
        asset.mint(address(simpleVault), AMOUNT);
        _approve(address(vault), AMOUNT);
        _call(address(vault), abi.encodeCall(MockERC4626Vault.deposit, (AMOUNT, address(simpleVault))));

        vm.recordLogs();
        _call(
            address(vault),
            abi.encodeCall(MockPlutusVault.requestRedeem, (AMOUNT, address(simpleVault), address(simpleVault)))
        );
        Vm.Log memory requestLog = _findEvent(vm.getRecordedLogs(), address(vault), PLUTUS_REDEEM_REQUESTED_EVENT);
        (address sender, uint256 requestedShares) = abi.decode(requestLog.data, (address, uint256));
        assertEq(sender, address(simpleVault), "RedeemRequested sender");
        assertEq(requestedShares, AMOUNT, "RedeemRequested shares");

        vm.recordLogs();
        vault.fulfillRedeem(1);
        Vm.Log memory fulfilmentLog = _findEvent(vm.getRecordedLogs(), address(vault), PLUTUS_REDEEM_FULFILLED_EVENT);
        (uint256 fulfilledShares, uint256 fulfilledAssets) = abi.decode(fulfilmentLog.data, (uint256, uint256));
        assertEq(fulfilledShares, AMOUNT, "Plutus fulfilled shares");
        assertEq(fulfilledAssets, AMOUNT, "Plutus fulfilled assets");

        vm.expectRevert(bytes("Receiver not whitelisted"));
        _call(address(vault), abi.encodeCall(MockPlutusVault.redeem, (1, OUTSIDER)));

        vm.recordLogs();
        _call(address(vault), abi.encodeCall(MockPlutusVault.redeem, (1, address(simpleVault))));
        _assertWithdrawEvent(vm.getRecordedLogs(), address(vault), AMOUNT, AMOUNT);
        assertEq(asset.balanceOf(address(simpleVault)), AMOUNT, "Plutus redeemed assets");
    }

    function testEmberRequestAndGainsClaimUseTheirGuardedRedemptionSurfaces() public {
        MockEmberVault ember = new MockEmberVault(asset);
        guard.whitelistERC4626(address(ember), "Ember test vault");
        asset.mint(address(simpleVault), AMOUNT * 2);
        _approve(address(ember), AMOUNT);
        _call(address(ember), abi.encodeCall(MockERC4626Vault.deposit, (AMOUNT, address(simpleVault))));

        vm.recordLogs();
        _call(address(ember), abi.encodeCall(MockEmberVault.redeemShares, (AMOUNT, address(simpleVault))));
        Vm.Log memory emberRequest = _findEvent(vm.getRecordedLogs(), address(ember), EMBER_REDEEM_REQUESTED_EVENT);
        (uint256 requestedShares,,,,) = abi.decode(emberRequest.data, (uint256, uint256, uint256, uint256, uint256));
        assertEq(requestedShares, AMOUNT, "Ember requested shares");
        assertEq(ember.balanceOf(address(ember)), AMOUNT, "Ember escrowed shares");
        assertEq(asset.balanceOf(address(simpleVault)), AMOUNT, "Ember request does not pay assets");

        vm.expectRevert(bytes("Receiver not whitelisted"));
        _call(address(ember), abi.encodeCall(MockEmberVault.redeemShares, (1, OUTSIDER)));

        // Ember's manager does not emit an operator settlement call. The mock
        // settles separately to prove the queued request's eventual accounting.
        vm.recordLogs();
        ember.processWithdrawalRequests(1);
        Vm.Log memory emberProcessed = _findEvent(vm.getRecordedLogs(), address(ember), EMBER_REQUEST_PROCESSED_EVENT);
        (uint256 processedShares, uint256 redeemedAssets,,,,,,,,) = abi.decode(
            emberProcessed.data, (uint256, uint256, uint256, uint256, bool, bool, uint256, uint256, uint256, uint256)
        );
        assertEq(processedShares, AMOUNT, "Ember processed shares");
        assertEq(redeemedAssets, AMOUNT, "Ember processed assets");
        assertEq(ember.totalSupply(), 0, "Ember processed share burn");
        assertEq(asset.balanceOf(address(simpleVault)), AMOUNT * 2, "Ember redeemed assets");

        MockGainsV1Vault gains = new MockGainsV1Vault(asset);
        guard.whitelistERC4626(address(gains), "Gains test vault");
        _approve(address(gains), AMOUNT);
        _call(address(gains), abi.encodeCall(MockERC4626Vault.deposit, (AMOUNT, address(simpleVault))));

        vm.expectRevert(bytes("Owner not whitelisted"));
        _call(address(gains), abi.encodeCall(MockGainsV1Vault.makeWithdrawRequest, (1, OUTSIDER)));

        vm.recordLogs();
        _call(address(gains), abi.encodeCall(MockGainsV1Vault.makeWithdrawRequest, (AMOUNT, address(simpleVault))));
        Vm.Log memory gainsRequest = _findEvent(vm.getRecordedLogs(), address(gains), GAINS_WITHDRAW_REQUESTED_EVENT);
        (uint256 requestedGainsShares, uint256 currentEpoch) = abi.decode(gainsRequest.data, (uint256, uint256));
        assertEq(requestedGainsShares, AMOUNT, "Gains requested shares");
        assertEq(currentEpoch, 0, "Gains request epoch");
        assertEq(gains.pendingWithdrawalShares(address(simpleVault)), AMOUNT);

        vm.recordLogs();
        gains.forceNewEpoch();
        _findEvent(vm.getRecordedLogs(), address(gains), GAINS_EPOCH_ADVANCED_EVENT);
        assertEq(gains.currentEpoch(), 1, "Gains settlement epoch");

        vm.recordLogs();
        _call(
            address(gains),
            abi.encodeCall(MockERC4626Vault.redeem, (AMOUNT, address(simpleVault), address(simpleVault)))
        );
        _assertWithdrawEvent(vm.getRecordedLogs(), address(gains), AMOUNT, AMOUNT);
        assertEq(asset.balanceOf(address(simpleVault)), AMOUNT * 2, "Gains redeemed assets");
    }

    function testOstiumAsyncSelectorsReachTheConfiguredTarget() public {
        MockOstiumV15Vault vault = new MockOstiumV15Vault(asset);
        guard.whitelistERC4626(address(vault), "Ostium test vault");
        asset.mint(address(simpleVault), AMOUNT);
        _approve(address(vault), AMOUNT);

        _call(address(vault), abi.encodeCall(MockOstiumV15Vault.requestDeposit, (AMOUNT)));

        vm.recordLogs();
        vault.tryNewSettlement();
        Vm.Log memory depositSettlementLog = _findEvent(vm.getRecordedLogs(), address(vault), OSTIUM_SETTLEMENT_EVENT);
        (, uint256 settledDepositAssets, uint256 settledDepositShares,) =
            abi.decode(depositSettlementLog.data, (int256, uint256, uint256, uint256));
        assertEq(settledDepositAssets, AMOUNT, "Ostium settled deposit assets");
        assertEq(settledDepositShares, 0, "Ostium settled withdrawal shares");

        _call(address(vault), abi.encodeCall(MockOstiumV15Vault.claimDeposit, (uint32(1))));
        assertEq(vault.balanceOf(address(simpleVault)), AMOUNT);
        _call(address(vault), abi.encodeCall(MockOstiumV15Vault.requestWithdraw, (AMOUNT)));

        vm.recordLogs();
        vault.tryNewSettlement();
        Vm.Log memory withdrawalSettlementLog =
            _findEvent(vm.getRecordedLogs(), address(vault), OSTIUM_SETTLEMENT_EVENT);
        (, uint256 settledAssets, uint256 settledShares,) =
            abi.decode(withdrawalSettlementLog.data, (int256, uint256, uint256, uint256));
        assertEq(settledAssets, 0, "Ostium settlement has no deposit assets");
        assertEq(settledShares, AMOUNT, "Ostium settled withdrawal shares");

        vm.recordLogs();
        _call(address(vault), abi.encodeCall(MockOstiumV15Vault.claimWithdraw, (uint32(2))));
        Vm.Log memory claimLog = _findEvent(vm.getRecordedLogs(), address(vault), OSTIUM_WITHDRAW_CLAIMED_EVENT);
        uint256 redeemedAssets = abi.decode(claimLog.data, (uint256));
        assertEq(redeemedAssets, AMOUNT, "Ostium claim assets");
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

        vm.warp(block.timestamp + vault.COOLDOWN_DURATION());
        vm.recordLogs();
        _call(address(vault), abi.encodeCall(MockNaraVault.unstake, (address(simpleVault))));
        Vm.Log memory transferLog = _findEvent(vm.getRecordedLogs(), address(asset), TRANSFER_EVENT);
        uint256 redeemedAssets = abi.decode(transferLog.data, (uint256));
        assertEq(redeemedAssets, AMOUNT, "Nara unstake transfer amount");
        assertEq(asset.balanceOf(address(simpleVault)), AMOUNT);
    }

    function testUpshiftDepositChecksAssetAndReceiver() public {
        MockUpshiftVault vault = new MockUpshiftVault(asset);
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

    function testUpshiftInstantRedeemChecksReceiverAndTransfersAssets() public {
        MockUpshiftVault vault = new MockUpshiftVault(asset);
        guard.whitelistUpshift(address(vault), address(asset), "Upshift test vault");
        asset.mint(address(simpleVault), AMOUNT);
        _approve(address(vault), AMOUNT);
        _call(address(vault), abi.encodeCall(MockUpshiftVault.deposit, (address(asset), AMOUNT, address(simpleVault))));

        vm.expectRevert(bytes("Receiver not whitelisted"));
        _call(address(vault), abi.encodeCall(MockUpshiftVault.instantRedeem, (AMOUNT, OUTSIDER)));

        vm.recordLogs();
        _call(address(vault), abi.encodeCall(MockUpshiftVault.instantRedeem, (AMOUNT, address(simpleVault))));
        _assertWithdrawEvent(vm.getRecordedLogs(), address(vault), AMOUNT, AMOUNT);
        assertEq(asset.balanceOf(address(simpleVault)), AMOUNT, "Upshift instant redeem transfer amount");
    }

    function testUpshiftQueuedRedeemSettlementAndClaim() public {
        MockUpshiftVault vault = new MockUpshiftVault(asset);
        guard.whitelistUpshift(address(vault), address(asset), "Upshift test vault");
        asset.mint(address(simpleVault), AMOUNT);
        _approve(address(vault), AMOUNT);
        _call(address(vault), abi.encodeCall(MockUpshiftVault.deposit, (address(asset), AMOUNT, address(simpleVault))));

        vm.expectRevert(bytes("Receiver not whitelisted"));
        _call(address(vault), abi.encodeCall(MockUpshiftVault.requestRedeem, (AMOUNT, OUTSIDER)));

        vm.recordLogs();
        _call(address(vault), abi.encodeCall(MockUpshiftVault.requestRedeem, (AMOUNT, address(simpleVault))));
        Vm.Log memory requestLog = _findEvent(vm.getRecordedLogs(), address(vault), UPSHIFT_WITHDRAWAL_REQUESTED_EVENT);
        uint256 requestedShares = abi.decode(requestLog.data, (uint256));
        assertEq(requestedShares, AMOUNT, "Upshift requested shares");

        // Settlement is an operator action and deliberately has no GuardV0
        // call-site permission. It may be performed by any keeper account.
        vm.recordLogs();
        vm.prank(OUTSIDER);
        vault.processAllClaimsByDate(vault.CLAIM_YEAR(), vault.CLAIM_MONTH(), vault.CLAIM_DAY(), 1);
        Vm.Log memory processedLog =
            _findEvent(vm.getRecordedLogs(), address(vault), UPSHIFT_WITHDRAWAL_PROCESSED_EVENT);
        uint256 processedAssets = abi.decode(processedLog.data, (uint256));
        assertEq(processedAssets, AMOUNT, "Upshift processed assets");

        uint256 claimYear = vault.CLAIM_YEAR();
        uint256 claimMonth = vault.CLAIM_MONTH();
        uint256 claimDay = vault.CLAIM_DAY();

        vm.expectRevert(bytes("Receiver not whitelisted"));
        _call(address(vault), abi.encodeCall(MockUpshiftVault.claim, (claimYear, claimMonth, claimDay, OUTSIDER)));

        vm.recordLogs();
        _call(
            address(vault),
            abi.encodeCall(MockUpshiftVault.claim, (claimYear, claimMonth, claimDay, address(simpleVault)))
        );
        _assertWithdrawEvent(vm.getRecordedLogs(), address(vault), AMOUNT, AMOUNT);
        assertEq(asset.balanceOf(address(simpleVault)), AMOUNT, "Upshift queued claim transfer amount");
    }
}
