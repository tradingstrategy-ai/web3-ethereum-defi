// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

/// Test-only token and vault targets for GuardV0 selector tests.
///
/// These contracts intentionally model only the manager-generated call
/// surfaces. They are not production protocol ABIs and must never be used as
/// deployment targets or as a source of protocol accounting behaviour.

contract GuardMockERC20 {
    string public name;
    string public symbol;
    uint8 public immutable decimals;

    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    constructor(string memory name_, string memory symbol_, uint8 decimals_) {
        name = name_;
        symbol = symbol_;
        decimals = decimals_;
    }

    function mint(address to, uint256 amount) external {
        balanceOf[to] += amount;
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
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
    }
}

contract MockERC4626Vault {
    GuardMockERC20 public immutable assetToken;

    mapping(address => uint256) public balanceOf;
    uint256 public totalSupply;
    address public lastReceiver;
    address public lastOwner;
    uint256 public lastAmount;

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
        return assets;
    }

    function withdraw(uint256 assets, address receiver, address owner) external virtual returns (uint256 shares) {
        _burn(owner, assets);
        _push(receiver, assets);
        lastReceiver = receiver;
        lastOwner = owner;
        lastAmount = assets;
        return assets;
    }

    function redeem(uint256 shares, address receiver, address owner) public virtual returns (uint256 assets) {
        _burn(owner, shares);
        _push(receiver, shares);
        lastReceiver = receiver;
        lastOwner = owner;
        lastAmount = shares;
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

contract MockERC7540Vault is MockERC4626Vault {
    struct Request {
        uint256 amount;
        address controller;
        address owner;
        bool pending;
    }

    uint256 public nextRequestId;
    mapping(uint256 => Request) public depositRequests;
    mapping(uint256 => Request) public redeemRequests;

    constructor(GuardMockERC20 asset_) MockERC4626Vault(asset_) {}

    function requestDeposit(uint256 assets, address controller, address owner)
        public
        virtual
        returns (uint256 requestId)
    {
        _pull(msg.sender, assets);
        requestId = ++nextRequestId;
        depositRequests[requestId] = Request(assets, controller, owner, true);
        lastReceiver = controller;
        lastOwner = owner;
        lastAmount = assets;
    }

    function requestRedeem(uint256 shares, address controller, address owner)
        public
        virtual
        returns (uint256 requestId)
    {
        requestId = ++nextRequestId;
        redeemRequests[requestId] = Request(shares, controller, owner, true);
        lastReceiver = controller;
        lastOwner = owner;
        lastAmount = shares;
    }

    function requestWithdraw(uint256 assets, address controller, address owner) external returns (uint256 requestId) {
        return requestRedeem(assets, controller, owner);
    }

    function deposit(uint256 assets, address receiver, address controller) external returns (uint256 shares) {
        require(controller == msg.sender, "Unexpected controller");
        balanceOf[receiver] += assets;
        totalSupply += assets;
        lastReceiver = receiver;
        lastOwner = controller;
        lastAmount = assets;
        return assets;
    }

    function redeem(uint256 shares, address receiver, address controller) public override returns (uint256 assets) {
        require(controller == msg.sender, "Unexpected controller");
        _burn(controller, shares);
        _push(receiver, shares);
        lastReceiver = receiver;
        lastOwner = controller;
        lastAmount = shares;
        return shares;
    }
}

contract MockEmberVault is MockERC4626Vault {
    constructor(GuardMockERC20 asset_) MockERC4626Vault(asset_) {}

    function redeemShares(uint256 shares, address receiver) external returns (uint256) {
        _burn(msg.sender, shares);
        _push(receiver, shares);
        lastReceiver = receiver;
        lastOwner = msg.sender;
        lastAmount = shares;
        return shares;
    }
}

contract MockGainsV1Vault is MockERC4626Vault {
    mapping(address => uint256) public pendingWithdrawalShares;

    constructor(GuardMockERC20 asset_) MockERC4626Vault(asset_) {}

    function makeWithdrawRequest(uint256 shares, address receiver) external {
        _burn(msg.sender, shares);
        pendingWithdrawalShares[receiver] += shares;
        lastReceiver = receiver;
        lastOwner = msg.sender;
        lastAmount = shares;
    }
}

contract MockOstiumV15Vault is MockERC4626Vault {
    uint32 public nextRequestId;
    mapping(uint32 => uint256) public deposits;
    mapping(uint32 => uint256) public withdrawals;

    constructor(GuardMockERC20 asset_) MockERC4626Vault(asset_) {}

    function requestDeposit(uint256 assets) external returns (uint32 requestId) {
        _pull(msg.sender, assets);
        requestId = ++nextRequestId;
        deposits[requestId] = assets;
        lastAmount = assets;
    }

    function claimDeposit(uint32 requestId) external {
        uint256 assets = deposits[requestId];
        require(assets > 0, "Unknown deposit");
        balanceOf[msg.sender] += assets;
        totalSupply += assets;
        delete deposits[requestId];
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
    }

    function claimWithdraw(uint32 requestId) external {
        uint256 assets = withdrawals[requestId];
        require(assets > 0, "Unknown withdrawal");
        delete withdrawals[requestId];
        _push(msg.sender, assets);
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

    constructor(GuardMockERC20 asset_) MockERC4626Vault(asset_) {}

    function cooldownShares(uint256 shares) external {
        _burn(msg.sender, shares);
        cooldownSharesByOwner[msg.sender] = shares;
        lastOwner = msg.sender;
        lastAmount = shares;
    }

    function unstake(address receiver) external {
        uint256 assets = cooldownSharesByOwner[msg.sender];
        require(assets > 0, "No cooldown");
        delete cooldownSharesByOwner[msg.sender];
        _push(receiver, assets);
        lastReceiver = receiver;
        lastOwner = msg.sender;
        lastAmount = assets;
    }
}

contract MockUpshiftVault {
    address public lastAsset;
    address public lastReceiver;
    uint256 public lastAmount;

    function deposit(address asset, uint256 amount, address receiver) external {
        require(GuardMockERC20(asset).transferFrom(msg.sender, address(this), amount), "Asset transfer failed");
        lastAsset = asset;
        lastReceiver = receiver;
        lastAmount = amount;
    }
}
