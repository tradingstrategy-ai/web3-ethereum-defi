// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.18;

import "./../lib/types/VaultTypes.sol";
import "./../lib/types/RebalanceTypes.sol";

interface IVault {
    error OnlyCrossChainManagerCanCall();
    error AccountIdInvalid();
    error TokenNotAllowed();
    error BrokerNotAllowed();
    error BalanceNotEnough(uint256 balance, uint128 amount);
    error AddressZero();
    error EnumerableSetError();
    error ZeroDepositFee();
    error ZeroDeposit();
    error ZeroCodeLength();
    error NotZeroCodeLength();
    error DepositExceedLimit();
    error NotImplemented();
    error ProtocolVaultAddressMismatch(address want, address got);

    // @deprecated
    event AccountDeposit(
        bytes32 indexed accountId,
        address indexed userAddress,
        uint64 indexed depositNonce,
        bytes32 tokenHash,
        uint128 tokenAmount
    );

    event AccountDepositTo(
        bytes32 indexed accountId,
        address indexed userAddress,
        uint64 indexed depositNonce,
        bytes32 tokenHash,
        uint128 tokenAmount
    );

    event AccountWithdraw(
        bytes32 indexed accountId,
        uint64 indexed withdrawNonce,
        bytes32 brokerHash,
        address sender,
        address receiver,
        bytes32 tokenHash,
        uint128 tokenAmount,
        uint128 fee
    );

    event AccountDelegate(
        address indexed delegateContract,
        bytes32 indexed brokerHash,
        address indexed delegateSigner,
        uint256 chainId,
        uint256 blockNumber
    );

    event SetAllowedToken(bytes32 indexed _tokenHash, bool _allowed);
    event SetAllowedBroker(bytes32 indexed _brokerHash, bool _allowed);
    event ChangeTokenAddressAndAllow(bytes32 indexed _tokenHash, address _tokenAddress);
    event ChangeCrossChainManager(address oldAddress, address newAddress);
    event ChangeDepositLimit(address indexed _tokenAddress, uint256 _limit);
    event WithdrawFailed(address indexed token, address indexed receiver, uint256 amount);

    function initialize() external;

    function deposit(VaultTypes.VaultDepositFE calldata data) external payable;
    function depositTo(address receiver, VaultTypes.VaultDepositFE calldata data) external payable;
    function getDepositFee(address recevier, VaultTypes.VaultDepositFE calldata data) external view returns (uint256);
    function enableDepositFee(bool _enabled) external;
    function withdraw(VaultTypes.VaultWithdraw calldata data) external;
    function delegateSigner(VaultTypes.VaultDelegate calldata data) external;
    function withdraw2Contract(VaultTypes.VaultWithdraw2Contract calldata data) external;

    // CCTP: functions for receive rebalance msg
    function rebalanceMint(RebalanceTypes.RebalanceMintCCData calldata data) external;
    function rebalanceBurn(RebalanceTypes.RebalanceBurnCCData calldata data) external;
    function setTokenMessengerContract(address _tokenMessengerContract) external;
    function setRebalanceMessengerContract(address _rebalanceMessengerContract) external;

    // admin call
    function setCrossChainManager(address _crossChainManagerAddress) external;
    function setDepositLimit(address _tokenAddress, uint256 _limit) external;
    function setProtocolVaultAddress(address _protocolVaultAddress) external;
    function emergencyPause() external;
    function emergencyUnpause() external;

    // whitelist
    function setAllowedToken(bytes32 _tokenHash, bool _allowed) external;
    function setAllowedBroker(bytes32 _brokerHash, bool _allowed) external;
    function changeTokenAddressAndAllow(bytes32 _tokenHash, address _tokenAddress) external;
    function getAllowedToken(bytes32 _tokenHash) external view returns (address);
    function getAllowedBroker(bytes32 _brokerHash) external view returns (bool);
    function getAllAllowedToken() external view returns (bytes32[] memory);
    function getAllAllowedBroker() external view returns (bytes32[] memory);
}
