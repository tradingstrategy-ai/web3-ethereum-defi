"""Vault deployments covered by the Trading Strategy podcast.

The dated inventory is shared by the metadata scanner, native exports and
migration 094. Keep this inventory fixed when adding later episodes.

Xerberus and CORE3 cover risk rating methods. The Yearn episode names Term
Finance as an adopter of Yearn V3 without giving an individual vault identity.
"""

from eth_defi.vault.strategy_tag import StrategyTag

#: Chain and vault address pairs by episode. Addresses refer to the covered
#: products themselves, including where a curator's listing contains feeders.
PODCAST_VAULTS_EPISODES_1_TO_16: frozenset[tuple[int, str]] = frozenset(
    {
        # TAU Labs discusses its Pendle bond vault and the InfiniFi and Reservoir loops.
        # https://tradingstrategy.ai/blog/tau-labs-podcast-1
        # https://tradingstrategy.ai/vaults/curators/tau
        (1, "0x08dd57a51025a266cecc85154860aa4e71e48ee8"),  # TAU Yield Bond ETF
        (1, "0xb0f56bb0bf13ee05fef8cd2d8df5ffdfcac7a74f"),  # TAU InfiniFi Pointsmax
        (1, "0x6f66b845604dad6e80b2a1472e6cacbbe66a8c40"),  # TAU Reservoir Pointsmax
        # Ember describes eEarn and its planned tranche integration.
        # https://tradingstrategy.ai/blog/podcast-2-ember-protocol
        # https://tradingstrategy.ai/vaults/ember-earn
        (1, "0x9be9294722f8aad37b11a9792be2c782182cafa2"),  # Ember Earn
        # Taler's post links directly to its first vault with TAU.
        # https://tradingstrategy.ai/blog/podcast-3-taler-finance
        # https://tradingstrategy.ai/vaults/tau-lending-optimizer
        (1, "0x63103375659d0aa94e9f35df15be01a3dd1ae9c0"),  # TAU Lending Optimizer
        # ETH Strategy explains its bondholder yield and ETH exposure.
        # https://tradingstrategy.ai/blog/podcast-4-ethstrategy
        # https://tradingstrategy.ai/vaults/eth-strategy-perpetual-note
        (1, "0xb250c9e0f7be4cff13f94374c993ac445a1385fe"),  # ETH Strategy Perpetual Note
        # AlphaGrowth describes AUSD and WMON loans against Balancer collateral.
        # https://tradingstrategy.ai/blog/episode-5-alphagrowth
        # eth_defi/erc_4626/vault_protocol/euler/vault.py (Euler Light Monad metadata)
        (143, "0x438cedce647491b1d93a73d491ec19a50194c222"),  # AlphaGrowth AUSD Borrow Vault
        (143, "0x75b6c392f778b8bcf9bdb676f8f128b4dd49ac19"),  # AlphaGrowth WMON Borrow Vault
        # YieldNest names three MAX vault products.
        # https://tradingstrategy.ai/blog/episode-7-yieldnest-finance
        # https://docs.yieldnest.finance/security/deployment-addresses
        (1, "0x3db228fe836d99ccb25ec4dfdc80ed6d2cdcb4b"),  # ynUSDx
        (1, "0x657d9aba1dbb59e53f9f3ecaa878447dcfc96dcb"),  # ynETHx
        (1, "0x01ba69727e2860b37bc1a2bd56999c1afb4c15d8"),  # ynRWAx
        # Frankencoin discusses how FPS holders bear losses in its equity pool.
        # https://tradingstrategy.ai/blog/episode-8-frankencoin
        # https://tradingstrategy.ai/vaults/frankencoin-shares
        (1, "0xdb861830d9ae2d1fcf99fa0cfd3973de382b0b5b"),  # Frankencoin Shares
        # Noon explains sUSN and the separate vault strategy that uses it for yield.
        # https://tradingstrategy.ai/blog/episode-9-noon-capital
        # https://tradingstrategy.ai/vaults/susn-delta-neutral-yield-vault
        # https://docs.noon.capital/1.-noon-the-basics/vaults
        (1, "0xe24a3dc889621612422a64e6388927901608b91d"),  # Staked USN
        (1, "0xa73424f1ac94b3ef0d0c9af4f2967c87d4af25d9"),  # sUSN Delta Neutral Yield Vault
        (143, "0x58ba69b289de313e66a13b7d1f822fc98b970554"),  # sUSN Delta Neutral Yield Vault
        # Domination Finance explains how its USDC vault backs traders' positions.
        # https://tradingstrategy.ai/blog/episode-10-domination-finance
        # https://tradingstrategy.ai/vaults/domfi-usdc-lp
        (8453, "0xa194082aabb75dd1ca9dc1ba573a5528beb8c2fb"),  # DomFi USDC LP
        # Frax explains how it invests frxUSD deposited in sfrxUSD.
        # https://tradingstrategy.ai/blog/episode-11-frax
        # https://tradingstrategy.ai/vaults/staked-frax-usd
        (1, "0xcf62f905562626cfcdd2261162a51fd02fc9c5b6"),  # Staked Frax USD
        # Atoma explains the cross-venue funding arbitrage and points vault.
        # https://tradingstrategy.ai/blog/episode-12-atoma
        # https://tradingstrategy.ai/vaults/atoma-index
        (42161, "0xcc56410e1a136af0eceb7241c6ae394f4d8b581c"),  # Atoma Index
        # Yearn discusses BOLD, Flex and named vaults using Yearn V3 accounting.
        # https://tradingstrategy.ai/blog/episode-13-yearn
        # https://tradingstrategy.ai/vaults/curators/yearn
        # https://tradingstrategy.ai/vaults/savings-crvusd
        # https://docs.cap.app/developers/addresses
        # eth_defi/erc_4626/vault_protocol/threejane/vault.py (sUSD3)
        (1, "0x9f4330700a36b29952869fac9b33f45eedd8a3d8"),  # Yearn BOLD
        (1, "0x863687e4e9751b57f38b4b0eba04744c72d0f7b8"),  # Flex USDC yVault
        (1, "0x0655977feb2f289a4ab78af67bab0d17aab84367"),  # Savings crvUSD
        (1, "0xf689555121e529ff0463e191f9bd9d1e496164a7"),  # 3Jane Staked USD3
        (1, "0x88887be419578051ff9f4eb6c858a951921d8888"),  # Staked Cap USD
        # Growi explains the original Hyperliquid mean-reversion vault.
        # https://tradingstrategy.ai/blog/episode-14-growi-fi
        # https://tradingstrategy.ai/vaults/growi-hf
        (9999, "0x1e37a337ed460039d1b15bd3bc489de789768d5e"),  # Growi HF
        # Stratwise explains the adaptive grid used by its public spot vault.
        # https://tradingstrategy.ai/blog/episode-16-stratwise
        # https://tradingstrategy.ai/vaults/stratwise-multi-asset-public
        (9999, "0x0ff219ac20596b457558341bc410bc7a08a1394c"),  # Stratwise Multi-Asset Public
    }
)


def add_podcast_available_tag(chain_id: int, address: str, tags: set[StrategyTag] | None) -> set[StrategyTag] | None:
    """Add podcast coverage to a vault's strategy tags.

    Podcast coverage follows the deployment covered by the interview. The
    scanner and native exporter add it alongside any investment classifications.

    :param chain_id: Chain or native dataset identifier.
    :param address: Vault address, matched case-insensitively.
    :param tags: Strategy tags supplied by the vault's adapter or export.
    :return: Tags including podcast coverage where applicable, or the original value.
    """
    if (chain_id, address.lower()) in PODCAST_VAULTS_EPISODES_1_TO_16:
        return (tags or set()) | {StrategyTag.podcast_available}
    return tags
