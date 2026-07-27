// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

/// Test-only token and vault targets for GuardV0 selector tests.
///
/// These contracts intentionally model manager-generated call surfaces and
/// the minimal counterpart settlement needed to verify their emitted events
/// and token accounting. They are not production protocol ABIs and must never
/// be used as deployment targets or as a source of protocol accounting
/// behaviour.

contract GuardMockERC20 {
    string public name;
    string public symbol;
    uint8 public immutable decimals;

    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    event Approval(address indexed owner, address indexed spender, uint256 value);
    event Transfer(address indexed from, address indexed to, uint256 value);

    constructor(string memory name_, string memory symbol_, uint8 decimals_) {
        name = name_;
        symbol = symbol_;
        decimals = decimals_;
    }

    function mint(address to, uint256 amount) external {
        balanceOf[to] += amount;
        emit Transfer(address(0), to, amount);
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        emit Approval(msg.sender, spender, amount);
        return true;
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        _transfer(msg.sender, to, amount);
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        uint256 approved = allowance[from][msg.sender];
        require(approved >= amount, "Insufficient allowance");
        allowance[from][msg.sender] = approved - amount;
        _transfer(from, to, amount);
        return true;
    }

    function _transfer(address from, address to, uint256 amount) internal {
        require(balanceOf[from] >= amount, "Insufficient balance");
        balanceOf[from] -= amount;
        balanceOf[to] += amount;
        emit Transfer(from, to, amount);
    }
}

contract MockERC4626Vault {
    GuardMockERC20 public immutable assetToken;

    mapping(address => uint256) public balanceOf;
    uint256 public totalSupply;
    address public lastReceiver;
    address public lastOwner;
    uint256 public lastAmount;

    event Deposit(address indexed sender, address indexed owner, uint256 assets, uint256 shares);
    event Withdraw(
        address indexed sender, address indexed receiver, address indexed owner, uint256 assets, uint256 shares
    );

    constructor(GuardMockERC20 asset_) {
        assetToken = asset_;
    }

    function asset() external view returns (address) {
        return address(assetToken);
    }

    function deposit(uint256 assets, address receiver) public virtual returns (uint256 shares) {
        _pull(msg.sender, assets);
        balanceOf[receiver] += assets;
        totalSupply += assets;
        lastReceiver = receiver;
        lastAmount = assets;
        emit Deposit(msg.sender, receiver, assets, assets);
        return assets;
    }

    function withdraw(uint256 assets, address receiver, address owner) external virtual returns (uint256 shares) {
        _burn(owner, assets);
        _push(receiver, assets);
        lastReceiver = receiver;
        lastOwner = owner;
        lastAmount = assets;
        emit Withdraw(msg.sender, receiver, owner, assets, assets);
        return assets;
    }

    function redeem(uint256 shares, address receiver, address owner) public virtual returns (uint256 assets) {
        _burn(owner, shares);
        _push(receiver, shares);
        lastReceiver = receiver;
        lastOwner = owner;
        lastAmount = shares;
        emit Withdraw(msg.sender, receiver, owner, shares, shares);
        return shares;
    }

    function _burn(address owner, uint256 shares) internal {
        require(balanceOf[owner] >= shares, "Insufficient shares");
        if (msg.sender != owner) {
            // The mock deliberately requires an ERC-20-style allowance only
            // when a distinct owner is used, mirroring the Guard threat model.
            revert("Delegated shares unsupported");
        }
        balanceOf[owner] -= shares;
        totalSupply -= shares;
    }

    function _pull(address from, uint256 amount) internal {
        require(assetToken.transferFrom(from, address(this), amount), "Asset transfer failed");
    }

    function _push(address receiver, uint256 amount) internal {
        require(assetToken.transfer(receiver, amount), "Asset transfer failed");
    }
}

contract MockCsigmaV2Pool is MockERC4626Vault {
    error WithdrawalPending();

    bool public withdrawalPending;

    constructor(GuardMockERC20 asset_) MockERC4626Vault(asset_) {}

    function setWithdrawalPending(bool value) external {
        withdrawalPending = value;
    }

    function redeem(uint256 shares, address receiver, address owner) public override returns (uint256 assets) {
        if (withdrawalPending) revert WithdrawalPending();
        return super.redeem(shares, receiver, owner);
    }
}

/// @dev YieldNest-shaped ERC-4626 mock with an explicit local liquidity gate.
///      The production ynRWAx contract limits immediate withdrawals through
///      maxRedeem(owner). Focused Anvil tests may enable this mock-only switch
///      through YieldNestDepositManager.force_settle(..., ignore_liquidity=true)
///      to exercise the Guard-approved redeem call without claiming that a
///      live buffer exists.
contract MockYieldNestVault is MockERC4626Vault {
    error ExceededMaxRedeem(address owner, uint256 shares, uint256 maxShares);

    bool public ignoreLiquidity;

    event LiquidityOverrideSet(bool ignoreLiquidity);

    constructor(GuardMockERC20 asset_) MockERC4626Vault(asset_) {}

    function maxRedeem(address owner) public view returns (uint256) {
        return ignoreLiquidity ? balanceOf[owner] : 0;
    }

    function setIgnoreLiquidity(bool value) external {
        ignoreLiquidity = value;
        emit LiquidityOverrideSet(value);
    }

    function redeem(uint256 shares, address receiver, address owner) public override returns (uint256 assets) {
        uint256 maximum = maxRedeem(owner);
        if (shares > maximum) revert ExceededMaxRedeem(owner, shares, maximum);
        return super.redeem(shares, receiver, owner);
    }
}

contract MockERC7540Vault is MockERC4626Vault {
    struct Request {
        //: Raw request assets for deposits or raw request shares for redemptions.
        uint256 amount;
        //: The remaining claimable raw amount after operator settlement.
        uint256 claimableAmount;
        address controller;
        address owner;
        bool pending;
    }

    uint256 public nextRequestId;
    mapping(uint256 => Request) public depositRequests;
    mapping(uint256 => Request) public redeemRequests;
    mapping(uint256 => bool) public depositRequestSettled;
    mapping(uint256 => bool) public redeemRequestSettled;
    mapping(address => uint256) public pendingDepositAssets;
    mapping(address => uint256) public claimableDepositAssets;
    mapping(address => uint256) public pendingRedeemShares;
    mapping(address => uint256) public claimableRedeemShares;
    mapping(address => uint256[]) private claimableDepositRequestIds;
    mapping(address => uint256[]) private claimableRedeemRequestIds;
    mapping(address => uint256) private depositClaimCursor;
    mapping(address => uint256) private redeemClaimCursor;

    event DepositRequest(
        address indexed controller, address indexed owner, uint256 indexed requestId, address sender, uint256 assets
    );
    /// Test-only operator settlement marker for the otherwise unspecified ERC-7540 settlement action.
    event DepositClaimable(uint256 indexed requestId, address indexed controller, uint256 assets, uint256 shares);
    event RedeemRequest(
        address indexed controller, address indexed owner, uint256 indexed requestId, address sender, uint256 shares
    );
    /// Accountable-compatible operator settlement marker for the otherwise unspecified ERC-7540 settlement action.
    event RedeemClaimable(address indexed controller, uint256 indexed requestId, uint256 assets, uint256 shares);

    constructor(GuardMockERC20 asset_) MockERC4626Vault(asset_) {}

    function requestDeposit(uint256 assets, address controller, address owner)
        public
        virtual
        returns (uint256 requestId)
    {
        require(controller == msg.sender, "Unexpected controller");
        _pull(msg.sender, assets);
        requestId = ++nextRequestId;
        depositRequests[requestId] = Request(assets, 0, controller, owner, true);
        pendingDepositAssets[controller] += assets;
        lastReceiver = controller;
        lastOwner = owner;
        lastAmount = assets;
        emit DepositRequest(controller, owner, requestId, msg.sender, assets);
    }

    function requestRedeem(uint256 shares, address controller, address owner)
        public
        virtual
        returns (uint256 requestId)
    {
        require(controller == msg.sender, "Unexpected controller");
        _burn(owner, shares);
        requestId = ++nextRequestId;
        redeemRequests[requestId] = Request(shares, 0, controller, owner, true);
        pendingRedeemShares[controller] += shares;
        lastReceiver = controller;
        lastOwner = owner;
        lastAmount = shares;
        emit RedeemRequest(controller, owner, requestId, msg.sender, shares);
    }

    function requestWithdraw(uint256 assets, address controller, address owner) external returns (uint256 requestId) {
        return requestRedeem(assets, controller, owner);
    }

    /// Mark an ERC-7540 deposit request claimable.
    ///
    /// ERC-7540 does not specify the operator settlement method. This test-only
    /// external operator action makes a pending request claimable before the
    /// guarded three-argument ``deposit`` claim emits its standard ``Deposit``.
    function fulfillDepositRequest(uint256 requestId) external {
        Request storage request = depositRequests[requestId];
        require(request.pending, "Unknown request");
        require(!depositRequestSettled[requestId], "Already fulfilled");
        request.pending = false;
        request.claimableAmount = request.amount;
        depositRequestSettled[requestId] = true;
        pendingDepositAssets[request.controller] -= request.amount;
        claimableDepositAssets[request.controller] += request.amount;
        claimableDepositRequestIds[request.controller].push(requestId);
        emit DepositClaimable(requestId, request.controller, request.amount, request.amount);
    }

    /// Mark an Accountable/ERC-7540 redemption request claimable.
    ///
    /// The deployed protocol executes this strategy-operator step separately
    /// from the asset manager's guarded request and claim calls.
    function fulfillRedeemRequest(uint256 requestId) external {
        Request storage request = redeemRequests[requestId];
        require(request.pending, "Unknown request");
        require(!redeemRequestSettled[requestId], "Already fulfilled");
        request.pending = false;
        request.claimableAmount = request.amount;
        redeemRequestSettled[requestId] = true;
        pendingRedeemShares[request.controller] -= request.amount;
        claimableRedeemShares[request.controller] += request.amount;
        claimableRedeemRequestIds[request.controller].push(requestId);
        emit RedeemClaimable(request.controller, requestId, request.amount, request.amount);
    }

    function pendingDepositRequest(uint256 requestId, address controller) external view returns (uint256 assets) {
        if (requestId == 0) return pendingDepositAssets[controller];
        Request storage request = depositRequests[requestId];
        return request.controller == controller && request.pending ? request.amount : 0;
    }

    function claimableDepositRequest(uint256 requestId, address controller) external view returns (uint256 assets) {
        if (requestId == 0) return claimableDepositAssets[controller];
        Request storage request = depositRequests[requestId];
        return request.controller == controller ? request.claimableAmount : 0;
    }

    function pendingRedeemRequest(uint256 requestId, address controller) external view returns (uint256 shares) {
        if (requestId == 0) return pendingRedeemShares[controller];
        Request storage request = redeemRequests[requestId];
        return request.controller == controller && request.pending ? request.amount : 0;
    }

    function claimableRedeemRequest(uint256 requestId, address controller) external view returns (uint256 shares) {
        if (requestId == 0) return claimableRedeemShares[controller];
        Request storage request = redeemRequests[requestId];
        return request.controller == controller ? request.claimableAmount : 0;
    }

    function deposit(uint256 assets, address receiver, address controller) external returns (uint256 shares) {
        require(controller == msg.sender, "Unexpected controller");
        require(claimableDepositAssets[controller] >= assets, "Deposit not claimable");
        _consumeDepositClaims(controller, assets);
        balanceOf[receiver] += assets;
        totalSupply += assets;
        lastReceiver = receiver;
        lastOwner = controller;
        lastAmount = assets;
        emit Deposit(msg.sender, receiver, assets, assets);
        return assets;
    }

    function redeem(uint256 shares, address receiver, address controller) public override returns (uint256 assets) {
        require(controller == msg.sender, "Unexpected controller");
        require(claimableRedeemShares[controller] >= shares, "Redeem not claimable");
        _consumeRedeemClaims(controller, shares);
        _push(receiver, shares);
        lastReceiver = receiver;
        lastOwner = controller;
        lastAmount = shares;
        emit Withdraw(msg.sender, receiver, controller, shares, shares);
        return shares;
    }

    function _consumeDepositClaims(address controller, uint256 assets) internal {
        claimableDepositAssets[controller] -= assets;
        uint256 remaining = assets;
        uint256[] storage requestIds = claimableDepositRequestIds[controller];
        uint256 cursor = depositClaimCursor[controller];
        while (remaining != 0) {
            Request storage request = depositRequests[requestIds[cursor]];
            uint256 available = request.claimableAmount;
            if (available > remaining) {
                request.claimableAmount = available - remaining;
                return;
            }
            remaining -= available;
            request.claimableAmount = 0;
            unchecked {
                ++cursor;
            }
            depositClaimCursor[controller] = cursor;
        }
    }

    function _consumeRedeemClaims(address controller, uint256 shares) internal {
        claimableRedeemShares[controller] -= shares;
        uint256 remaining = shares;
        uint256[] storage requestIds = claimableRedeemRequestIds[controller];
        uint256 cursor = redeemClaimCursor[controller];
        while (remaining != 0) {
            Request storage request = redeemRequests[requestIds[cursor]];
            uint256 available = request.claimableAmount;
            if (available > remaining) {
                request.claimableAmount = available - remaining;
                return;
            }
            remaining -= available;
            request.claimableAmount = 0;
            unchecked {
                ++cursor;
            }
            redeemClaimCursor[controller] = cursor;
        }
    }
}

/// Plutus Hedge's asynchronous redemption claim surface.
///
/// Plutus uses ``requestRedeem(shares, controller, owner)`` followed by an
/// operator fulfilment and ``redeem(requestId, receiver)``. The mock exposes
/// the manager-owned request and claim calls, while keeping fulfilment separate
/// because it is an operator-only action in the deployed protocol.
contract MockPlutusVault is MockERC4626Vault {
    struct RedeemRequest {
        address controller;
        address owner;
        uint256 shares;
        uint256 assets;
        bool fulfilled;
    }

    uint256 public nextRequestId;
    mapping(uint256 => RedeemRequest) public redeemRequests;

    event RedeemRequested(
        address indexed controller, address indexed owner, uint256 indexed requestId, address sender, uint256 shares
    );
    event RedeemFulfilled(uint256 indexed requestId, address indexed controller, uint256 shares, uint256 lockedAssets);
    event RedeemCancelled(uint256 indexed requestId, address indexed controller, address indexed owner, uint256 shares);

    constructor(GuardMockERC20 asset_) MockERC4626Vault(asset_) {}

    function requestRedeem(uint256 shares, address controller, address owner) external returns (uint256 requestId) {
        require(controller == msg.sender, "Unexpected controller");
        _burn(owner, shares);
        requestId = ++nextRequestId;
        redeemRequests[requestId] = RedeemRequest(controller, owner, shares, 0, false);
        lastReceiver = controller;
        lastOwner = owner;
        lastAmount = shares;
        emit RedeemRequested(controller, owner, requestId, msg.sender, shares);
    }

    function fulfillRedeem(uint256 requestId) external {
        RedeemRequest storage request = redeemRequests[requestId];
        require(request.owner != address(0), "Unknown request");
        require(!request.fulfilled, "Already fulfilled");
        request.assets = request.shares;
        request.fulfilled = true;
        emit RedeemFulfilled(requestId, request.controller, request.shares, request.assets);
    }

    function redeem(uint256 requestId, address receiver) external returns (uint256 assets) {
        RedeemRequest memory request = redeemRequests[requestId];
        require(request.owner == msg.sender, "Unexpected owner");
        require(request.fulfilled, "Request not fulfilled");
        delete redeemRequests[requestId];
        _push(receiver, request.assets);
        lastReceiver = receiver;
        lastOwner = request.owner;
        lastAmount = request.assets;
        emit Withdraw(msg.sender, receiver, request.owner, request.assets, request.shares);
        return request.assets;
    }

    function cancelRedeemRequest(uint256 requestId) external {
        RedeemRequest memory request = redeemRequests[requestId];
        require(request.owner == msg.sender, "Unexpected owner");
        require(!request.fulfilled, "Already fulfilled");
        delete redeemRequests[requestId];
        balanceOf[request.owner] += request.shares;
        totalSupply += request.shares;
        emit RedeemCancelled(requestId, request.controller, request.owner, request.shares);
    }
}

contract MockEmberVault is MockERC4626Vault {
    struct WithdrawalRequest {
        address owner;
        address receiver;
        uint256 shares;
        uint256 timestamp;
    }

    uint256 public nextSequenceNumber;
    uint256 public nextRequestToProcess = 1;
    mapping(uint256 => WithdrawalRequest) public withdrawalRequests;

    event RequestRedeemed(
        address indexed vault,
        address indexed owner,
        address indexed receiver,
        uint256 shares,
        uint256 timestamp,
        uint256 totalShares,
        uint256 totalSharesPendingToBurn,
        uint256 sequenceNumber
    );
    event RequestProcessed(
        address indexed vault,
        address indexed owner,
        address indexed receiver,
        uint256 shares,
        uint256 withdrawAmount,
        uint256 requestTimestamp,
        uint256 processTimestamp,
        bool skipped,
        bool cancelled,
        uint256 totalShares,
        uint256 totalSharesPendingToBurn,
        uint256 sequenceNumber,
        uint256 requestSequenceNumber
    );

    constructor(GuardMockERC20 asset_) MockERC4626Vault(asset_) {}

    function redeemShares(uint256 shares, address receiver) external returns (uint256 requestId) {
        require(balanceOf[msg.sender] >= shares, "Insufficient shares");
        balanceOf[msg.sender] -= shares;
        balanceOf[address(this)] += shares;
        requestId = ++nextSequenceNumber;
        // Ember emits request timestamps in milliseconds, unlike EVM block
        // timestamps. Keep the protocol event unit so manager parsers can use
        // the mock without a special case.
        uint256 timestampMs = block.timestamp * 1_000;
        withdrawalRequests[requestId] = WithdrawalRequest(msg.sender, receiver, shares, timestampMs);
        lastReceiver = receiver;
        lastOwner = msg.sender;
        lastAmount = shares;
        emit RequestRedeemed(address(this), msg.sender, receiver, shares, timestampMs, totalSupply, shares, requestId);
    }

    /// Process one queued request as Ember's operator would.
    ///
    /// This is deliberately not a GuardV0 call surface: EmberDepositManager
    /// never emits this operator-only function for the asset manager.
    function processWithdrawalRequests(uint256 numRequests) external returns (uint256 assets) {
        for (uint256 i; i < numRequests; ++i) {
            uint256 requestId = nextRequestToProcess++;
            WithdrawalRequest memory request = withdrawalRequests[requestId];
            require(request.owner != address(0), "Unknown withdrawal");
            delete withdrawalRequests[requestId];
            balanceOf[address(this)] -= request.shares;
            totalSupply -= request.shares;
            _push(request.receiver, request.shares);
            emit RequestProcessed(
                address(this),
                request.owner,
                request.receiver,
                request.shares,
                request.shares,
                request.timestamp,
                block.timestamp * 1_000,
                false,
                false,
                totalSupply,
                0,
                ++nextSequenceNumber,
                requestId
            );
            assets += request.shares;
        }
    }
}

contract MockGainsV1Vault is MockERC4626Vault {
    mapping(address => uint256) public pendingWithdrawalShares;
    uint16 public currentEpoch;

    event WithdrawRequested(
        address indexed sender, address indexed owner, uint256 shares, uint256 currEpoch, uint256 indexed unlockEpoch
    );
    event EpochAdvanced(uint16 indexed previousEpoch, uint16 indexed newEpoch);

    constructor(GuardMockERC20 asset_) MockERC4626Vault(asset_) {}

    /// Queue shares owned by ``owner`` for a later ERC-4626 redemption claim.
    ///
    /// Gains V1's production ABI names this argument ``owner``. The later
    /// ``redeem`` call independently supplies its payout receiver.
    function makeWithdrawRequest(uint256 shares, address owner) external {
        require(owner == msg.sender, "Unexpected owner");
        _burn(owner, shares);
        pendingWithdrawalShares[owner] += shares;
        lastOwner = owner;
        lastAmount = shares;
        emit WithdrawRequested(msg.sender, owner, shares, currentEpoch, currentEpoch + 1);
    }

    /// Advance the mock's permissionless epoch settlement by one round.
    ///
    /// Gains V1 has no dedicated vault settlement event in its published ABI;
    /// this test-only event makes the mock's state transition observable.
    function forceNewEpoch() external {
        uint16 previousEpoch = currentEpoch;
        currentEpoch = previousEpoch + 1;
        emit EpochAdvanced(previousEpoch, currentEpoch);
    }

    function redeem(uint256 shares, address receiver, address owner) public override returns (uint256 assets) {
        require(owner == msg.sender, "Unexpected owner");
        require(pendingWithdrawalShares[owner] >= shares, "Withdrawal not claimable");
        require(currentEpoch > 0, "Withdrawal not claimable");
        pendingWithdrawalShares[owner] -= shares;
        _push(receiver, shares);
        lastReceiver = receiver;
        lastOwner = owner;
        lastAmount = shares;
        emit Withdraw(msg.sender, receiver, owner, shares, shares);
        return shares;
    }
}

contract MockOstiumV15Vault is MockERC4626Vault {
    uint32 public nextRequestId;
    uint32 public lastSettlementId;
    mapping(uint32 => uint256) public deposits;
    mapping(uint32 => uint256) public withdrawals;
    mapping(uint32 => bool) public depositClaimable;
    mapping(uint32 => bool) public withdrawalClaimable;

    event DepositRequestedV2(address indexed owner, uint32 indexed settlementId, uint256 assets);
    event DepositClaimedV2(address indexed owner, uint32 indexed settlementId, uint256 shares);
    event WithdrawRequestedV2(address indexed owner, uint32 indexed settlementId, uint256 shares);
    event WithdrawClaimedV2(address indexed owner, uint32 indexed settlementId, uint256 assets);
    event AsyncDepositWithdrawExecuted(
        uint32 indexed settlementId,
        int256 deltaShares,
        uint256 totalAssetsToDeposit,
        uint256 totalSharesToWithdraw,
        uint256 shareToAssetsPrice
    );

    constructor(GuardMockERC20 asset_) MockERC4626Vault(asset_) {}

    function requestDeposit(uint256 assets) external returns (uint32 requestId) {
        _pull(msg.sender, assets);
        requestId = ++nextRequestId;
        deposits[requestId] = assets;
        lastAmount = assets;
        emit DepositRequestedV2(msg.sender, requestId, assets);
    }

    function claimDeposit(uint32 requestId) external {
        uint256 assets = deposits[requestId];
        require(assets > 0, "Unknown deposit");
        require(depositClaimable[requestId], "Deposit not claimable");
        balanceOf[msg.sender] += assets;
        totalSupply += assets;
        delete deposits[requestId];
        emit DepositClaimedV2(msg.sender, requestId, assets);
    }

    function cancelRequestDeposit(uint32 requestId, uint256) public {
        uint256 assets = deposits[requestId];
        require(assets > 0, "Unknown deposit");
        delete deposits[requestId];
        _push(msg.sender, assets);
    }

    function reclaimDeposit(uint32 requestId) external {
        cancelRequestDeposit(requestId, 0);
    }

    function requestWithdraw(uint256 shares) external returns (uint32 requestId) {
        _burn(msg.sender, shares);
        requestId = ++nextRequestId;
        withdrawals[requestId] = shares;
        lastAmount = shares;
        emit WithdrawRequestedV2(msg.sender, requestId, shares);
    }

    /// Settle every currently pending async request in one mock settlement.
    ///
    /// Ostium V1.5 exposes a permissionless ``tryNewSettlement()`` round. The
    /// mock uses one-to-one request and settlement ids but preserves the
    /// protocol's terminal ``AsyncDepositWithdrawExecuted`` event shape.
    function tryNewSettlement() external returns (uint32 settlementId) {
        uint256 totalAssetsToDeposit;
        uint256 totalSharesToWithdraw;
        for (uint32 requestId = 1; requestId <= nextRequestId; ++requestId) {
            if (deposits[requestId] > 0 && !depositClaimable[requestId]) {
                depositClaimable[requestId] = true;
                totalAssetsToDeposit += deposits[requestId];
            }
            if (withdrawals[requestId] > 0 && !withdrawalClaimable[requestId]) {
                withdrawalClaimable[requestId] = true;
                totalSharesToWithdraw += withdrawals[requestId];
            }
        }
        settlementId = ++lastSettlementId;
        emit AsyncDepositWithdrawExecuted(
            settlementId,
            int256(totalAssetsToDeposit) - int256(totalSharesToWithdraw),
            totalAssetsToDeposit,
            totalSharesToWithdraw,
            1e18
        );
    }

    function claimWithdraw(uint32 requestId) external {
        uint256 assets = withdrawals[requestId];
        require(assets > 0, "Unknown withdrawal");
        require(withdrawalClaimable[requestId], "Withdrawal not claimable");
        delete withdrawals[requestId];
        _push(msg.sender, assets);
        emit WithdrawClaimedV2(msg.sender, requestId, assets);
    }

    function cancelRequestWithdraw(uint32 requestId, uint256) public {
        uint256 shares = withdrawals[requestId];
        require(shares > 0, "Unknown withdrawal");
        delete withdrawals[requestId];
        balanceOf[msg.sender] += shares;
        totalSupply += shares;
    }

    function reclaimWithdraw(uint32 requestId) external {
        cancelRequestWithdraw(requestId, 0);
    }
}

contract MockNaraVault is MockERC4626Vault {
    mapping(address => uint256) public cooldownSharesByOwner;
    mapping(address => uint256) public cooldownEndsAt;
    uint256 public constant COOLDOWN_DURATION = 7 days;

    constructor(GuardMockERC20 asset_) MockERC4626Vault(asset_) {}

    function cooldownShares(uint256 shares) external {
        _burn(msg.sender, shares);
        cooldownSharesByOwner[msg.sender] = shares;
        cooldownEndsAt[msg.sender] = block.timestamp + COOLDOWN_DURATION;
        lastOwner = msg.sender;
        lastAmount = shares;
    }

    function unstake(address receiver) external {
        uint256 assets = cooldownSharesByOwner[msg.sender];
        require(assets > 0, "No cooldown");
        require(block.timestamp >= cooldownEndsAt[msg.sender], "Cooldown active");
        delete cooldownSharesByOwner[msg.sender];
        delete cooldownEndsAt[msg.sender];
        _push(receiver, assets);
        lastReceiver = receiver;
        lastOwner = msg.sender;
        lastAmount = assets;
    }
}

contract MockUpshiftVault {
    /// Test-only one-to-one asset/share accounting for the verified
    /// multiAssetVault request/process/claim lifecycle.
    GuardMockERC20 public immutable assetToken;
    address public lastAsset;
    address public lastReceiver;
    uint256 public lastAmount;
    mapping(address => uint256) public balanceOf;
    mapping(address => uint256) public pendingShares;
    mapping(address => uint256) public claimableAssets;
    mapping(address => uint256) public claimableAssetsByReceiver;
    mapping(address => address) public requestedReceiver;
    address[] private pendingOwners;

    uint256 public constant CLAIMABLE_EPOCH = 1;
    uint256 public constant CLAIM_YEAR = 2026;
    uint256 public constant CLAIM_MONTH = 1;
    uint256 public constant CLAIM_DAY = 1;

    event Deposit(
        address assetIn, uint256 amountIn, uint256 shares, address indexed senderAddr, address indexed receiverAddr
    );
    event Withdraw(
        address indexed sender, address indexed receiver, address indexed owner, uint256 assets, uint256 shares
    );
    event WithdrawalRequested(uint256 shares, address indexed holderAddr, address indexed receiverAddr);
    event WithdrawalProcessed(uint256 assetsAmount, address indexed receiverAddr);

    constructor(GuardMockERC20 asset_) {
        assetToken = asset_;
    }

    function deposit(address asset, uint256 amount, address receiver) external returns (uint256 shares) {
        require(asset == address(assetToken), "Unexpected asset");
        require(assetToken.transferFrom(msg.sender, address(this), amount), "Asset transfer failed");
        balanceOf[receiver] += amount;
        lastAsset = asset;
        lastReceiver = receiver;
        lastAmount = amount;
        emit Deposit(asset, amount, amount, msg.sender, receiver);
        return amount;
    }

    function instantRedeem(uint256 shares, address receiver) external {
        _burnShares(msg.sender, shares);
        require(assetToken.transfer(receiver, shares), "Asset transfer failed");
        lastReceiver = receiver;
        lastAmount = shares;
        emit Withdraw(msg.sender, receiver, msg.sender, shares, shares);
    }

    function requestRedeem(uint256 shares, address receiver) external returns (uint256, uint256, uint256, uint256) {
        _burnShares(msg.sender, shares);
        if (pendingShares[msg.sender] == 0) pendingOwners.push(msg.sender);
        pendingShares[msg.sender] += shares;
        requestedReceiver[msg.sender] = receiver;
        lastReceiver = receiver;
        lastAmount = shares;
        emit WithdrawalRequested(shares, msg.sender, receiver);
        return (CLAIMABLE_EPOCH, CLAIM_YEAR, CLAIM_MONTH, CLAIM_DAY);
    }

    /// Test-only stand-in for Upshift's off-manager operator settlement.
    function processAllClaimsByDate(uint256 year, uint256 month, uint256 day, uint256 maxLimit) external {
        require(year == CLAIM_YEAR && month == CLAIM_MONTH && day == CLAIM_DAY, "Unexpected claim date");
        require(maxLimit > 0, "Nothing to process");
        uint256 processCount = pendingOwners.length < maxLimit ? pendingOwners.length : maxLimit;
        uint256 processed;
        for (uint256 i; i < processCount; ++i) {
            address owner = pendingOwners[i];
            uint256 shares = pendingShares[owner];
            if (shares == 0) continue;
            pendingShares[owner] = 0;
            claimableAssets[owner] += shares;
            claimableAssetsByReceiver[requestedReceiver[owner]] += shares;
            emit WithdrawalProcessed(shares, requestedReceiver[owner]);
            processed++;
        }
        require(processed > 0, "Nothing to process");
    }

    function claim(uint256 year, uint256 month, uint256 day, address receiver) external returns (uint256, uint256) {
        require(year == CLAIM_YEAR && month == CLAIM_MONTH && day == CLAIM_DAY, "Unexpected claim date");
        require(receiver == requestedReceiver[msg.sender], "Unexpected receiver");
        uint256 assets = claimableAssets[msg.sender];
        require(assets > 0, "Nothing to claim");
        claimableAssets[msg.sender] = 0;
        claimableAssetsByReceiver[receiver] -= assets;
        require(assetToken.transfer(receiver, assets), "Asset transfer failed");
        lastReceiver = receiver;
        lastAmount = assets;
        emit Withdraw(msg.sender, receiver, msg.sender, assets, assets);
        return (assets, assets);
    }

    function getBurnableAmountByReceiver(uint256 year, uint256 month, uint256 day, address receiver)
        external
        view
        returns (uint256)
    {
        if (year != CLAIM_YEAR || month != CLAIM_MONTH || day != CLAIM_DAY) return 0;
        return claimableAssetsByReceiver[receiver];
    }

    function _burnShares(address owner, uint256 shares) internal {
        require(shares > 0 && balanceOf[owner] >= shares, "Insufficient shares");
        balanceOf[owner] -= shares;
    }
}
