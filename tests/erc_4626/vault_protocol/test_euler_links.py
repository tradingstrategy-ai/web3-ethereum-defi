"""Euler vault frontend link tests."""

from web3 import Web3

from eth_defi.erc_4626.vault_protocol.euler.vault import ALPHAGROWTH_EULER_LIGHT_BASE_URL, EulerEarnVault, EulerVault
from eth_defi.vault.base import VaultSpec


def create_euler_vault(
    chain_id: int,
    vault_address: str,
    vault_class: type[EulerVault] | type[EulerEarnVault] = EulerVault,
) -> EulerVault | EulerEarnVault:
    """Create an Euler vault instance for link-only tests.

    Link construction only needs the vault spec, so these tests can use a plain
    ``Web3`` instance without RPC configuration.

    :param chain_id:
        EVM chain id for the vault.

    :param vault_address:
        ERC-4626 vault contract address.

    :return:
        Euler vault instance.
    """
    return vault_class(
        web3=Web3(),
        spec=VaultSpec(chain_id=chain_id, vault_address=vault_address),
    )


def test_euler_alphagrowth_ausd_vault_uses_light_frontend() -> None:
    """AlphaGrowth AUSD vault links to its Euler Light lend route."""
    vault_address = "0x438cedcE647491B1d93a73d491eC19A50194c222"
    vault = create_euler_vault(chain_id=143, vault_address=vault_address)

    assert vault.get_link() == f"{ALPHAGROWTH_EULER_LIGHT_BASE_URL}/lend/{Web3.to_checksum_address(vault_address)}"


def test_euler_alphagrowth_ausd_vault_uses_light_metadata() -> None:
    """AlphaGrowth AUSD vault uses metadata from AlphaGrowth's label repository."""
    vault_address = "0x438cedcE647491B1d93a73d491eC19A50194c222"
    vault = create_euler_vault(chain_id=143, vault_address=vault_address)

    assert vault.name == "AlphaGrowth AUSD Borrow Vault"
    assert vault.description == "Borrow AUSD against Balancer BPT collateral."
    assert vault.entity == "alphagrowth"
    assert vault.manager_name == "alphagrowth"


def test_euler_alphagrowth_wmon_vault_uses_light_frontend() -> None:
    """AlphaGrowth WMON vault links to its Euler Light lend route."""
    vault_address = "0x75b6c392f778b8bcf9bdb676f8f128b4dd49ac19"
    vault = create_euler_vault(chain_id=143, vault_address=vault_address)

    assert vault.get_link() == f"{ALPHAGROWTH_EULER_LIGHT_BASE_URL}/lend/{Web3.to_checksum_address(vault_address)}"


def test_euler_regular_monad_vault_uses_official_frontend() -> None:
    """Non-special Monad EVK vaults use Euler's Lend page and numeric chain id."""
    vault = create_euler_vault(chain_id=143, vault_address="0x1111111111111111111111111111111111111111")

    assert vault.get_link() == "https://app.euler.finance/lend/0x1111111111111111111111111111111111111111?network=143"


def test_euler_alphagrowth_address_on_other_chain_uses_official_frontend() -> None:
    """The custom AlphaGrowth link is scoped to Monad deployments."""
    vault_address = "0x438cedcE647491B1d93a73d491eC19A50194c222"
    vault = create_euler_vault(chain_id=1, vault_address=vault_address)

    assert vault.get_link() == f"https://app.euler.finance/lend/{Web3.to_checksum_address(vault_address)}?network=1"


def test_euler_evk_hyperevm_vault_uses_lend_frontend() -> None:
    """Clearstar Yield is an EVK vault and cannot load in Euler's Earn page."""
    vault_address = "0x94F5C76A93F12057d73991AE5B4f70e9287b5b28"
    vault = create_euler_vault(chain_id=999, vault_address=vault_address)

    assert vault.get_link() == f"https://app.euler.finance/lend/{Web3.to_checksum_address(vault_address)}?network=999"


def test_euler_earn_vault_uses_earn_frontend() -> None:
    """Listed EulerEarn vaults use Euler's Earn page and numeric chain id."""
    vault_address = "0x1111111111111111111111111111111111111111"
    vault = create_euler_vault(chain_id=1, vault_address=vault_address, vault_class=EulerEarnVault)

    assert vault.get_link() == f"https://app.euler.finance/earn/{Web3.to_checksum_address(vault_address)}?network=1"


def test_busd0_zero_rate_vault_uses_fira_frontend() -> None:
    """bUSD0 Zero Rate Vault is unsupported by Euler's Earn app."""
    vault = create_euler_vault(
        chain_id=1,
        vault_address="0xFE7C47895eDb12a990b311Df33B90Cfea1D44c24",
        vault_class=EulerEarnVault,
    )

    assert vault.get_link() == "https://app.fira.money/market/busd0-usd0?type=variable"
