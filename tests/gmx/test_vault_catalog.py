"""Unit tests for onchain GMX V2 vault enumeration."""

from types import SimpleNamespace

import pytest
from eth_utils import to_checksum_address

from eth_defi.gmx import vault_catalog
from eth_defi.gmx.vault_catalog import fetch_gmx_v2_vault_products

MARKET_1 = to_checksum_address("0x1000000000000000000000000000000000000001")
MARKET_2 = to_checksum_address("0x1000000000000000000000000000000000000002")
MARKET_3 = to_checksum_address("0x1000000000000000000000000000000000000003")
INDEX = to_checksum_address("0x2000000000000000000000000000000000000001")
LONG = to_checksum_address("0x2000000000000000000000000000000000000002")
SHORT = to_checksum_address("0x2000000000000000000000000000000000000003")
GLV = to_checksum_address("0x3000000000000000000000000000000000000001")
BLOCK_NUMBER = 123


class _Call:
    """Minimal Web3 contract-call fake."""

    def __init__(self, result: object) -> None:
        self.result = result

    def call(self, *, block_identifier: int) -> object:
        assert block_identifier == BLOCK_NUMBER
        return self.result


def _fake_get_markets(_datastore: str, start: int, end: int) -> _Call:
    """Return deterministic paginated GM products."""
    pages = {
        (0, 2): [(MARKET_1, INDEX, LONG, SHORT), (MARKET_2, INDEX, LONG, SHORT)],
        (2, 4): [(MARKET_3, INDEX, LONG, SHORT)],
    }
    return _Call(pages.get((start, end), []))


def _fake_get_glv_info_list(_datastore: str, start: int, end: int) -> _Call:
    """Return deterministic paginated GLV products."""
    pages = {
        (0, 2): [((GLV, LONG, SHORT), (MARKET_1, MARKET_2))],
    }
    return _Call(pages.get((start, end), []))


def _fake_get_bool(_key: bytes) -> _Call:
    """Mark every fake GMX DataStore flag as enabled."""
    return _Call(False)


def test_fetch_gmx_v2_vault_products(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enumerate paged GM and GLV products without depending on REST listings."""
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=42161))
    reader = SimpleNamespace(functions=SimpleNamespace(getMarkets=_fake_get_markets))
    glv_reader = SimpleNamespace(functions=SimpleNamespace(getGlvInfoList=_fake_get_glv_info_list))
    datastore = SimpleNamespace(functions=SimpleNamespace(getBool=_fake_get_bool))

    monkeypatch.setattr(vault_catalog, "get_contract_addresses", lambda _chain: SimpleNamespace(datastore="0x4000000000000000000000000000000000000001"))
    monkeypatch.setattr(vault_catalog, "get_reader_contract", lambda _web3, _chain: reader)
    monkeypatch.setattr(vault_catalog, "get_glv_reader_contract", lambda _web3, _chain: glv_reader)
    monkeypatch.setattr(vault_catalog, "get_datastore_contract", lambda _web3, _chain: datastore)
    monkeypatch.setattr(
        vault_catalog,
        "fetch_erc20_details",
        lambda _web3, address, **_kwargs: SimpleNamespace(symbol=f"GM-{address[-1]}", name=f"GMX share {address[-1]}", decimals=18),
    )

    products = list(fetch_gmx_v2_vault_products(web3, page_size=2, block_identifier=BLOCK_NUMBER))

    assert [product.token_address for product in products] == [MARKET_1, MARKET_2, MARKET_3, GLV]
    assert [product.product_type for product in products] == ["gm", "gm", "gm", "glv"]
    assert products[0].component_addresses == (MARKET_1, INDEX, LONG, SHORT)
    assert products[0].accepted_deposit_tokens == (LONG, SHORT)
    assert products[3].component_addresses == (GLV, LONG, SHORT, MARKET_1, MARKET_2)
    assert all(product.is_enabled for product in products)


def test_fetch_gmx_v2_vault_products_rejects_unknown_chain() -> None:
    """Do not accidentally catalogue a non-GMX deployment as GMX V2."""
    web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1))

    with pytest.raises(ValueError, match="unsupported"):
        list(fetch_gmx_v2_vault_products(web3, block_identifier=BLOCK_NUMBER))
