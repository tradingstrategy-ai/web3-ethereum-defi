"""Pallas asynchronous trading-vault support.

`Pallas <https://app.pallas.fund/>`__ runs USDT0-denominated vault contracts on
HyperEVM. The reviewed strategies trade Hyperliquid HIP-3 perpetual markets.

The proxies point to verified implementations named
``ERC7540NonCustodialTradingVaultUpgradeable``. Despite that name, the public
ABI uses Pallas-specific one-argument request and claim functions instead of
the standard ERC-7540 signatures. The generic synchronous ERC-4626 transaction
manager is therefore not exposed for these vaults.

- `Basis Trading HIP-3 vault <https://hyperevmscan.io/address/0x9b3aa83BD833123437d4efa656E7121B7F317899>`__
- `Directional Volatility vault <https://hyperevmscan.io/address/0xa642188e1345AEe1809f6db5431464b079978c68>`__
- `Current Basis implementation <https://hyperevmscan.io/address/0xe324e4a5C9f8ea9Db2F957702d4Bb164DE3caF17>`__
"""

from eth_typing import HexAddress
from web3 import Web3
from web3.types import BlockIdentifier

from eth_defi.erc_4626.vault import ERC4626Vault
from eth_defi.erc_4626.vault_protocol.pallas.constants import PALLAS_VAULT_LINKS
from eth_defi.types import Percent

#: Pallas stores management and performance fees in basis points.
PALLAS_FEE_BPS_DENOMINATOR = 10_000

#: ABI-encoded integer return size.
ABI_WORD_SIZE = 32

#: Selector for ``managementFeeBps()`` from the verified Pallas implementation.
PALLAS_MANAGEMENT_FEE_SELECTOR = bytes(Web3.keccak(text="managementFeeBps()")[0:4])

#: Selector for ``performanceFeeBps()`` from the verified Pallas implementation.
PALLAS_PERFORMANCE_FEE_SELECTOR = bytes(Web3.keccak(text="performanceFeeBps()")[0:4])


def fetch_pallas_fee(
    web3: Web3,
    vault_address: HexAddress | str,
    selector: bytes,
    block_identifier: BlockIdentifier,
) -> Percent:
    """Read one Pallas basis-point fee getter as a fractional percentage.

    The reviewed implementation exposes ordinary no-argument fee getters.
    Reading them with their canonical selectors keeps the adapter small while
    retaining block-pinned historical reads.

    - `Verified Basis implementation <https://hyperevmscan.io/address/0xe324e4a5C9f8ea9Db2F957702d4Bb164DE3caF17#code>`__

    :param web3:
        HyperEVM connection.
    :param vault_address:
        Pallas proxy address.
    :param selector:
        Four-byte selector for a no-argument fee getter.
    :param block_identifier:
        Block at which the fee is read.
    :return:
        Fee as a fraction, such as ``0.0145`` for 1.45%.
    :raise ValueError:
        If the return value is malformed or outside the basis-point range.
    """

    result = web3.eth.call(
        {
            "to": Web3.to_checksum_address(vault_address),
            "data": selector,
        },
        block_identifier=block_identifier,
    )
    if len(result) != ABI_WORD_SIZE:
        raise ValueError(f"Unexpected Pallas fee return size for {vault_address}: {len(result)} bytes")

    raw_fee = int.from_bytes(result)
    if raw_fee > PALLAS_FEE_BPS_DENOMINATOR:
        raise ValueError(f"Invalid Pallas fee for {vault_address}: {raw_fee} BPS")
    return raw_fee / PALLAS_FEE_BPS_DENOMINATOR


class PallasVault(ERC4626Vault):
    """Pallas trading vault with asynchronous settlement.

    Management and performance fees are configured independently for each
    vault. The contract accrues them by minting shares to the fee recipient;
    application-level premium rebates are separate from these configured rates.
    """

    def get_management_fee(self, block_identifier: BlockIdentifier) -> Percent:
        """Read the vault's annual management fee from ``managementFeeBps()``.

        :param block_identifier:
            Block at which the fee is read.
        :return:
            Annual fee as a fraction.
        """
        return fetch_pallas_fee(self.web3, self.vault_address, PALLAS_MANAGEMENT_FEE_SELECTOR, block_identifier)

    def get_performance_fee(self, block_identifier: BlockIdentifier) -> Percent:
        """Read the vault's profit fee from ``performanceFeeBps()``.

        :param block_identifier:
            Block at which the fee is read.
        :return:
            Performance fee as a fraction.
        """
        return fetch_pallas_fee(self.web3, self.vault_address, PALLAS_PERFORMANCE_FEE_SELECTOR, block_identifier)

    def get_link(self, referral: str | None = None) -> str:
        """Return the strategy-specific Pallas app page when the vault is reviewed.

        Pallas exposes separate pages for the reviewed strategies. Unknown
        future deployments fall back to the vault list.

        :param referral:
            Unused because Pallas' vault pages do not provide a documented
            referral query parameter.
        :return:
            Strategy page for a reviewed deployment, otherwise the Pallas app.
        """
        del referral
        return PALLAS_VAULT_LINKS.get(self.address.lower(), "https://app.pallas.fund/")
