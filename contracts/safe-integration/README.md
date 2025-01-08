# Trading Strategy module as a Safe module using Zodiac.

[TradingStrategyModuleV0](./src/TradingStrategyModuleV0.sol) is a Zodiac-module for Gnosis Safe multisignature wallets.
It enables automated trading, "trading algorithms" or "AI-agent", for various use cases.`

- You can assign an asset management role for an automated system 
- The asset manager can perform automated trades allowed by whitelist-based rules
- The module is designed to be used with [Lagoon vaults](https://tradingstrategy.ai/glossary/lagoon),
  but should work with any Gnosis Safe where you enable this module
- For more information see [Trading Strategy website](https://tradingstrategy.ai)
- Code is not audited
- Automated trading involves a lot of risk outside technical risks

Module is called [TradingStrategyModuleV0](./src/TradingStrategyModuleV0.sol) reflecting its MVP nature.

## Dependencies

Included as Github submodules

- Zodiac: main: https://github.com/gnosisguild/zodiac/tree/master/contractscd ..
- Safe> v1.3.0-1: https://github.com/safe-global/safe-smart-account/
- OpenZeppelin: release-v3.4: https://github.com/OpenZeppelin/openzeppelin-contracts

## Rules and whitelists

- See [GuardBaseV0](../guard/src/GuardV0Base.sol) for the rule logic and supported protocols

## How to use

- The code is designed to be used with offchain Python automation
- [See Lagoon integration code in eth-defi Python package](https://web3-ethereum-defi.readthedocs.io/api/lagoon/index.html)

