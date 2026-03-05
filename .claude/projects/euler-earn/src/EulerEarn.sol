// SPDX-License-Identifier: GPL-2.0-or-later
pragma solidity 0.8.26;

import {
    MarketConfig,
    PendingUint136,
    PendingAddress,
    MarketAllocation,
    IEulerEarnBase,
    IEulerEarnStaticTyping
} from "./interfaces/IEulerEarn.sol";
import {IEulerEarnFactory} from "./interfaces/IEulerEarnFactory.sol";

import {PendingLib} from "./libraries/PendingLib.sol";
import {ConstantsLib} from "./libraries/ConstantsLib.sol";
import {ErrorsLib} from "./libraries/ErrorsLib.sol";
import {EventsLib} from "./libraries/EventsLib.sol";
import {SafeERC20Permit2Lib} from "./libraries/SafeERC20Permit2Lib.sol";
import {UtilsLib, WAD} from "./libraries/UtilsLib.sol";
import {SafeCast} from "openzeppelin-contracts/utils/math/SafeCast.sol";
import {IERC20Metadata} from "openzeppelin-contracts/token/ERC20/extensions/IERC20Metadata.sol";

import {Context} from "openzeppelin-contracts/utils/Context.sol";
import {ReentrancyGuard} from "openzeppelin-contracts/utils/ReentrancyGuard.sol";
import {Ownable2Step, Ownable} from "openzeppelin-contracts/access/Ownable2Step.sol";
import {
    IERC20,
    IERC4626,
    ERC20,
    ERC4626,
    Math,
    SafeERC20
} from "openzeppelin-contracts/token/ERC20/extensions/ERC4626.sol";
import {EVCUtil} from "ethereum-vault-connector/utils/EVCUtil.sol";

/// @title EulerEarn
/// @author Forked with gratitude from Morpho Labs. Inspired by Silo Labs.
/// @custom:contact security@morpho.org
/// @custom:contact security@euler.xyz
/// @notice ERC4626 compliant vault allowing users to deposit assets to any ERC4626 strategy vault allowed by the factory.
contract EulerEarn is ReentrancyGuard, ERC4626, Ownable2Step, EVCUtil, IEulerEarnStaticTyping {
    using Math for uint256;
    using UtilsLib for uint256;
    using SafeCast for uint256;
    using SafeERC20 for IERC20;
    using SafeERC20Permit2Lib for IERC20;
    using PendingLib for MarketConfig;
    using PendingLib for PendingUint136;
    using PendingLib for PendingAddress;

    /* IMMUTABLES */

    /// @inheritdoc IEulerEarnBase
    address public immutable permit2Address;

    /// @inheritdoc IEulerEarnBase
    address public immutable creator;

    /* STORAGE */

    /// @inheritdoc IEulerEarnBase
    address public curator;

    /// @inheritdoc IEulerEarnBase
    mapping(address => bool) public isAllocator;

    /// @inheritdoc IEulerEarnBase
    address public guardian;

    /// @inheritdoc IEulerEarnStaticTyping
    mapping(IERC4626 => MarketConfig) public config;

    /// @inheritdoc IEulerEarnBase
    uint256 public timelock;

    /// @inheritdoc IEulerEarnStaticTyping
    PendingAddress public pendingGuardian;

    /// @inheritdoc IEulerEarnStaticTyping
    mapping(IERC4626 => PendingUint136) public pendingCap;

    /// @inheritdoc IEulerEarnStaticTyping
    PendingUint136 public pendingTimelock;

    /// @inheritdoc IEulerEarnBase
    uint96 public fee;

    /// @inheritdoc IEulerEarnBase
    address public feeRecipient;

    /// @inheritdoc IEulerEarnBase
    IERC4626[] public supplyQueue;

    /// @inheritdoc IEulerEarnBase
    IERC4626[] public withdrawQueue;

    /// @inheritdoc IEulerEarnBase
    uint256 public lastTotalAssets;

    /// @inheritdoc IEulerEarnBase
    uint256 public lostAssets;

    /// @dev "Overrides" the ERC20's storage variable to be able to modify it.
    string private _name;

    /// @dev "Overrides" the ERC20's storage variable to be able to modify it.
    string private _symbol;

    /* CONSTRUCTOR */

    /// @dev Initializes the contract.
    /// @param owner The owner of the contract.
    /// @param evc The EVC address.
    /// @param permit2 The address of the Permit2 contract.
    /// @param initialTimelock The initial timelock.
    /// @param _asset The address of the underlying asset.
    /// @param __name The name of the Earn vault.
    /// @param __symbol The symbol of the Earn vault.
    /// @dev We pass "" as name and symbol to the ERC20 because these are overriden in this contract.
    /// This means that the contract deviates slightly from the ERC2612 standard.
    constructor(
        address owner,
        address evc,
        address permit2,
        uint256 initialTimelock,
        address _asset,
        string memory __name,
        string memory __symbol
    ) ERC4626(IERC20(_asset)) ERC20("", "") Ownable(owner) EVCUtil(evc) {
        if (initialTimelock != 0) _checkTimelockBounds(initialTimelock);
        _setTimelock(initialTimelock);

        _name = __name;
        emit EventsLib.SetName(__name);

        _symbol = __symbol;
        emit EventsLib.SetSymbol(__symbol);

        permit2Address = permit2;
        creator = msg.sender;
    }

    /* MODIFIERS */

    /// @dev Reverts if the caller doesn't have the curator role.
    modifier onlyCuratorRole() {
        address msgSender = _msgSenderOnlyEVCAccountOwner();
        if (msgSender != curator && msgSender != owner()) revert ErrorsLib.NotCuratorRole();

        _;
    }

    /// @dev Reverts if the caller doesn't have the allocator role.
    modifier onlyAllocatorRole() {
        address msgSender = _msgSenderOnlyEVCAccountOwner();
        if (!isAllocator[msgSender] && msgSender != curator && msgSender != owner()) {
            revert ErrorsLib.NotAllocatorRole();
        }

        _;
    }

    /// @dev Reverts if the caller doesn't have the guardian role.
    modifier onlyGuardianRole() {
        address msgSender = _msgSenderOnlyEVCAccountOwner();
        if (msgSender != owner() && msgSender != guardian) revert ErrorsLib.NotGuardianRole();

        _;
    }

    /// @dev Reverts if the caller doesn't have the curator nor the guardian role.
    modifier onlyCuratorOrGuardianRole() {
        address msgSender = _msgSenderOnlyEVCAccountOwner();
        if (msgSender != guardian && msgSender != curator && msgSender != owner()) {
            revert ErrorsLib.NotCuratorNorGuardianRole();
        }

        _;
    }

    /// @dev Makes sure conditions are met to accept a pending value.
    /// @dev Reverts if:
    /// - there's no pending value;
    /// - the timelock has not elapsed since the pending value has been submitted.
    modifier afterTimelock(uint256 validAt) {
        if (validAt == 0) revert ErrorsLib.NoPendingValue();
        if (block.timestamp < validAt) revert ErrorsLib.TimelockNotElapsed();

        _;
    }

    /* ONLY OWNER FUNCTIONS */

    /// @inheritdoc IEulerEarnBase
    function setName(string memory newName) external onlyOwner {
        _name = newName;

        emit EventsLib.SetName(newName);
    }

    /// @inheritdoc IEulerEarnBase
    function setSymbol(string memory newSymbol) external onlyOwner {
        _symbol = newSymbol;

        emit EventsLib.SetSymbol(newSymbol);
    }

    /// @inheritdoc IEulerEarnBase
    function setCurator(address newCurator) external onlyOwner {
        if (newCurator == curator) revert ErrorsLib.AlreadySet();

        curator = newCurator;

        emit EventsLib.SetCurator(newCurator);
    }

    /// @inheritdoc IEulerEarnBase
    function setIsAllocator(address newAllocator, bool newIsAllocator) external onlyOwner {
        if (isAllocator[newAllocator] == newIsAllocator) revert ErrorsLib.AlreadySet();

        isAllocator[newAllocator] = newIsAllocator;

        emit EventsLib.SetIsAllocator(newAllocator, newIsAllocator);
    }

    /// @inheritdoc IEulerEarnBase
    function submitTimelock(uint256 newTimelock) external onlyOwner {
        if (newTimelock == timelock) revert ErrorsLib.AlreadySet();
        if (pendingTimelock.validAt != 0) revert ErrorsLib.AlreadyPending();
        _checkTimelockBounds(newTimelock);

        if (newTimelock > timelock) {
            _setTimelock(newTimelock);
        } else {
            // Safe "unchecked" cast because newTimelock <= MAX_TIMELOCK.
            pendingTimelock.update(uint136(newTimelock), timelock);

            emit EventsLib.SubmitTimelock(newTimelock);
        }
    }

    /// @inheritdoc IEulerEarnBase
    function setFee(uint256 newFee) external nonReentrant onlyOwner {
        if (newFee == fee) revert ErrorsLib.AlreadySet();
        if (newFee > ConstantsLib.MAX_FEE) revert ErrorsLib.MaxFeeExceeded();
        if (newFee != 0 && feeRecipient == address(0)) revert ErrorsLib.ZeroFeeRecipient();

        // Accrue interest and fee using the previous fee set before changing it.
        _accrueInterest();

        // Safe "unchecked" cast because newFee <= MAX_FEE.
        fee = uint96(newFee);

        emit EventsLib.SetFee(_msgSender(), fee);
    }

    /// @inheritdoc IEulerEarnBase
    function setFeeRecipient(address newFeeRecipient) external nonReentrant onlyOwner {
        if (newFeeRecipient == feeRecipient) revert ErrorsLib.AlreadySet();
        if (newFeeRecipient == address(0) && fee != 0) revert ErrorsLib.ZeroFeeRecipient();

        // Accrue interest and fee to the previous fee recipient set before changing it.
        _accrueInterest();

        feeRecipient = newFeeRecipient;

        emit EventsLib.SetFeeRecipient(newFeeRecipient);
    }

    /// @inheritdoc IEulerEarnBase
    function submitGuardian(address newGuardian) external onlyOwner {
        if (newGuardian == guardian) revert ErrorsLib.AlreadySet();
        if (pendingGuardian.validAt != 0) revert ErrorsLib.AlreadyPending();

        if (guardian == address(0)) {
            _setGuardian(newGuardian);
        } else {
            pendingGuardian.update(newGuardian, timelock);

            emit EventsLib.SubmitGuardian(newGuardian);
        }
    }

    /* ONLY CURATOR FUNCTIONS */

    /// @inheritdoc IEulerEarnBase
    function submitCap(IERC4626 id, uint256 newSupplyCap) external nonReentrant onlyCuratorRole {
        if (id.asset() != asset()) revert ErrorsLib.InconsistentAsset(id);
        if (pendingCap[id].validAt != 0) revert ErrorsLib.AlreadyPending();
        if (config[id].removableAt != 0) revert ErrorsLib.PendingRemoval();

        // For the sake of backwards compatibility, the max allowed cap can either be set to type(uint184).max or type(uint136).max.
        newSupplyCap = newSupplyCap == type(uint184).max ? type(uint136).max : newSupplyCap;

        uint256 supplyCap = config[id].cap;
        if (newSupplyCap == supplyCap) revert ErrorsLib.AlreadySet();

        if (newSupplyCap < supplyCap) {
            _setCap(id, newSupplyCap.toUint136());
        } else {
            if (!IEulerEarnFactory(creator).isStrategyAllowed(address(id))) revert ErrorsLib.UnauthorizedMarket(id);

            pendingCap[id].update(newSupplyCap.toUint136(), timelock);

            emit EventsLib.SubmitCap(_msgSender(), id, newSupplyCap);
        }
    }

    /// @inheritdoc IEulerEarnBase
    function submitMarketRemoval(IERC4626 id) external onlyCuratorRole {
        if (config[id].removableAt != 0) revert ErrorsLib.AlreadyPending();
        if (config[id].cap != 0) revert ErrorsLib.NonZeroCap();
        if (!config[id].enabled) revert ErrorsLib.MarketNotEnabled(id);
        if (pendingCap[id].validAt != 0) revert ErrorsLib.PendingCap(id);

        // Safe "unchecked" cast because timelock <= MAX_TIMELOCK.
        config[id].removableAt = uint64(block.timestamp + timelock);

        emit EventsLib.SubmitMarketRemoval(_msgSender(), id);
    }

    /* ONLY ALLOCATOR FUNCTIONS */

    /// @inheritdoc IEulerEarnBase
    function setSupplyQueue(IERC4626[] calldata newSupplyQueue) external onlyAllocatorRole {
        uint256 length = newSupplyQueue.length;

        if (length > ConstantsLib.MAX_QUEUE_LENGTH) revert ErrorsLib.MaxQueueLengthExceeded();

        for (uint256 i; i < length; ++i) {
            if (config[newSupplyQueue[i]].cap == 0) revert ErrorsLib.UnauthorizedMarket(newSupplyQueue[i]);
        }

        supplyQueue = newSupplyQueue;

        emit EventsLib.SetSupplyQueue(_msgSender(), newSupplyQueue);
    }

    /// @inheritdoc IEulerEarnBase
    function updateWithdrawQueue(uint256[] calldata indexes) external onlyAllocatorRole {
        uint256 newLength = indexes.length;
        uint256 currLength = withdrawQueue.length;

        bool[] memory seen = new bool[](currLength);
        IERC4626[] memory newWithdrawQueue = new IERC4626[](newLength);

        for (uint256 i; i < newLength; ++i) {
            uint256 prevIndex = indexes[i];

            // If prevIndex >= currLength, it will revert with native "Index out of bounds".
            IERC4626 id = withdrawQueue[prevIndex];
            if (seen[prevIndex]) revert ErrorsLib.DuplicateMarket(id);
            seen[prevIndex] = true;

            newWithdrawQueue[i] = id;
        }

        for (uint256 i; i < currLength; ++i) {
            if (!seen[i]) {
                IERC4626 id = withdrawQueue[i];

                if (config[id].cap != 0) revert ErrorsLib.InvalidMarketRemovalNonZeroCap(id);
                if (pendingCap[id].validAt != 0) revert ErrorsLib.PendingCap(id);

                if (expectedSupplyAssets(id) != 0) {
                    if (config[id].removableAt == 0) revert ErrorsLib.InvalidMarketRemovalNonZeroSupply(id);

                    if (block.timestamp < config[id].removableAt) {
                        revert ErrorsLib.InvalidMarketRemovalTimelockNotElapsed(id);
                    }
                }

                delete config[id];
            }
        }

        withdrawQueue = newWithdrawQueue;

        emit EventsLib.SetWithdrawQueue(_msgSender(), newWithdrawQueue);
    }

    /// @inheritdoc IEulerEarnBase
    function reallocate(MarketAllocation[] calldata allocations) external nonReentrant onlyAllocatorRole {
        address msgSender = _msgSender();
        uint256 totalSupplied;
        uint256 totalWithdrawn;
        for (uint256 i; i < allocations.length; ++i) {
            MarketAllocation memory allocation = allocations[i];
            IERC4626 id = allocation.id;
            if (!config[id].enabled) revert ErrorsLib.MarketNotEnabled(id);

            uint256 supplyShares = config[id].balance;
            uint256 supplyAssets = id.previewRedeem(supplyShares);
            uint256 withdrawn = supplyAssets.zeroFloorSub(allocation.assets);

            if (withdrawn > 0) {
                // Guarantees that unknown frontrunning donations can be withdrawn, in order to disable a market.
                uint256 shares;
                if (allocation.assets == 0) {
                    shares = supplyShares;
                    withdrawn = 0;
                }

                uint256 withdrawnAssets;
                uint256 withdrawnShares;

                if (shares == 0) {
                    withdrawnAssets = withdrawn;
                    withdrawnShares = id.withdraw(withdrawn, address(this), address(this));
                } else {
                    withdrawnAssets = id.redeem(shares, address(this), address(this));
                    withdrawnShares = shares;
                }

                config[id].balance = uint112(supplyShares - withdrawnShares);

                emit EventsLib.ReallocateWithdraw(msgSender, id, withdrawnAssets, withdrawnShares);

                totalWithdrawn += withdrawnAssets;
            } else {
                uint256 suppliedAssets = allocation.assets == type(uint256).max
                    ? totalWithdrawn.zeroFloorSub(totalSupplied)
                    : allocation.assets.zeroFloorSub(supplyAssets);

                if (suppliedAssets == 0) continue;

                uint256 supplyCap = config[id].cap;
                if (supplyAssets + suppliedAssets > supplyCap) revert ErrorsLib.SupplyCapExceeded(id);

                // The vaults's underlying asset is guaranteed to be the vault's asset because it has a non-zero supply cap.
                uint256 suppliedShares = id.deposit(suppliedAssets, address(this));

                config[id].balance = (supplyShares + suppliedShares).toUint112();

                emit EventsLib.ReallocateSupply(msgSender, id, suppliedAssets, suppliedShares);

                totalSupplied += suppliedAssets;
            }
        }

        if (totalWithdrawn != totalSupplied) revert ErrorsLib.InconsistentReallocation();
    }

    /* REVOKE FUNCTIONS */

    /// @inheritdoc IEulerEarnBase
    function revokePendingTimelock() external onlyGuardianRole {
        delete pendingTimelock;

        emit EventsLib.RevokePendingTimelock(_msgSender());
    }

    /// @inheritdoc IEulerEarnBase
    function revokePendingGuardian() external onlyGuardianRole {
        delete pendingGuardian;

        emit EventsLib.RevokePendingGuardian(_msgSender());
    }

    /// @inheritdoc IEulerEarnBase
    function revokePendingCap(IERC4626 id) external onlyCuratorOrGuardianRole {
        delete pendingCap[id];

        emit EventsLib.RevokePendingCap(_msgSender(), id);
    }

    /// @inheritdoc IEulerEarnBase
    function revokePendingMarketRemoval(IERC4626 id) external onlyCuratorOrGuardianRole {
        delete config[id].removableAt;

        emit EventsLib.RevokePendingMarketRemoval(_msgSender(), id);
    }

    /* EXTERNAL */

    /// @inheritdoc IEulerEarnBase
    function supplyQueueLength() external view returns (uint256) {
        return supplyQueue.length;
    }

    /// @inheritdoc IEulerEarnBase
    function withdrawQueueLength() external view returns (uint256) {
        return withdrawQueue.length;
    }

    /// @inheritdoc IEulerEarnBase
    function maxWithdrawFromStrategy(IERC4626 id) public view returns (uint256) {
        return UtilsLib.min(id.maxWithdraw(address(this)), expectedSupplyAssets(id));
    }

    /// @inheritdoc IEulerEarnBase
    function expectedSupplyAssets(IERC4626 id) public view returns (uint256) {
        return id.previewRedeem(config[id].balance);
    }

    /// @inheritdoc IEulerEarnBase
    function acceptTimelock() external afterTimelock(pendingTimelock.validAt) {
        _setTimelock(pendingTimelock.value);
    }

    /// @inheritdoc IEulerEarnBase
    function acceptGuardian() external afterTimelock(pendingGuardian.validAt) {
        _setGuardian(pendingGuardian.value);
    }

    /// @inheritdoc IEulerEarnBase
    function acceptCap(IERC4626 id) external afterTimelock(pendingCap[id].validAt) {
        if (!IEulerEarnFactory(creator).isStrategyAllowed(address(id))) revert ErrorsLib.UnauthorizedMarket(id);

        // Safe "unchecked" cast because pendingCap <= type(uint136).max.
        _setCap(id, uint136(pendingCap[id].value));
    }

    /* ERC4626 (PUBLIC) */

    /// @inheritdoc IERC20Metadata
    function name() public view override(IERC20Metadata, ERC20) returns (string memory) {
        return _name;
    }

    /// @inheritdoc IERC20Metadata
    function symbol() public view override(IERC20Metadata, ERC20) returns (string memory) {
        return _symbol;
    }

    /// @inheritdoc IERC4626
    /// @dev Warning: May be higher than the actual max deposit due to duplicate vaults in the supplyQueue.
    /// @dev If deposit would throw ZeroShares error, function returns 0.
    function maxDeposit(address) public view override returns (uint256) {
        uint256 suppliable = _maxDeposit();

        return _convertToShares(suppliable, Math.Rounding.Floor) == 0 ? 0 : suppliable;
    }

    /// @inheritdoc IERC4626
    /// @dev Warning: May be higher than the actual max mint due to duplicate vaults in the supplyQueue.
    function maxMint(address) public view override returns (uint256) {
        uint256 suppliable = _maxDeposit();

        return _convertToShares(suppliable, Math.Rounding.Floor);
    }

    /// @inheritdoc IERC4626
    /// @dev Warning: May be lower than the actual amount of assets that can be withdrawn by `owner` due to conversion
    /// roundings between shares and assets.
    function maxWithdraw(address owner) public view override returns (uint256 assets) {
        (assets,,) = _maxWithdraw(owner);
    }

    /// @inheritdoc IERC4626
    /// @dev Warning: May be lower than the actual amount of shares that can be redeemed by `owner` due to conversion
    /// roundings between shares and assets.
    function maxRedeem(address owner) public view override returns (uint256) {
        (uint256 assets, uint256 newTotalSupply, uint256 newTotalAssets) = _maxWithdraw(owner);

        return _convertToSharesWithTotals(assets, newTotalSupply, newTotalAssets, Math.Rounding.Floor);
    }

    /// @inheritdoc IERC4626
    function deposit(uint256 assets, address receiver) public override nonReentrant returns (uint256 shares) {
        _accrueInterest();

        shares = _convertToSharesWithTotals(assets, totalSupply(), lastTotalAssets, Math.Rounding.Floor);

        if (shares == 0) revert ErrorsLib.ZeroShares();

        _deposit(_msgSender(), receiver, assets, shares);
    }

    /// @inheritdoc IERC4626
    function mint(uint256 shares, address receiver) public override nonReentrant returns (uint256 assets) {
        _accrueInterest();

        assets = _convertToAssetsWithTotals(shares, totalSupply(), lastTotalAssets, Math.Rounding.Ceil);

        _deposit(_msgSender(), receiver, assets, shares);
    }

    /// @inheritdoc IERC4626
    function withdraw(uint256 assets, address receiver, address owner)
        public
        override
        nonReentrant
        returns (uint256 shares)
    {
        _accrueInterest();

        // Do not call expensive `maxWithdraw` and optimistically withdraw assets.

        shares = _convertToSharesWithTotals(assets, totalSupply(), lastTotalAssets, Math.Rounding.Ceil);

        _withdraw(_msgSender(), receiver, owner, assets, shares);
    }

    /// @inheritdoc IERC4626
    function redeem(uint256 shares, address receiver, address owner)
        public
        override
        nonReentrant
        returns (uint256 assets)
    {
        _accrueInterest();

        // Do not call expensive `maxRedeem` and optimistically redeem shares.

        assets = _convertToAssetsWithTotals(shares, totalSupply(), lastTotalAssets, Math.Rounding.Floor);

        // Since losses are not realized, exchange rate is never < 1 and zero assets check is not needed.

        _withdraw(_msgSender(), receiver, owner, assets, shares);
    }

    /// @inheritdoc IERC4626
    /// @dev totalAssets is the sum of the vault's assets on the strategy vaults plus the lost assets (see corresponding
    /// docs in IEulerEarn.sol).
    function totalAssets() public view override returns (uint256) {
        (, uint256 newTotalAssets,) = _accruedFeeAndAssets();

        return newTotalAssets;
    }

    /* ERC4626 (INTERNAL) */

    /// @dev Returns the maximum amount of asset (`assets`) that the `owner` can withdraw from the vault, as well as the
    /// new vault's total supply (`newTotalSupply`) and total assets (`newTotalAssets`).
    function _maxWithdraw(address owner)
        internal
        view
        returns (uint256 assets, uint256 newTotalSupply, uint256 newTotalAssets)
    {
        uint256 feeShares;
        (feeShares, newTotalAssets,) = _accruedFeeAndAssets();
        newTotalSupply = totalSupply() + feeShares;

        assets = _convertToAssetsWithTotals(balanceOf(owner), newTotalSupply, newTotalAssets, Math.Rounding.Floor);
        assets -= _simulateWithdrawStrategy(assets);
    }

    /// @dev Returns the maximum amount of assets that the Earn vault can supply to the strategy vaults.
    function _maxDeposit() internal view returns (uint256 totalSuppliable) {
        for (uint256 i; i < supplyQueue.length; ++i) {
            IERC4626 id = supplyQueue[i];

            uint256 supplyCap = config[id].cap;
            if (supplyCap == 0) continue;

            uint256 supplyAssets = expectedSupplyAssets(id);

            totalSuppliable += UtilsLib.min(supplyCap.zeroFloorSub(supplyAssets), id.maxDeposit(address(this)));
        }
    }

    /// @inheritdoc ERC4626
    /// @dev The accrual of performance fees is taken into account in the conversion.
    function _convertToShares(uint256 assets, Math.Rounding rounding) internal view override returns (uint256) {
        (uint256 feeShares, uint256 newTotalAssets,) = _accruedFeeAndAssets();

        return _convertToSharesWithTotals(assets, totalSupply() + feeShares, newTotalAssets, rounding);
    }

    /// @inheritdoc ERC4626
    /// @dev The accrual of performance fees is taken into account in the conversion.
    function _convertToAssets(uint256 shares, Math.Rounding rounding) internal view override returns (uint256) {
        (uint256 feeShares, uint256 newTotalAssets,) = _accruedFeeAndAssets();

        return _convertToAssetsWithTotals(shares, totalSupply() + feeShares, newTotalAssets, rounding);
    }

    /// @dev Returns the amount of shares that the vault would exchange for the amount of `assets` provided.
    /// @dev It assumes that the arguments `newTotalSupply` and `newTotalAssets` are up to date.
    function _convertToSharesWithTotals(
        uint256 assets,
        uint256 newTotalSupply,
        uint256 newTotalAssets,
        Math.Rounding rounding
    ) internal pure returns (uint256) {
        return assets.mulDiv(
            newTotalSupply + ConstantsLib.VIRTUAL_AMOUNT, newTotalAssets + ConstantsLib.VIRTUAL_AMOUNT, rounding
        );
    }

    /// @dev Returns the amount of assets that the vault would exchange for the amount of `shares` provided.
    /// @dev It assumes that the arguments `newTotalSupply` and `newTotalAssets` are up to date.
    function _convertToAssetsWithTotals(
        uint256 shares,
        uint256 newTotalSupply,
        uint256 newTotalAssets,
        Math.Rounding rounding
    ) internal pure returns (uint256) {
        return shares.mulDiv(
            newTotalAssets + ConstantsLib.VIRTUAL_AMOUNT, newTotalSupply + ConstantsLib.VIRTUAL_AMOUNT, rounding
        );
    }

    /// @inheritdoc ERC4626
    /// @dev Used in mint or deposit to deposit the underlying asset to strategy vaults.
    function _deposit(address caller, address receiver, uint256 assets, uint256 shares) internal override {
        IERC20(asset()).safeTransferFromWithPermit2(caller, address(this), assets, permit2Address);
        _mint(receiver, shares);

        emit Deposit(caller, receiver, assets, shares);

        _supplyStrategy(assets);

        // `lastTotalAssets + assets` may be a little above `totalAssets()`.
        // This can lead to a small accrual of `lostAssets` at the next interaction.
        _updateLastTotalAssets(lastTotalAssets + assets);
    }

    /// @inheritdoc ERC4626
    /// @dev Used in redeem or withdraw to withdraw the underlying asset from the strategy vaults.
    /// @dev Depending on 3 cases, reverts when withdrawing "too much" with:
    /// 1. NotEnoughLiquidity when withdrawing more than available liquidity.
    /// 2. ERC20InsufficientAllowance when withdrawing more than `caller`'s allowance.
    /// 3. ERC20InsufficientBalance when withdrawing more than `owner`'s balance.
    /// @dev The function prevents sending assets to addresses which are known to be EVC sub-accounts
    function _withdraw(address caller, address receiver, address owner, uint256 assets, uint256 shares)
        internal
        override
    {
        // assets sent to EVC sub-accounts would be lost, as the private key for a sub-account is not known
        address evcOwner = evc.getAccountOwner(receiver);
        if (evcOwner != address(0) && evcOwner != receiver) {
            revert ErrorsLib.BadAssetReceiver();
        }

        // `lastTotalAssets - assets` may be a little above `totalAssets()`.
        // This can lead to a small accrual of `lostAssets` at the next interaction.
        // clamp at 0 so the error raised is the more informative NotEnoughLiquidity.
        _updateLastTotalAssets(lastTotalAssets.zeroFloorSub(assets));

        _withdrawStrategy(assets);

        super._withdraw(caller, receiver, owner, assets, shares);
    }

    /* INTERNAL */

    /// @notice Retrieves the message sender in the context of the EVC.
    /// @dev This function returns the account on behalf of which the current operation is being performed, which is
    /// either msg.sender or the account authenticated by the EVC.
    /// @return The address of the message sender.
    function _msgSender() internal view virtual override(EVCUtil, Context) returns (address) {
        return EVCUtil._msgSender();
    }

    /// @dev Reverts if `newTimelock` is not within the bounds.
    function _checkTimelockBounds(uint256 newTimelock) internal pure {
        if (newTimelock > ConstantsLib.MAX_TIMELOCK) revert ErrorsLib.AboveMaxTimelock();
        if (newTimelock < ConstantsLib.POST_INITIALIZATION_MIN_TIMELOCK) revert ErrorsLib.BelowMinTimelock();
    }

    /// @dev Sets `timelock` to `newTimelock`.
    function _setTimelock(uint256 newTimelock) internal {
        timelock = newTimelock;

        emit EventsLib.SetTimelock(_msgSender(), newTimelock);

        delete pendingTimelock;
    }

    /// @dev Sets `guardian` to `newGuardian`.
    function _setGuardian(address newGuardian) internal {
        guardian = newGuardian;

        emit EventsLib.SetGuardian(_msgSender(), newGuardian);

        delete pendingGuardian;
    }

    /// @dev Sets the cap of the vault to `supplyCap`.
    function _setCap(IERC4626 id, uint136 supplyCap) internal {
        address msgSender = _msgSender();
        MarketConfig storage marketConfig = config[id];

        (bool success, bytes memory result) = address(id).staticcall(abi.encodeCall(this.permit2Address, ()));
        address permit2 = success && result.length >= 32 ? abi.decode(result, (address)) : address(0);

        if (supplyCap > 0) {
            IERC20(asset()).forceApproveMaxWithPermit2(address(id), permit2);

            if (!marketConfig.enabled) {
                withdrawQueue.push(id);

                if (withdrawQueue.length > ConstantsLib.MAX_QUEUE_LENGTH) revert ErrorsLib.MaxQueueLengthExceeded();

                marketConfig.enabled = true;
                marketConfig.balance = id.balanceOf(address(this)).toUint112();

                // Take into account assets of the new vault without applying a fee.
                _updateLastTotalAssets(lastTotalAssets + expectedSupplyAssets(id));

                emit EventsLib.SetWithdrawQueue(msgSender, withdrawQueue);
            }

            marketConfig.removableAt = 0;
        } else {
            IERC20(asset()).revokeApprovalWithPermit2(address(id), permit2);
        }

        marketConfig.cap = supplyCap;

        emit EventsLib.SetCap(msgSender, id, supplyCap);

        delete pendingCap[id];
    }

    /* LIQUIDITY ALLOCATION */

    /// @dev Supplies `assets` to the strategy vaults.
    function _supplyStrategy(uint256 assets) internal {
        for (uint256 i; i < supplyQueue.length; ++i) {
            IERC4626 id = supplyQueue[i];

            uint256 supplyCap = config[id].cap;
            if (supplyCap == 0) continue;

            uint256 supplyAssets = expectedSupplyAssets(id);

            uint256 toSupply =
                UtilsLib.min(UtilsLib.min(supplyCap.zeroFloorSub(supplyAssets), id.maxDeposit(address(this))), assets);

            if (toSupply > 0) {
                // Using try/catch to skip vaults that revert.
                try id.deposit(toSupply, address(this)) returns (uint256 suppliedShares) {
                    config[id].balance = (config[id].balance + suppliedShares).toUint112();
                    assets -= toSupply;
                } catch {}
            }

            if (assets == 0) return;
        }

        if (assets != 0) revert ErrorsLib.AllCapsReached();
    }

    /// @dev Withdraws `assets` from the strategy vaults.
    function _withdrawStrategy(uint256 assets) internal {
        for (uint256 i; i < withdrawQueue.length; ++i) {
            IERC4626 id = withdrawQueue[i];

            uint256 toWithdraw = UtilsLib.min(maxWithdrawFromStrategy(id), assets);

            if (toWithdraw > 0) {
                // Using try/catch to skip vaults that revert.
                try id.withdraw(toWithdraw, address(this), address(this)) returns (uint256 withdrawnShares) {
                    config[id].balance = uint112(config[id].balance - withdrawnShares);
                    assets -= toWithdraw;
                } catch {}
            }

            if (assets == 0) return;
        }

        if (assets != 0) revert ErrorsLib.NotEnoughLiquidity();
    }

    /// @dev Simulates a withdraw of `assets` from the strategy vaults.
    /// @return The remaining assets to be withdrawn.
    function _simulateWithdrawStrategy(uint256 assets) internal view returns (uint256) {
        for (uint256 i; i < withdrawQueue.length; ++i) {
            IERC4626 id = withdrawQueue[i];

            assets = assets.zeroFloorSub(maxWithdrawFromStrategy(id));

            if (assets == 0) break;
        }

        return assets;
    }

    /* FEE MANAGEMENT */

    /// @dev Updates `lastTotalAssets` to `updatedTotalAssets`.
    function _updateLastTotalAssets(uint256 updatedTotalAssets) internal {
        lastTotalAssets = updatedTotalAssets;

        emit EventsLib.UpdateLastTotalAssets(updatedTotalAssets);
    }

    /// @dev Accrues `lastTotalAssets`, `lostAssets` and mints the fee shares to the fee recipient.
    function _accrueInterest() internal {
        (uint256 feeShares, uint256 newTotalAssets, uint256 newLostAssets) = _accruedFeeAndAssets();

        _updateLastTotalAssets(newTotalAssets);
        lostAssets = newLostAssets;
        emit EventsLib.UpdateLostAssets(newLostAssets);

        if (feeShares != 0) _mint(feeRecipient, feeShares);

        emit EventsLib.AccrueInterest(newTotalAssets, feeShares);
    }

    /// @dev Computes and returns the `feeShares` to mint, the new `totalAssets` and the new `lostAssets`.
    /// @return feeShares the shares to mint to `feeRecipient`.
    /// @return newTotalAssets the new `totalAssets`.
    /// @return newLostAssets the new lostAssets.
    function _accruedFeeAndAssets()
        internal
        view
        returns (uint256 feeShares, uint256 newTotalAssets, uint256 newLostAssets)
    {
        // The assets that the Earn vault has on the strategy vaults.
        uint256 realTotalAssets;
        for (uint256 i; i < withdrawQueue.length; ++i) {
            IERC4626 id = withdrawQueue[i];
            realTotalAssets += expectedSupplyAssets(id);
        }

        uint256 lastTotalAssetsCached = lastTotalAssets;
        if (realTotalAssets < lastTotalAssetsCached - lostAssets) {
            // If the vault lost some assets (realTotalAssets decreased), lostAssets is increased.
            newLostAssets = lastTotalAssetsCached - realTotalAssets;
        } else {
            // If it did not, lostAssets stays the same.
            newLostAssets = lostAssets;
        }

        newTotalAssets = realTotalAssets + newLostAssets;
        uint256 totalInterest = newTotalAssets - lastTotalAssetsCached;
        if (totalInterest != 0 && fee != 0) {
            // It is acknowledged that `feeAssets` may be rounded down to 0 if `totalInterest * fee < WAD`.
            uint256 feeAssets = totalInterest.mulDiv(fee, WAD);
            // The fee assets is subtracted from the total assets in this calculation to compensate for the fact
            // that total assets is already increased by the total interest (including the fee assets).
            feeShares =
                _convertToSharesWithTotals(feeAssets, totalSupply(), newTotalAssets - feeAssets, Math.Rounding.Floor);
        }
    }
}
