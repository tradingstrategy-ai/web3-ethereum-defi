"""Maintained chain-scoped strategy classifications for Accountable vaults."""

from eth_typing import HexAddress

from eth_defi.vault.strategy_tag import StrategyTag, lookup_strategy_tags

#: Chain containing the reviewed Accountable deployments below.
MONAD_CHAIN_ID = 143

#: Reviewed Accountable share vaults deployed on Ethereum.
ETHEREUM_STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Vault: Vectis UltraX Arb, Ethereum (1).
    #: Added: 2026-11-09.
    #: Decision material: The current Accountable API maps loan 608280921 to
    #: this share token and identifies Vectis Finance as manager. Vectis's ETH
    #: documentation confirms Accountable infrastructure, automated allocation,
    #: hedged perpetual positions and funding-rate/basis arbitrage across venues.
    #: Sources:
    #: - https://yield.accountable.capital/api/loan/608280921
    #: - https://docs.vectis.finance/vaults/ultrax-arb-vault-v2-eth
    #: - https://tradingstrategy.ai/vaults/vectis-ultrax-arb
    "0x05ed3da1deeaaa57c8e09986e95ac271572dcf89": {
        StrategyTag.arbitrage,
        StrategyTag.delta_neutral,
        StrategyTag.funding_rate_arbitrage,
        StrategyTag.perpetual_futures,
        StrategyTag.algorithmic_trading,
    },
}

#: Reviewed Accountable share vaults deployed on Monad.
STRATEGY_TAGS: dict[str, set[StrategyTag]] = {
    #: Vault: Saturn PT-USDat Loop Vault, Monad (143).
    #: Added: 2026-11-02.
    #: Decision material: Current local vault-metadata-db.pickle row inspected
    #: on this date describes recursive USDC borrowing against PT-USDat,
    #: earning the fixed-yield/variable-borrow-cost spread and selling PT on
    #: Pendle's AMM. This supports looping, carry and AMM venue tags, not
    #: liquidity provision or lending interest earned from supplying USDC.
    #: Sources:
    #: - https://yield.accountable.capital/vaults/0x593784188369E5e5f484025A1940cf437620C3Fe
    #: - docs/protocol-research/saturn-pt-usdat-description.md
    "0x1a5b77cef5e4b6f806aeac6f6ea9213a617dc7e3": {
        StrategyTag.lending_looping,
        StrategyTag.carry_trade,
        StrategyTag.amm,
    },
    #: Vault: Glasgow Cosmic by Ryllo, Monad (143).
    #: Added: 2026-10-22.
    #: Decision material: The vault finances the cricket team's working capital
    #: through credit secured by its real-world receivables. Accountable's
    #: cached metadata identifies Ryllo as the manager and describes this
    #: strategy; the current public vault listing corroborates the description.
    #: Sources:
    #: - https://yield.accountable.capital/vaults/0x44DF31Ec2F1dD517268929903061a47edb7fbD1f
    #: - https://tradingstrategy.ai/vaults/glasgow-cosmic-by-ryllo
    #: - https://ryllo.co/
    "0x899e270df335287f687fdac8a9089059b042044f": {
        StrategyTag.rwa,
        StrategyTag.rwa_credit,
        StrategyTag.rwa_lending,
    },
}


#: Reviewed Tauri deployments, keyed by chain to avoid cross-chain attribution.
TRAVESSIA_STRATEGY_TAGS: dict[int, dict[str, set[StrategyTag]]] = {
    1: {
        #: Vault: Travessia Tauri Vault, chain 1.
        #: Added: 2026-11-09.
        #: Decision material: Stablecoin loans fund Tauri Agricola's grain
        #: purchases and transport until buyers pay their invoices.
        #: This is real-world business credit, not commodity-price trading.
        #: Sources:
        #: - https://www.travessiacredit.com/vaults/1/tauri/0x4C18E2bb9942b12b28e780acF2D9EC2DDA126df9
        "0x4c18e2bb9942b12b28e780acf2d9ec2dda126df9": {StrategyTag.rwa, StrategyTag.rwa_credit, StrategyTag.rwa_lending},
        #: Vault: Travessia Tauri Vault - USDC, chain 1.
        #: Added: 2026-11-09.
        #: Decision material: Stablecoin loans fund Tauri Agricola's grain
        #: purchases and transport until buyers pay their invoices.
        #: This is real-world business credit, not commodity-price trading.
        #: Sources:
        #: - https://www.travessiacredit.com/vaults/1/tauri/0xf84A37545e313E8177d80039158ed478C7Dc9079
        "0xf84a37545e313e8177d80039158ed478c7dc9079": {StrategyTag.rwa, StrategyTag.rwa_credit, StrategyTag.rwa_lending},
        #: Vault: Travessia Tauri Vault - USDT, chain 1.
        #: Added: 2026-11-09.
        #: Decision material: Stablecoin loans fund Tauri Agricola's grain
        #: purchases and transport until buyers pay their invoices.
        #: This is real-world business credit, not commodity-price trading.
        #: Sources:
        #: - https://www.travessiacredit.com/vaults/1/tauri/0xe4EE0fe4B09021FCdF76B165AA1e8eAF7d447c35
        "0xe4ee0fe4b09021fcdf76b165aa1e8eaf7d447c35": {StrategyTag.rwa, StrategyTag.rwa_credit, StrategyTag.rwa_lending},
    },
    143: {
        #: Vault: Travessia Tauri Vault, chain 143.
        #: Added: 2026-11-09.
        #: Decision material: Stablecoin loans fund Tauri Agricola's grain
        #: purchases and transport until buyers pay their invoices.
        #: This is real-world business credit, not commodity-price trading.
        #: Sources:
        #: - https://www.travessiacredit.com/vaults/143/tauri/0x8D24B48Cb736addb5B7069565F192A052F42Ba61
        "0x8d24b48cb736addb5b7069565f192a052f42ba61": {StrategyTag.rwa, StrategyTag.rwa_credit, StrategyTag.rwa_lending},
    },
    43114: {
        #: Vault: Travessia Tauri Vault, chain 43114.
        #: Added: 2026-11-09.
        #: Decision material: Stablecoin loans fund Tauri Agricola's grain
        #: purchases and transport until buyers pay their invoices.
        #: This is real-world business credit, not commodity-price trading.
        #: Sources:
        #: - https://www.travessiacredit.com/vaults/43114/tauri/0x9c3da70cD7feDEAeF528d060ea99AcE133E08Fd2
        "0x9c3da70cd7fedeaef528d060ea99ace133e08fd2": {StrategyTag.rwa, StrategyTag.rwa_credit, StrategyTag.rwa_lending},
    },
    8453: {
        #: Vault: Travessia Tauri Vault - USDC, chain 8453.
        #: Added: 2026-11-09.
        #: Decision material: The current official address-specific page
        #: describes loans financing Tauri Agricola's Brazilian grain trades.
        #: Sources:
        #: - https://www.travessiacredit.com/vaults/8453/tauri/0xb37cc5e891d87c5a3ef750e1bdf59c960575593c
        "0xb37cc5e891d87c5a3ef750e1bdf59c960575593c": {StrategyTag.rwa, StrategyTag.rwa_credit, StrategyTag.rwa_lending},
    },
    137: {
        #: Vault: Travessia Tauri Vault - BRZ, chain 137.
        #: Added: 2026-11-09.
        #: Decision material: The current official address-specific page
        #: describes loans financing Tauri Agricola's Brazilian grain trades.
        #: Sources:
        #: - https://www.travessiacredit.com/vaults/137/tauri/0x08dd5f2a0b107b8f93c4f524901994353bedfaf7
        "0x08dd5f2a0b107b8f93c4f524901994353bedfaf7": {StrategyTag.rwa, StrategyTag.rwa_credit, StrategyTag.rwa_lending},
    },
}


def get_strategy_tags(chain_id: int, address: HexAddress) -> set[StrategyTag] | None:
    """Resolve the reviewed Accountable strategy for a chain and share address.

    Keep the original Monad-only classifications scoped to Monad while allowing
    separately researched Tauri and Vectis deployments on other chains. Lookups return
    copies so consumers cannot mutate the maintained tables.

    :param chain_id: Chain containing the share token.
    :param address: Share token address, normalised by the lookup helper.
    :return: Maintained tags or ``None`` for an unreviewed deployment.
    """
    tags = lookup_strategy_tags(TRAVESSIA_STRATEGY_TAGS.get(chain_id, {}), address)
    if tags is not None:
        return tags
    if chain_id == 1:
        return lookup_strategy_tags(ETHEREUM_STRATEGY_TAGS, address)
    if chain_id == MONAD_CHAIN_ID:
        return lookup_strategy_tags(STRATEGY_TAGS, address)
    return None
