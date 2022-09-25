# About Aave v3

This README explains how Aave v3 interest calculation works and how we use the ReserveDataUpdated
events to track interest rates.

## Aave v3 blockchain events

The ReserveDataUpdated event in [ReserveLogic.sol](https://github.com/aave/aave-v3-core/blob/v1.16.2/contracts/protocol/libraries/logic/ReserveLogic.sol#L31)
(signature 804c9b842b2748a22bb64b345453a3de7ca54a6ca45ce00d415894979e22897a) is triggered whenever the interest rates of a reserve currency are updated.

The event contains the following attributes:

* address indexed reserve - The currency reserve (AToken address) whose rates were updated.
* uint256 liquidityRate - Deposit interest rate.
* uint256 stableBorrowRate - Stable debt interest rate.
* uint256 variableBorrowRate - Variable debt interest rate.
* uint256 liquidityIndex - Deposit interest index (to calculate accrued interest between start and end time).
* uint256 variableBorrowIndex - Variable debt interest index (to calculate accrued interest between start and end time).

Note that Aave v2 triggers events with the same signature. We need to be careful to check the contract address that triggered the event.

Event-related constants are defined in constants.py.

## Interest rate calculation

The basic interest rates can be read directly from the liquidityRate, stableBorrowRate and variableBorrowRate attributes.

The attributes are 256-bit unsigned integers multiplied with RAY (10^27). To get the decimal values, we convert the (very big)
values first to Python Decimal objects and divide by 10^27. To get percent values, we multiply those with 100%.

Rate calculations are implemented in rates.py.

## APR and APY calculation

Aave v3 has its own definition for APR and APY found in https://docs.aave.com/developers/v/2.0/guides/apy-and-apr.

To get APR and APY values, we apply the formulas to liquidityRate, stableBorrowRate and variableBorrowRate. This
is implemented in rates.py.

## Accured interest calculation

To calculate accrued interest for a deposit made at T1 and withdrawn at T2, we need the current liquidity index at both times:

    relative_amount = deposit_amount / liquidity_index(T1)
    withdraw_amount = relative_amount * liquidity_index(T2)
    accrued_interest = withdraw_amount - relative_amount

To calculate accrued interest for a variable interest loan taken at T1 and paid back at T2, we need the current variable borrow index at both times:

    relative_amount = debt_amount / variable_borrow_index(T1)
    payback_amount = relative_amount * variable_borrow_index(T2)
    accrued_interest = payback_amount - relative_amount

To calculate accrued interest for a stable interest loan, we need a slightly more complicated formula defined in
https://github.com/aave/aave-v3-core/blob/v1.16.2/contracts/protocol/libraries/math/MathUtils.sol#L51. The formula is adapted
from Solidity to Python.

All accrued interest calculations are implemented in rates.py.

## Notes about Aave v3 tokens and contracts

Aave v3 uses four types of token contracts:
* Token contract - The actual token (e.g. DAI)
* AToken contract - Aave contract for deposited currency (e.g. aPolDAI)
* VariableDebtToken contract - Aave contract for variable-rate debt (e.g. vPolDAI)
* StableDebtToken contract - Aave contract for stable-rate debt (e.g. sPolDAI)

Aave v3 calculates balance interests algorithmically. Deposit balances are represented by ATokens. Debt balances are represented by VariableDebtTokens and StableDebtTokens. There is a separate AToken, VariableDebtToken and StableDebtToken for each currency reserve.

When you deposit currency, ATokens are minted. Aave V3 stores the balance as a scaled value based on the current liquidity index of the reserve. For instance, if 100 tokens are minted and the current liquidity index is 2, the scaled balance stored in the blockchain is 50.

When you call AToken.balanceOf(), the function multiplies the scaled balance by the current liquidity index. For instance, if the scaled balance stored in the blockchain is 50, and the current liquidity index is 2, the calculated balance is 100. If the liquidity index increases by 10% to 2.2, the calculated balance is 110.

Variable debt tokens work in the same way as ATokens, using the variable borrow index. Stable debt tokens have a more complicated algorithm for calculating the accrued interest in their balanceOf() method.

Normally the liquidity index and variable borrow index of each currency reserve are updated in every block, as tokens are deposited and borrowed. If there has been no activity for a while, Aave V3 uses the most recent liquidity rate to project the up-to-date liquidity index when calculating balances.

Relevant contracts for reference:
* https://github.com/aave/aave-v3-core/blob/v1.16.0/contracts/protocol/tokenization/base/ScaledBalanceTokenBase.sol#L69 (_mintScaled)
* https://github.com/aave/aave-v3-core/blob/v1.16.0/contracts/protocol/tokenization/AToken.sol#L131 (balanceOf)
* https://github.com/aave/aave-v3-core/blob/v1.16.0/contracts/protocol/libraries/logic/ReserveLogic.sol#L47 (getNormalizedIncome)
* https://github.com/aave/aave-v3-core/blob/v1.16.0/contracts/protocol/libraries/math/MathUtils.sol#L23 (calculateLinearInterest)
* https://github.com/aave/aave-v3-core/blob/v1.16.0/contracts/protocol/tokenization/VariableDebtToken.sol (balanceOf)
* https://github.com/aave/aave-v3-core/blob/v1.16.0/contracts/protocol/tokenization/StableDebtToken.sol (balanceOf)
