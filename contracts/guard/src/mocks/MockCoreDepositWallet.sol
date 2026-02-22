// Mock CoreDepositWallet for testing Hypercore vault guard integration.
//
// Deployed at the CoreDepositWallet address via anvil_setCode
// to simulate USDC bridging from HyperEVM to HyperCore in Anvil fork tests.
//
// Records all deposit calls for test assertions.

pragma solidity ^0.8.0;

import {IERC20} from "../lib/IERC20.sol";

contract MockCoreDepositWallet {

    struct RecordedDeposit {
        address sender;
        uint256 amount;
        uint32 destinationDex;
    }

    RecordedDeposit[] public deposits;

    event Deposit(address indexed sender, uint256 amount, uint32 destinationDex);
    event DepositFor(address indexed sender, address indexed recipient, uint256 amount, uint32 destinationDex);

    function deposit(uint256 amount, uint32 destinationDex) external {
        deposits.push(RecordedDeposit({
            sender: msg.sender,
            amount: amount,
            destinationDex: destinationDex
        }));
        emit Deposit(msg.sender, amount, destinationDex);
    }

    function depositFor(address recipient, uint256 amount, uint32 destinationDex) external {
        deposits.push(RecordedDeposit({
            sender: msg.sender,
            amount: amount,
            destinationDex: destinationDex
        }));
        emit DepositFor(msg.sender, recipient, amount, destinationDex);
    }

    function getDepositCount() external view returns (uint256) {
        return deposits.length;
    }

    function getDeposit(uint256 index) external view returns (
        address sender,
        uint256 amount,
        uint32 destinationDex
    ) {
        RecordedDeposit storage d = deposits[index];
        return (d.sender, d.amount, d.destinationDex);
    }
}
