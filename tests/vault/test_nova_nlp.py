"""Nova NLP curator attribution alongside its existing Lagoon integration."""

import pytest

from eth_defi.erc_4626.vault_protocol.lagoon.vault import LagoonVault
from eth_defi.vault.base import VaultSpec
from eth_defi.vault.curator import identify_curator, is_protocol_curator, load_curator_map
from eth_defi.vault.curator_export import build_curators_for_export
from eth_defi.vault.risk import VaultTechnicalRisk, get_vault_risk
from eth_defi.vault.strategy_tag import StrategyTag


@pytest.mark.parametrize("vault_name", ["Nova NLP", "Unbranded vault"])
def test_nova_nlp_curator_without_upstream_manager(vault_name: str) -> None:
    """Attribute the exact HyperEVM deployment even when Lagoon omits curators.

    Nova's documentation identifies the vault address, so attribution does not
    depend on its public name or optional offchain manager enrichment.

    :param vault_name: Public name, including a deliberately unbranded variant.
    :return: ``None`` after validating the curator and public profile.
    """
    slug = identify_curator(999, "sNLP", vault_name, "0xEeEd7BB939d65938Fe8f40dd898Cd5942E32f09E", "lagoon-finance", manager_name=None)
    assert slug == "nova-markets"
    assert is_protocol_curator(slug) is True
    assert load_curator_map()[slug]["protocol_curator"] is True
    profile = build_curators_for_export([slug])[slug]
    assert profile["name"] == "Nova Markets"
    assert profile["canonical_feeder_id"] == "nova-markets"
    assert profile["protocol_curator"] is True
    assert "NLP vault" in profile["long_description"]


@pytest.mark.parametrize(
    ("chain_id", "address", "name"),
    [
        (1, "0xeeed7bb939d65938fe8f40dd898cd5942e32f09e", "Nova NLP"),
        (42161, "0xeeed7bb939d65938fe8f40dd898cd5942e32f09e", "Nova NLP"),
        (999, "0x000000000000000000000000000000000000f00d", "Nova NLP"),
        (999, "0x000000000000000000000000000000000000f00d", "Hypernova"),
    ],
)
def test_nova_nlp_attribution_is_deployment_specific(chain_id: int, address: str, name: str) -> None:
    """Do not attribute unrelated deployments through a generic Nova alias.

    A reused address on another chain or a similar vault name is insufficient
    evidence of a relationship with Nova Markets.

    :param chain_id: Chain containing the unverified deployment.
    :param address: Unverified vault address.
    :param name: Vault name resembling Nova NLP.
    :return: ``None`` after confirming no curator is inferred.
    """
    assert identify_curator(chain_id, "sNLP", name, address, "lagoon-finance") is None


def test_nova_nlp_retains_lagoon_strategy_tags() -> None:
    """Expose the reviewed NLP strategy through the native Lagoon adapter.

    The existing maintained classification covers two-sided order-book market
    making and the optional idle-capital Morpho lending sleeve. No RPC reads
    are needed for the adapter's strategy hook.

    :return: ``None`` after validating the exact maintained strategy set.
    """
    vault = object.__new__(LagoonVault)
    vault.spec = VaultSpec(999, "0xeeed7bb939d65938fe8f40dd898cd5942e32f09e")
    assert get_vault_risk("Lagoon Finance", vault.spec.vault_address) == VaultTechnicalRisk.dangerous
    assert get_vault_risk("Lagoon Finance", "0x000000000000000000000000000000000000f00d") == VaultTechnicalRisk.low
    assert vault.get_strategy_tags() == {
        StrategyTag.delta_neutral,
        StrategyTag.lending,
        StrategyTag.liquidity_provider,
        StrategyTag.market_making,
        StrategyTag.market_making_clob,
        StrategyTag.multistrategy,
        StrategyTag.perpetual_futures,
    }
