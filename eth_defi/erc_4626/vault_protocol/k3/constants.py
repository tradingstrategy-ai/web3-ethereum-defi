"""Reviewed K3 deployments from the official registry and verified source.

https://github.com/K3-Capital/k3-vault-contracts/tree/main/deployments
Includes the reviewed Amphor-backed MetaVaultWrapper deployments on Base
and Monad. Test deployments are excluded.
"""

import datetime

#: Canonical proxy addresses, scoped to their deployment chain.
K3_VAULTS: frozenset[tuple[int, str]] = frozenset(
    {
        (1, "0x009c02a73706a68e0ae0209235408206e4f53709"),
        (5042, "0x56fb80282bb565de67c196924dda0138e30323be"),
        (1, "0x333333c5def8368c3fcc9c378cedc0f709fa5ecb"),
        (8453, "0xf508f0096b966f657a6bee55b341e9309d5871fb"),
        (143, "0x37aef9d5b058382024ffb5c08e2f4c9ca4978751"),
    }
)
#: Reviewed Amphor-backed wrappers, verified implementation source:
#: https://basescan.org/address/0x8c4f97ce5b2eea713728729aec08e48bce588216#code
#: https://monadscan.com/address/0x1cca0fc4ce416893fe5e74039c4c81a4d10e6806#code
K3_METAVAULTS = frozenset(
    {
        (8453, "0xf508f0096b966f657a6bee55b341e9309d5871fb"),
        (143, "0x37aef9d5b058382024ffb5c08e2f4c9ca4978751"),
    }
)
#: Only SmartAccountWrapper deployments have first-party JSON records.
K3_REGISTRY_VAULTS = K3_VAULTS - K3_METAVAULTS

#: First-party registry with deployment identity and historical wiring evidence.
K3_REGISTRY_URL = "https://raw.githubusercontent.com/K3-Capital/k3-vault-contracts/main/deployments"

#: Registry deployment receipts for direct wrappers; first observed deposit
#: blocks from the local scanner snapshot for Base and Monad (not creation dates).
#: Timestamps use naive UTC.
K3_HARDCODED_LEADS = (
    (1, "0x333333c5def8368c3fcc9c378cedc0f709fa5ecb", 26_192_428, datetime.datetime(2026, 10, 14, 18, 6, 47)),  # noqa: DTZ001 - naive UTC
    (8453, "0xf508f0096b966f657a6bee55b341e9309d5871fb", 40_712_182, datetime.datetime(2026, 1, 12, 10, 28, 31)),  # noqa: DTZ001 - naive UTC
    (143, "0x37aef9d5b058382024ffb5c08e2f4c9ca4978751", 97_904_009, datetime.datetime(2026, 8, 21, 12, 10, 3)),  # noqa: DTZ001 - naive UTC
    (1, "0x009c02a73706a68e0ae0209235408206e4f53709", 25_680_513, datetime.datetime(2026, 8, 4, 9, 0, 35)),  # noqa: DTZ001 - pipeline uses naive UTC
    (5042, "0x56fb80282bb565de67c196924dda0138e30323be", 22_493_034, datetime.datetime(2026, 9, 24, 9, 1, 0)),  # noqa: DTZ001 - pipeline uses naive UTC
)
