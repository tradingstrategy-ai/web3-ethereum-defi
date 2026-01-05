# ETH Strategy

## Summary

ETH Strategy is a DeFi treasury protocol that offers leveraged ETH exposure without risk of liquidation or volatility decay. It operates as Ethereum's first autonomous treasury protocol, designed to reimagine MicroStrategy's approach in a DeFi-native context. The ESPN (ETH Strategy Perpetual Note) vault is an ERC-4626 vault that allows users to deposit USDS stablecoins to earn yield.

## Details

- **Chain**: Ethereum Mainnet
- **Address**: `0xb250c9e0f7be4cff13f94374c993ac445a1385fe`
- **Explorer link**: https://etherscan.io/address/0xb250c9e0f7be4cff13f94374c993ac445a1385fe
- **Contract name**: EthStrategyPerpetualNote (ESPN)
- **Contract type**: ERC-4626 compliant vault
- **Underlying asset**: USDS (`0xdC035D45d973E3EC169d2276DDab16f1e407384F`)
- **Deployer**: Eth Strategy: Deployer (`0x69697df59d8dc401d7f24ac55b138f99d7da725f`)

## Protocol: ETH Strategy

ETH Strategy is a tokenised vehicle for ETH accumulation that gives token holders a claim on a growing pool of ETH managed through transparent, onchain strategies. The protocol is designed to be egalitarian and takes no fee on deposits or redemptions.

Key characteristics:

- Leveraged ETH exposure without liquidation risk
- Treasury growth through convertible debt issuances
- ETH deployed to staking services and lending protocols (partnership with Ether.fi)
- DAO-governed with rage quit functionality

### Growth mechanisms

1. **Convertible Bonds**: Users buy bonds with USDC, proceeds used to acquire ETH
2. **ATM Offerings**: New tokens sold at market price when trading at premium to NAV
3. **NAV Options**: Options contracts that can be minted by governance
4. **Redemptions**: Holders can vote to redeem ETH if token trades at discount

### Links

- **Web page**: https://www.ethstrat.xyz/
- **Documentation**: https://token-strategy.gitbook.io/eth-strategy
- **GitHub**: https://github.com/dangerousfood/ethstrategy
- **Twitter/X**: https://x.com/eth_strategy
- **DefiLlama**: https://defillama.com/protocol/eth-strategy

## Smart contract developer

The smart contracts are developed by EthStrategy Inc. (dangerousfood on GitHub, Joseph Delong).

## Audits

No public audit reports were found during research. The protocol documentation and GitHub repository do not reference any completed security audits as of January 2026.

**Audit status**: Not found / Not publicly disclosed

## Fee information

According to the README and documentation:

- **No deposit fees**: Protocol is designed to take no fee on deposits
- **No redemption fees**: Protocol takes no fee on redemptions
- **Governance controlled**: Fee structure is egalitarian and controlled by DAO

The ESPN vault smart contract does not contain hardcoded fee mechanisms. Any fees would be managed by the designated manager address or through governance decisions.

## Notes

- The ESPN token represents a "perpetual, compounding income token" that productises convertible notes
- Withdrawals are disabled by default, with exits occurring through LP mechanisms
- The protocol raised approximately $46.5 million (12,342 ETH) in prelaunch funding
- US persons are not allowed to participate per the terms of service
- The vault uses USDS (Sky/MakerDAO stablecoin) as its underlying asset
- Smart contracts use OpenZeppelin libraries (ERC4626, Ownable2Step, ReentrancyGuard, SafeERC20)
- Licensed under Apache 2.0
- A Summer.fi RFC proposes onboarding ESPN as a High Risk Stables Vault: https://forum.summer.fi/t/rfc-eth-strategy-perpetual-note-espn-vault-high-risk-stables-vault/369
