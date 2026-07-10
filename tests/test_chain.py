"""Chain / node feature tests."""

import os

import pytest
from web3 import HTTPProvider, Web3

from eth_defi.chain import get_chain_homepage, get_chain_id_by_name, get_chain_name, get_evm_block_time, has_graphql_support
from eth_defi.provider.broken_provider import get_block_tip_latency
from eth_defi.provider.env import get_json_rpc_env, read_json_rpc_url

ROBINHOOD_CHAIN_ID = 4663
ROBINHOOD_BLOCK_TIME = 0.25
TEMPO_CHAIN_ID = 4217
TEMPO_BLOCK_TIME = 0.5


def test_robinhood_chain_metadata():
    """Robinhood chain metadata resolves through public helper APIs."""

    assert get_chain_name(ROBINHOOD_CHAIN_ID) == "Robinhood"
    assert get_chain_id_by_name("Robinhood") == ROBINHOOD_CHAIN_ID
    assert get_json_rpc_env(ROBINHOOD_CHAIN_ID) == "JSON_RPC_ROBINHOOD"
    assert get_evm_block_time(ROBINHOOD_CHAIN_ID) == ROBINHOOD_BLOCK_TIME
    assert get_chain_homepage(ROBINHOOD_CHAIN_ID) == ("Robinhood", "https://robinhood.com/us/en/chain/")


def test_tempo_chain_metadata(monkeypatch: pytest.MonkeyPatch):
    """Tempo chain metadata resolves through public helper APIs."""

    monkeypatch.setenv("JSON_RPC_TEMPO", "https://tempo.example")

    assert get_chain_name(TEMPO_CHAIN_ID) == "Tempo"
    assert get_chain_id_by_name("Tempo") == TEMPO_CHAIN_ID
    assert get_json_rpc_env(TEMPO_CHAIN_ID) == "JSON_RPC_TEMPO"
    assert read_json_rpc_url(TEMPO_CHAIN_ID) == "https://tempo.example"
    assert get_evm_block_time(TEMPO_CHAIN_ID) == TEMPO_BLOCK_TIME
    assert get_chain_homepage(TEMPO_CHAIN_ID) == ("Tempo", "https://tempo.xyz")


def test_has_not_graphql_support():
    """Check if a GoEthereum node has GraphQL support turned on."""

    # Does not provide /graphql
    provider = HTTPProvider("https://polygon-rpc.com/")
    assert not has_graphql_support(provider)


@pytest.mark.skipif(
    os.environ.get("JSON_RPC_POLYGON_PRIVATE") is None,
    reason="Set JSON_RPC_POLYGON_PRIVATE environment variable to a privately configured Polygon node with GraphQL turned on",
)
def test_has_graphql_support():
    """Check if a GoEthereum node has GraphQL support turned on."""

    # A specially set up server to test this
    # Does provide /graphql
    provider = HTTPProvider(os.environ["JSON_RPC_POLYGON_PRIVATE"])
    assert has_graphql_support(provider)


def test_block_tip_latency():
    """Check for the block tip latency by a provider."""

    # Does not provide /graphql
    provider = HTTPProvider("https://polygon-rpc.com/")
    web3 = Web3(provider)
    assert get_block_tip_latency(web3) == 0
