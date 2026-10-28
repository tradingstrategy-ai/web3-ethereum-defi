"""Reviewed Antarctic Arbitrum deployments.

Creation transactions were resolved with the explorer contract-creation API,
receipts and cache-aware Hypersync timestamps on 2026-09-30.
See https://www.antarctic.exchange/lp/amlp and the ABI source README.
"""

import datetime
from dataclasses import dataclass

from eth_typing import HexAddress

ANTARCTIC_CHAIN_ID = 42161
ANTARCTIC_USDT = HexAddress("0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9")


@dataclass(slots=True, frozen=True)
class AntarcticDeployment:
    """Identify one reviewed LP token and its separate settlement source."""

    #: Application product identifier.
    product: str
    #: LP token, used as the vault identity.
    address: HexAddress
    #: Standalone manager emitting settlement logs.
    manager: HexAddress
    #: Token creation block.
    deployment_block: int
    #: Token creation timestamp in naive UTC.
    deployed_at: datetime.datetime
    #: Manager creation block, the historical event lower bound.
    manager_deployment_block: int
    #: Token creation transaction.
    creation_transaction: str
    #: Manager creation transaction.
    manager_creation_transaction: str


ANTARCTIC_DEPLOYMENTS = (
    AntarcticDeployment("amlp", HexAddress("0x152f5e6142db867f905a68617dbb6408d7993a4b"), HexAddress("0x98a6aee58699e4f4e13d8d8d0800e4e9cbbcf8dd"), 291176730, datetime.datetime(2025, 1, 2, 10, 17, 16), 291176746, "0xa1e6b0dae8099ed8f328ec4d6363005595faa2ebb41694876103fbad618457cd", "0x5843fc6ec0830799f50b0e407c53c8d6bea590afe85d2ee370ca1df896110d57"),  # noqa: DTZ001
    AntarcticDeployment("ahlp", HexAddress("0x5fd22da8315992dbbd82d5ac1087803ff134c2c4"), HexAddress("0xc5f9d4b9f68caaa869317baa09a233b22940bd9f"), 357654635, datetime.datetime(2025, 7, 14, 15, 40, 38), 357654676, "0x262c3c8ddfe97d2d5d40d9f7ab2c47772c34964b6820e8adee73c61bff721997", "0x790b02a87155de4eced234dff0dddfd40f51ea0a06345b406db1cdf1a6615eff"),  # noqa: DTZ001
)

#: Lowercase keys; every adapter also checks the chain identity.
ANTARCTIC_BY_ADDRESS = {deployment.address: deployment for deployment in ANTARCTIC_DEPLOYMENTS}
ANTARCTIC_HARDCODED_LEADS = tuple((ANTARCTIC_CHAIN_ID, d.address, d.deployment_block, d.deployed_at) for d in ANTARCTIC_DEPLOYMENTS)
