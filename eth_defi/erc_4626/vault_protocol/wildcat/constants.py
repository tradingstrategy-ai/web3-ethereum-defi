"""Reviewed Wildcat Finance deployment constants.

Wildcat registers every lending market in its ``WildcatArchController``
registry. Version 2 markets are deployed by the ``HooksFactory``, which is the
indexed ``controller`` of their ``MarketAdded`` registry events.

Addresses come from the
`contract deployments page <https://github.com/wildcat-finance/wildcat-docs/blob/main/technical-overview/contract-deployments.md>`__
and the `SDK constants <https://github.com/wildcat-finance/wildcat.ts/blob/main/src/constants.ts>`__,
reviewed on 2026-11-10.
"""

import dataclasses
import datetime

from eth_typing import HexAddress

#: Ethereum mainnet chain id. Version 2 Ethereum markets are the reviewed scope.
WILDCAT_ETHEREUM_CHAIN_ID = 1

#: ``MarketAdded(address indexed controller, address market)`` signature emitted
#: by the ``WildcatArchController`` registry.
WILDCAT_MARKET_ADDED_EVENT_SIGNATURE = "MarketAdded(address,address)"

#: Scale factor precision. Wildcat stores ``scaleFactor`` in ray units.
#:
#: https://github.com/wildcat-finance/v2-protocol/blob/main/docs/Scale%20Factor.md
WILDCAT_RAY = 10**27

#: Interval between price reads, in the live scanner and the price backfill.
# Lending markets do not move fast, so daily reads avoid unnecessary churn.
WILDCAT_READ_INTERVAL = datetime.timedelta(days=1)

#: Borrowers whose markets are Wildcat's own tests, flagged ``unofficial``.
#:
#: Wildcat Labs' borrower profile (alias ``[TEST BORROWER]``) says its markets
#: are "for the explicit purpose of testing-in-prod by Wildcat Labs, and should
#: not be interacted with". Reviewed on 2026-11-10 from
#: https://app.wildcat.finance/api/profiles/0x569e7cb1a1c839133012de4adee8361389b0113b?chainId=1
WILDCAT_TEST_BORROWERS: frozenset[str] = frozenset({"0x569e7cb1a1c839133012de4adee8361389b0113b"})

#: ``version()`` return value of a version 2 market.
WILDCAT_V2_MARKET_VERSION = "2"

#: ``version()`` return value of the fixed-term hooks template.
WILDCAT_FIXED_TERM_HOOKS_VERSION = "FixedTermHooks"

#: ``version()`` return value of the open-term hooks template.
WILDCAT_OPEN_TERM_HOOKS_VERSION = "OpenTermHooks"


@dataclasses.dataclass(slots=True, frozen=True)
class WildcatDeployment:
    """Reviewed Wildcat registry deployment for one chain."""

    #: EVM chain id.
    chain_id: int
    #: ``WildcatArchController`` registry address.
    arch_controller: HexAddress
    #: Version 2 ``HooksFactory`` address, the controller of V2 markets.
    hooks_factory: HexAddress
    #: ``WildcatArchController`` deployment block.
    first_block: int


#: Reviewed Wildcat deployments keyed by chain id.
WILDCAT_DEPLOYMENTS: dict[int, WildcatDeployment] = {
    WILDCAT_ETHEREUM_CHAIN_ID: WildcatDeployment(
        chain_id=WILDCAT_ETHEREUM_CHAIN_ID,
        arch_controller=HexAddress("0xfEB516d9D946dD487A9346F6fee11f40C6945eE4"),
        hooks_factory=HexAddress("0xdd7dd3b5076cf89440d05585ff56d246386207be"),
        first_block=18_686_645,
    ),
}
