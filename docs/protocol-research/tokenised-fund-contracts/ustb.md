# USTB contract research

Checked on 2026-07-17. This note concerns the public Ethereum fund-token
deployment and technical interface, not investor eligibility, legal rights,
custody, or investment suitability.

## Result

| Fund name | Chain and token | Smart-contract name | Description | GitHub | Docs |
| --- | --- | --- | --- | --- | --- |
| Invesco Short Duration US Government Securities Fund (USTB; Superstate-branded in the current developer documentation) | Ethereum ERC-20, [`0x43415eB6ff9DB7E26A15b704e7A3eDCe97d31C4e`](https://etherscan.io/address/0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e#code) | EIP-1967 proxy; current verified implementation [`SuperstateTokenV5_1`](https://etherscan.io/address/0x1f50a1ee0ec8275d0c83b7bb08896b4b47d6e8c4#code) at `0x1f50a1EE0eC8275d0c83B7BB08896B4b47D6E8C4` | An upgradeable, allowlisted 6-decimal ERC-20 fund share. It supports issuer mint/burn, pause/accounting pause, EIP-2612-style permit, USDC atomic subscription, off-chain redemption initiation, book-entry and cross-chain burn/bridge flows, and issuer-controlled NAV-oracle/stablecoin configuration. | [`superstateinc/ustb`](https://github.com/superstateinc/ustb) — public token and allowlist code; [`superstateinc/onchain-redemptions`](https://github.com/superstateinc/onchain-redemptions) — redemption/oracle code. | [Superstate smart-contract registry and integration guide](https://docs.superstate.com/welcome-to-superstate/smart-contracts), [USTB product documentation](https://docs.superstate.com/superstate-funds/ustb), [redemption documentation](https://docs.superstate.com/ustb/redeeming-ustb) |

## On-chain verification

Superstate's official smart-contract registry identifies the Ethereum USTB
token proxy as
[`0x43415eB6ff9DB7E26A15b704e7A3eDCe97d31C4e`](https://etherscan.io/address/0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e#code).
Etherscan labels it *Superstate: USTB Token*, recognises its EIP-1967 proxy
pattern, and resolves its active implementation to
[`0x1f50a1EE0eC8275d0c83B7BB08896B4b47D6E8C4`](https://etherscan.io/address/0x1f50a1ee0ec8275d0c83b7bb08896b4b47d6e8c4#code).

The implementation is Etherscan **Exact Match** source verified as
`SuperstateTokenV5_1`, compiled with Solidity `0.8.28` and one million
optimiser runs. Sourcify did not return hosted full- or partial-match metadata
for the proxy during this check, so the verified Etherscan implementation is
the deployment-specific source reference.

The public `superstateinc/ustb` repository is clearly the same protocol and
source family: its `SuperstateToken` is a pausable OpenZeppelin upgradeable
ERC-20 with the same subscription, allowlist, burn, bridge, accounting, and
oracle model. Its presently published `main` source is version 4 and does not
contain the exact `SuperstateTokenV5_1` contract name, so this research does
not claim an exact current-bytecode-to-Git-commit correspondence. The verified
Etherscan source remains authoritative for V5.1.

## Contract surface and behaviour

| Area | Functions | Effect |
| --- | --- | --- |
| ERC-20 and permit | `name`, `symbol`, `decimals`, `totalSupply`, `balanceOf`, `allowance`, `approve`, `transfer`, `transferFrom`, `permit`, `nonces`, `DOMAIN_SEPARATOR` | Standard token interface plus signed approval; transfer paths are gated by the private-instrument allowlist. |
| Investor eligibility | `allowlistV2`, `isAllowed` | Exposes the external Superstate allowlist used to decide whether an address may hold/transact in the fund token. |
| Issuance and redemption | `mint`, `bulkMint`, `adminBurn`, `offchainRedeem` | Owner-controlled creation/burning of shares, and a holder path to initiate the standard off-chain redemption process. |
| Atomic USDC subscription | overloaded `subscribe`, `calculateSuperstateTokenOut`, `supportedStablecoins` | On Ethereum, an allowlisted investor/protocol can transfer a configured stablecoin (currently documented as USDC) and receive USTB atomically at the oracle price, optionally to another allowlisted address. |
| Book-entry and cross-chain movement | `bridge`, `bridgeToBookEntry`, `supportedChainIds` | Burns source-chain USTB to receive the specified amount on a supported destination chain, or converts tokens into Superstate book-entry shares. |
| Price and redemption liquidity | `superstateOracle`, `getChainlinkPrice`, `redemptionContract`, `setOracle`, `setRedemptionContract` | References the issuer's continuous-price oracle and the separate redemption-liquidity contract. |
| Emergency and administration | `pause`, `unpause`, `accountingPause`, `accountingUnpause`, `owner`, `transferOwnership`, configuration setters | Owner can halt transfers or issuance/redemption accounting and alter oracle, fee, stablecoin, redemption, and bridge-chain configuration. |

The documented transfer control is not merely advisory: both sender and
recipient must be allowlisted and authorised for the specific private
instrument. The docs explain that identities are represented by Superstate
entity IDs and permissions, maintained after KYC and investment-agreement
processes; an unauthorised ERC-20 wallet cannot freely receive USTB.

## Protocol-family conclusion

**Conclusion: Superstate's own permissioned tokenised-fund protocol (high
confidence), based on OpenZeppelin upgradeable ERC-20 components, not
ERC-4626.**

The match is direct rather than inferred: the issuer's contract registry
names the exact proxy, links its public repositories, and describes the EVM
implementation as an upgradeable OpenZeppelin ERC-20 with Superstate's
allowlist, mint, redemption, bridge, and oracle extensions. The public source
and the deployment ABI share these distinctive function families. The token
does not expose the ERC-4626 `asset`, `deposit`, `withdraw`, or `redeem`
interface; its `subscribe` and redemption mechanisms are Superstate-specific
flows coupled to allowlisting and issuer pricing/liquidity infrastructure.

## On-chain supply and ABI price availability

At 2026-07-17, `totalSupply()` on Ethereum returned **59,105,391.356421 USTB**
(6 decimals). The token ABI provides pricing routes through `superstateOracle`
and `getChainlinkPrice()`; the documented continuous-price and daily NAV feeds
are the supported share-price sources rather than `totalSupply()`.

## Related contracts and integration implications

The official registry lists the following Ethereum components. They are
architecture dependencies, not alternative USTB token addresses:

| Contract | Address | Purpose |
| --- | --- | --- |
| `AllowlistV3` proxy | [`0x02f1fa8b196d21c7b733eb2700b825611d8a38e5`](https://etherscan.io/address/0x02f1fa8b196d21c7b733eb2700b825611d8a38e5#code) | Superstate's current permission/identity registry for EVM token holders and protocols. |
| `RedemptionIdle` proxy | [`0x4c21b7577c8fe8b0b0669165ee7c8f67fa1454cf`](https://etherscan.io/address/0x4c21b7577c8fe8b0b0669165ee7c8f67fa1454cf#code) | Holds replenished USDC liquidity for one-transaction USTB redemptions; `redeem` can fail if the contract lacks USDC. |
| USTB continuous-price oracle | [`0xe4fa682f94610ccd170680cc3b045d77d9e528a8`](https://etherscan.io/address/0xe4fa682f94610ccd170680cc3b045d77d9e528a8#code) | Superstate's NAV-per-share oracle, which linearly extrapolates from its two newest NAV checkpoints and presents a Chainlink-compatible interface. |
| Chainlink USTB oracle | [`0x289B5036cd942e619E1Ee48670F98d214E745AAC`](https://etherscan.io/address/0x289b5036cd942e619e1ee48670f98d214e745aac#code) | Issuer-listed daily NAV/share feed. |

- Track the proxy address, resolve its implementation dynamically, and observe
  upgrades/configuration changes. Do not consider its current implementation
  address immutable protocol state.
- Treat USTB as a compliance-gated tokenised fund, not a permissionless
  money-market vault. Standard ERC-20 reads work, but transfer, subscription,
  redemption, and bridge integrations must account for eligibility and pause
  conditions.
- For NAV and atomic settlement, use the documented oracle/redemption paths;
  `totalSupply` is not a standalone measure of current value or redeemable
  USDC liquidity.
- Avoid double counting shares burned by `bridge`, `bridgeToBookEntry`, or
  redemption flows when aggregating multi-chain or book-entry holdings.

## Primary sources

- [Etherscan USTB token proxy](https://etherscan.io/address/0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e#code)
- [Etherscan verified `SuperstateTokenV5_1` implementation](https://etherscan.io/address/0x1f50a1ee0ec8275d0c83b7bb08896b4b47d6e8c4#code)
- [Superstate's contract registry and technical integration documentation](https://docs.superstate.com/welcome-to-superstate/smart-contracts)
- [USTB fund documentation](https://docs.superstate.com/superstate-funds/ustb)
- [Superstate USTB token/allowlist source](https://github.com/superstateinc/ustb)
- [Superstate on-chain redemption/oracle source](https://github.com/superstateinc/onchain-redemptions)
