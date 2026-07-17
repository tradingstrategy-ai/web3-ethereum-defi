"""Known wstGBP deployments used by the vault scanner."""

import datetime
from dataclasses import dataclass

from eth_typing import HexAddress


@dataclass(slots=True, frozen=True)
class WSTGBPDeployment:
    """Scanner metadata for a wstGBP tokenised asset.

    The primary wstGBP contract is also the ERC-20 share token. It is the
    address the shared vault discovery and historical-reader pipelines use.
    """

    #: EVM chain identifier.
    chain_id: int

    #: wstGBP token and vault address.
    vault: HexAddress

    #: First block where the token bytecode exists.
    first_seen_at_block: int

    #: Timestamp of :attr:`first_seen_at_block` as a naive UTC datetime.
    first_seen_at: datetime.datetime


#: Ethereum mainnet chain id.
ETHEREUM_CHAIN_ID = 1

#: wstGBP deployment on Ethereum.
#:
#: Deployment transaction:
#: https://etherscan.io/tx/0x8f035ef7b0e678a54b63ad16f23249ae9583fb66cc34fd49bd3771f0ba8a07f0
WSTGBP = WSTGBPDeployment(
    chain_id=ETHEREUM_CHAIN_ID,
    vault=HexAddress("0x57c3571f10767e49c9d7b60feb6c67804783b7ae"),
    first_seen_at_block=24_852_292,
    first_seen_at=datetime.datetime(2026, 4, 10, 22, 24, 23, tzinfo=datetime.UTC).replace(tzinfo=None),
)

#: Hardcoded wstGBP scanner leads.
#:
#: wstGBP mint and redeem events are not ERC-4626 ``Deposit`` and
#: ``Withdraw`` events, so event-based discovery cannot find this vault.
WSTGBP_HARDCODED_LEADS = (
    (
        WSTGBP.chain_id,
        WSTGBP.vault,
        WSTGBP.first_seen_at_block,
        WSTGBP.first_seen_at,
    ),
)
