"""CCTP whitelisting for Lagoon vaults.

Utilities for whitelisting CCTP contracts in Guard contracts
for cross-chain USDC transfers through managed vaults.

When a Lagoon vault needs to perform cross-chain USDC transfers,
the guard contract must whitelist:

1. The TokenMessengerV2 contract (for ``depositForBurn()`` calls)
2. Each allowed destination domain (CCTP domain IDs)
3. USDC as an allowed asset (via ``whitelistToken()``)
4. The vault/Safe address as an allowed receiver

Example::

    from eth_defi.cctp.whitelist import CCTPDeployment

    cctp = CCTPDeployment.create_for_chain(
        chain_id=1,  # Ethereum
        allowed_destinations=[42161, 8453],  # Arbitrum, Base
    )

    # Pass to Lagoon vault deployment
    deploy_automated_lagoon_vault(
        ...
        cctp_deployment=cctp,
    )
"""

import logging
from dataclasses import dataclass, field

from eth_typing import HexAddress
from hexbytes import HexBytes
from web3 import Web3
from web3.contract import Contract

from eth_defi.cctp.constants import (
    CHAIN_ID_TO_CCTP_DOMAIN,
    CCTP_DOMAIN_NAMES,
    MESSAGE_TRANSMITTER_V2,
    TOKEN_MESSENGER_V2,
    TOKEN_MINTER_V2,
)
from eth_defi.trace import assert_transaction_success_with_explanation

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CCTPDeployment:
    """CCTP V2 deployment configuration for guard whitelisting.

    All CCTP V2 contracts share the same address across EVM chains (CREATE2),
    so this mainly configures which destination domains are allowed.
    """

    #: TokenMessengerV2 contract address
    token_messenger: HexAddress = HexAddress(TOKEN_MESSENGER_V2)

    #: MessageTransmitterV2 contract address
    message_transmitter: HexAddress = HexAddress(MESSAGE_TRANSMITTER_V2)

    #: TokenMinterV2 contract address
    token_minter: HexAddress = HexAddress(TOKEN_MINTER_V2)

    #: CCTP domain IDs of allowed destination chains.
    #: Use :data:`eth_defi.cctp.constants.CHAIN_ID_TO_CCTP_DOMAIN` to convert
    #: chain IDs to domain IDs.
    allowed_destination_domains: list[int] = field(default_factory=list)

    @classmethod
    def create_for_chain(
        cls,
        chain_id: int,
        allowed_destinations: list[int] | None = None,
    ) -> "CCTPDeployment":
        """Create a CCTP deployment configuration for a given chain.

        :param chain_id:
            EVM chain ID of the **source** chain (e.g. 1 for Ethereum).

        :param allowed_destinations:
            List of **EVM chain IDs** for allowed destination chains.
            Automatically converted to CCTP domain IDs.
            If ``None``, no destinations are allowed (add them separately).

        :return:
            Configured :class:`CCTPDeployment` instance.

        :raises ValueError:
            If the source chain or a destination chain is not supported.
        """
        if chain_id not in CHAIN_ID_TO_CCTP_DOMAIN:
            raise ValueError(f"Chain {chain_id} is not supported by CCTP. Supported chains: {list(CHAIN_ID_TO_CCTP_DOMAIN.keys())}")

        domains = []
        if allowed_destinations:
            for dest_chain_id in allowed_destinations:
                domain = CHAIN_ID_TO_CCTP_DOMAIN.get(dest_chain_id)
                if domain is None:
                    raise ValueError(f"Destination chain {dest_chain_id} is not supported by CCTP. Supported chains: {list(CHAIN_ID_TO_CCTP_DOMAIN.keys())}")
                domains.append(domain)

        return cls(allowed_destination_domains=domains)


def setup_cctp_whitelisting(
    web3: Web3,
    guard: Contract,
    cctp_deployment: CCTPDeployment,
    owner: HexAddress | str,
) -> list[HexBytes]:
    """Whitelist CCTP contracts in a guard for cross-chain transfers.

    Calls the guard's ``whitelistCCTP()`` and ``whitelistCCTPDestination()``
    functions to enable cross-chain USDC transfers.

    :param web3:
        Web3 connection

    :param guard:
        Guard contract (TradingStrategyModuleV0 or similar)

    :param cctp_deployment:
        CCTP deployment configuration

    :param owner:
        Address of the guard owner (typically the Safe)

    :return:
        List of transaction hashes
    """
    tx_hashes = []

    # Whitelist TokenMessengerV2
    logger.info(
        "Whitelisting CCTP TokenMessenger: %s",
        cctp_deployment.token_messenger,
    )
    tx_hash = guard.functions.whitelistCCTP(
        Web3.to_checksum_address(cctp_deployment.token_messenger),
        "Allow CCTP cross-chain transfers",
    ).transact({"from": owner})
    assert_transaction_success_with_explanation(web3, tx_hash)
    tx_hashes.append(tx_hash)

    # Whitelist each destination domain
    for domain in cctp_deployment.allowed_destination_domains:
        domain_name = CCTP_DOMAIN_NAMES.get(domain, f"domain {domain}")
        logger.info(
            "Whitelisting CCTP destination domain: %d (%s)",
            domain,
            domain_name,
        )
        tx_hash = guard.functions.whitelistCCTPDestination(
            domain,
            f"CCTP destination: {domain_name}",
        ).transact({"from": owner})
        assert_transaction_success_with_explanation(web3, tx_hash)
        tx_hashes.append(tx_hash)

    logger.info(
        "CCTP whitelisting complete: %d destination(s)",
        len(cctp_deployment.allowed_destination_domains),
    )
    return tx_hashes
