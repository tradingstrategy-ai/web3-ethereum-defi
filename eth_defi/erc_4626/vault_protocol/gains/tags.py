"""Evidence-based strategy classifications for reviewed Gains-family vaults.

Unconfirmed forks and staging products are deliberately unmapped. Exact
identities and their evidence live in :mod:`.metadata`; contract compatibility
alone does not establish a live liquidity-provision strategy.
"""

from eth_typing import HexAddress

from eth_defi.erc_4626.vault_protocol.gains.metadata import lookup_gains_vault_metadata
from eth_defi.vault.strategy_tag import StrategyTag, lookup_strategy_tags

_GTRADE_TAGS: set[StrategyTag] = {
    StrategyTag.amm,
    StrategyTag.liquidity_provider,
    StrategyTag.market_making,
    StrategyTag.market_making_amm,
    StrategyTag.perpetual_futures,
}


STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Vault: gUSDC, Base. Added: 2026-08-18; reviewed: 2026-10-28.
    #: Decision material: Official directory identifies this exact collateral
    #: pool; gToken depositors supply AMM counterparty liquidity to perpetuals
    #: for fees while bearing trader PnL. These are liquidity-provider,
    #: market-making, AMM and perpetual-futures exposures.
    #: Sources:
    #: - https://docs.gains.trade/what-is-gains-network/contract-addresses/base-mainnet
    #: - https://docs.gains.trade/liquidity-farming-pools/gtoken-vaults
    "0xad20523a7dc37babc1cc74897e4977232b3d02e5": _GTRADE_TAGS.copy(),
    #: Vault: gBTCUSD, Base. Added: 2026-08-18; reviewed: 2026-10-28.
    #: Decision material: Official directory identifies this exact collateral
    #: pool; gToken depositors supply AMM counterparty liquidity to perpetuals
    #: for fees while bearing trader PnL. These are liquidity-provider,
    #: market-making, AMM and perpetual-futures exposures.
    #: Sources:
    #: - https://docs.gains.trade/what-is-gains-network/contract-addresses/base-mainnet
    #: - https://docs.gains.trade/liquidity-farming-pools/gtoken-vaults
    "0xb7cb7cb7c3cd96e251c9bf8800b9631134bbadc6": _GTRADE_TAGS.copy(),
    #: Vault: gDAI, Polygon. Added: 2026-08-18; reviewed: 2026-10-28.
    #: Decision material: Official directory identifies this exact collateral
    #: pool; gToken depositors supply AMM counterparty liquidity to perpetuals
    #: for fees while bearing trader PnL. These are liquidity-provider,
    #: market-making, AMM and perpetual-futures exposures.
    #: Sources:
    #: - https://docs.gains.trade/what-is-gains-network/contract-addresses/polygon-mainnet
    #: - https://docs.gains.trade/liquidity-farming-pools/gtoken-vaults
    "0x91993f2101cc758d0deb7279d41e880f7defe827": _GTRADE_TAGS.copy(),
    #: Vault: gETH, Polygon. Added: 2026-08-18; reviewed: 2026-10-28.
    #: Decision material: Official directory identifies this exact collateral
    #: pool; gToken depositors supply AMM counterparty liquidity to perpetuals
    #: for fees while bearing trader PnL. These are liquidity-provider,
    #: market-making, AMM and perpetual-futures exposures.
    #: Sources:
    #: - https://docs.gains.trade/what-is-gains-network/contract-addresses/polygon-mainnet
    #: - https://docs.gains.trade/liquidity-farming-pools/gtoken-vaults
    "0x1544e1ff1a6f6bdbfb901622c12bb352a43464fb": _GTRADE_TAGS.copy(),
    #: Vault: gUSDC, Polygon. Added: 2026-08-18; reviewed: 2026-10-28.
    #: Decision material: Official directory identifies this exact collateral
    #: pool; gToken depositors supply AMM counterparty liquidity to perpetuals
    #: for fees while bearing trader PnL. These are liquidity-provider,
    #: market-making, AMM and perpetual-futures exposures.
    #: Sources:
    #: - https://docs.gains.trade/what-is-gains-network/contract-addresses/polygon-mainnet
    #: - https://docs.gains.trade/liquidity-farming-pools/gtoken-vaults
    "0x29019fe2e72e8d4d2118e8d0318bef389ffe2c81": _GTRADE_TAGS.copy(),
    #: Vault: gDAI, Arbitrum. Added: 2026-08-18; reviewed: 2026-10-28.
    #: Decision material: Official directory identifies this exact collateral
    #: pool; gToken depositors supply AMM counterparty liquidity to perpetuals
    #: for fees while bearing trader PnL. These are liquidity-provider,
    #: market-making, AMM and perpetual-futures exposures.
    #: Sources:
    #: - https://docs.gains.trade/what-is-gains-network/contract-addresses/arbitrum-mainnet
    #: - https://docs.gains.trade/liquidity-farming-pools/gtoken-vaults
    "0xd85e038593d7a098614721eae955ec2022b9b91b": _GTRADE_TAGS.copy(),
    #: Vault: gETH, Arbitrum. Added: 2026-08-18; reviewed: 2026-10-28.
    #: Decision material: Official directory identifies this exact collateral
    #: pool; gToken depositors supply AMM counterparty liquidity to perpetuals
    #: for fees while bearing trader PnL. These are liquidity-provider,
    #: market-making, AMM and perpetual-futures exposures.
    #: Sources:
    #: - https://docs.gains.trade/what-is-gains-network/contract-addresses/arbitrum-mainnet
    #: - https://docs.gains.trade/liquidity-farming-pools/gtoken-vaults
    "0x5977a9682d7af81d347cfc338c61692163a2784c": _GTRADE_TAGS.copy(),
    #: Vault: gUSDC, Arbitrum. Added: 2026-08-18; reviewed: 2026-10-28.
    #: Decision material: Official directory identifies this exact collateral
    #: pool; gToken depositors supply AMM counterparty liquidity to perpetuals
    #: for fees while bearing trader PnL. These are liquidity-provider,
    #: market-making, AMM and perpetual-futures exposures.
    #: Sources:
    #: - https://docs.gains.trade/what-is-gains-network/contract-addresses/arbitrum-mainnet
    #: - https://docs.gains.trade/liquidity-farming-pools/gtoken-vaults
    "0xd3443ee1e91af28e5fb858fbd0d72a63ba8046e0": _GTRADE_TAGS.copy(),
    #: Vault: gGNS, Arbitrum. Added: 2026-08-18; reviewed: 2026-10-28.
    #: Decision material: Official directory identifies this exact collateral
    #: pool; gToken depositors supply AMM counterparty liquidity to perpetuals
    #: for fees while bearing trader PnL. These are liquidity-provider,
    #: market-making, AMM and perpetual-futures exposures.
    #: Sources:
    #: - https://docs.gains.trade/what-is-gains-network/contract-addresses/arbitrum-mainnet
    #: - https://docs.gains.trade/liquidity-farming-pools/gtoken-vaults
    "0x4beef1113f968326905224d2ca272f3032a9a9f4": _GTRADE_TAGS.copy(),
    #: Vault: gUSDm, MegaETH. Added: 2026-08-18; reviewed: 2026-10-28.
    #: Decision material: Official directory identifies this exact collateral
    #: pool; gToken depositors supply AMM counterparty liquidity to perpetuals
    #: for fees while bearing trader PnL. These are liquidity-provider,
    #: market-making, AMM and perpetual-futures exposures.
    #: Sources:
    #: - https://docs.gains.trade/what-is-gains-network/contract-addresses/megaeth-mainnet
    #: - https://docs.gains.trade/liquidity-farming-pools/gtoken-vaults
    "0x46344456f130e9dcdea7f98cdb0e02fb9f4ab72d": _GTRADE_TAGS.copy(),
}


def get_strategy_tags(chain_id: int, address: HexAddress | str) -> set[StrategyTag] | None:
    """Return tags only for the chain on which the product was verified.

    The address-only mapping remains available for existing consumers, while
    adapters and metadata repairs use this deployment-aware resolver.

    :param chain_id: Deployment chain ID.
    :param address: Vault contract address.
    :return: A copy of the reviewed tags, or ``None`` for missing information.
    """
    metadata = lookup_gains_vault_metadata(chain_id, address)
    if metadata is None or metadata.curator is None:
        return None
    return lookup_strategy_tags(STRATEGY_TAGS, address)
