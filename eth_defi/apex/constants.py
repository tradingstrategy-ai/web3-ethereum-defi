"""Constants for the ApeX Omni vault reader."""

import datetime
from dataclasses import dataclass
from pathlib import Path

from eth_typing import HexAddress

#: Synthetic chain identifier for ApeX native vaults.
APEX_CHAIN_ID: int = 9995

#: ApeX Omni public REST API base URL.
APEX_API_BASE_URL: str = "https://omni.apex.exchange/api/v3"

#: Public destination URL template for an ApeX Omni user-created vault.
APEX_VAULT_URL_TEMPLATE: str = "https://omni.apex.exchange/vaultInfo/{vault_id}"

#: Official protocol vaults use a separate view selected by the trailing ``/1``.
#: See https://www.apex.exchange/blog/detail/weekly-update-11may2026.
APEX_OFFICIAL_VAULT_URL_TEMPLATE: str = "https://omni.apex.exchange/vaultInfo/{vault_id}/1"

#: Default local metrics database.
APEX_METRICS_DATABASE: Path = Path("~/.tradingstrategy/vaults/apex-vaults.duckdb").expanduser()

#: Default ranking observation cadence.
APEX_DEFAULT_SCAN_INTERVAL: datetime.timedelta = datetime.timedelta(hours=4)

#: Default historical refresh cadence.
APEX_DEFAULT_HISTORY_INTERVAL: datetime.timedelta = datetime.timedelta(hours=24)

#: Default process-wide public API request rate.
APEX_DEFAULT_REQUESTS_PER_SECOND: float = 5.0

#: Default number of history reader workers.
APEX_DEFAULT_MAX_WORKERS: int = 8

#: Default TCP connection timeout.
APEX_DEFAULT_CONNECT_TIMEOUT: float = 10.0

#: Default socket inactivity timeout.
APEX_DEFAULT_READ_TIMEOUT: float = 30.0

#: Monotonic request-attempt budget used to clamp blocking phases.
APEX_DEFAULT_REQUEST_DEADLINE: float = 60.0

#: Monotonic budget shared by the complete two-pass ranking read.
APEX_DEFAULT_RANKING_DEADLINE: float = 300.0

#: Monotonic budget shared by one vault's history operation.
APEX_DEFAULT_HISTORY_DEADLINE: float = 120.0

#: Maximum retry sleep.
APEX_DEFAULT_MAX_RETRY_DELAY: float = 10.0

#: Maximum JSON response size.
APEX_DEFAULT_MAX_RESPONSE_BYTES: int = 16 * 1024 * 1024

#: Number of HTTP retries after the initial request.
APEX_DEFAULT_RETRIES: int = 3

#: Number of complete ranking stabilisation retries.
APEX_DEFAULT_RANKING_ATTEMPTS: int = 3

#: Ranking page size verified against the public endpoint.
APEX_RANKING_PAGE_SIZE: int = 100

#: Explicitly verified terminal status.
APEX_TERMINAL_STATUS: str = "VAULT_FINISHED"

#: Public endpoint returning ApeX Omni official (protocol-operated) vaults.
#:
#: Unlike the user copy-trading vaults served by ``/vault/ranking``, the official
#: vaults are listed by ``/vault/official-vaults`` and carry small integer
#: ``vaultId`` values with distinct StarkEx L2 addresses.
APEX_OFFICIAL_VAULTS_PATH: str = "vault/official-vaults"

#: Batched net-asset-value history endpoint used for the official vaults.
#:
#: Called as ``/vault/fund-net-value-batch?vaultIds=10000,10001`` and returns a
#: daily ``netValue`` and ``totalValue`` series per official vault.
APEX_FUND_NET_VALUE_BATCH_PATH: str = "vault/fund-net-value-batch"


@dataclass(slots=True, frozen=True)
class ApexOfficialVault:
    """Curated metadata for one ApeX Omni official, protocol-operated vault.

    ApeX Omni runs a small number of official vaults built and operated by the
    ApeX team itself, as opposed to the user-created copy-trading vaults. They
    distribute exchange liquidation-fee revenue, unlike the trading and
    liquidity strategies used by Hyperliquid's HLP and Lighter's LLP.

    The ApeX official-vault API exposes placeholder ``desc`` text for these
    vaults, so the short and long descriptions here are curated from ApeX's
    official documentation and announcements. See the `Protocol Vaults
    announcement
    <https://www.apex.exchange/blog/detail/Introducing-Protocol-Vaults-on-ApeX-Omni-Stable-Returns-Backed-by-Real-Fees>`__.
    """

    #: ApeX platform vault ID (small integer namespace for official vaults).
    vault_id: str

    #: Display name reported by the ApeX official-vaults endpoint.
    name: str

    #: Distinct StarkEx L2 vault address reported by ApeX.
    reported_ethereum_address: HexAddress

    #: One-line summary shown on vault listing pages.
    short_description: str

    #: Multi-paragraph strategy description in Markdown.
    long_description: str


#: Curated metadata for the ApeX Omni official vaults.
#:
#: Addresses, IDs and total caps were captured live from
#: ``/vault/official-vaults`` on 2026-07-25. Descriptions are sourced from the
#: ApeX `Protocol Vaults announcement
#: <https://www.apex.exchange/blog/detail/Introducing-Protocol-Vaults-on-ApeX-Omni-Stable-Returns-Backed-by-Real-Fees>`__,
#: the `Protocol Vault guide
#: <https://apex-pro.gitbook.io/apex-pro/apex-omni/protocol-vaults>`__
#: (NAV timing verified 2026-10-06), and the `New User Vault weekly update
#: <https://www.apex.exchange/blog/detail/weekly-update-11may2026>`__.
APEX_OFFICIAL_VAULTS: tuple[ApexOfficialVault, ...] = (
    ApexOfficialVault(
        vault_id="10000",
        name="Protocol Vault",
        reported_ethereum_address=HexAddress("0x83F0e6dC9C352F9156a3a06B512B58012629D05C"),
        short_description="ApeX Omni's flagship protocol-operated vault, distributing USDT liquidation-fee revenue with no lock-up.",
        long_description=(
            "The Protocol Vault is ApeX Omni's flagship official vault, built and operated by the ApeX "
            "team itself rather than a third-party strategy manager. It distributes exchange fee "
            "revenue; Hyperliquid's HLP and Lighter's LLP instead manage trading and liquidity "
            "positions, so their investment mechanics differ.\n\n"
            "Depositors pool USDT and earn a proportional share of the perpetual-futures liquidation "
            "fees collected across the ApeX Omni exchange. ApeX totals these fees daily and distributes "
            "them to vault holders through a net asset value (NAV) update at 08:05 UTC, so reported "
            "yield comes from exchange activity rather than a depositor trading strategy.\n\n"
            "Key parameters published by ApeX:\n\n"
            "- Denominated in USDT\n"
            "- Minimum purchase above 10 USDT\n"
            "- Total vault cap raised over time to around 20,000,000 USDT, with a per-user cap raised "
            "from 200,000 USDT up to around 1,000,000 USDT\n"
            "- Daily NAV update at 08:05 UTC; shares are issued at the next NAV after a deposit\n"
            "- No lock-up: principal and accrued yield are redeemable back to the depositing account\n\n"
            "Realised yield varies with exchange liquidation activity. The protocol's advertised "
            "return and safety claims are issuer statements, not independent guarantees."
        ),
    ),
    ApexOfficialVault(
        vault_id="10001",
        name="New Vault",
        reported_ethereum_address=HexAddress("0xf33561bA75bE787bA53914C2bfE9A7A7DD320B8a"),
        short_description="ApeX Omni's new-user protocol vault: a simplified, lower-capacity entry point earning the same liquidation-fee yield.",
        long_description=("The New Vault (New User Vault) is a second official ApeX Omni vault, also built and operated by the ApeX team, aimed at newcomers to the platform. It offers a simplified, direct entry point so users can start earning from their first day without navigating the full ApeX product suite.\n\nLike the flagship Protocol Vault, it pays USDT yield from ApeX Omni perpetual liquidation fees, distributed to holders through daily NAV updates, and carries no lock-up: principal and yield are redeemable back to the depositing account.\n\nIt differs from the flagship Protocol Vault mainly in scope. It is an onboarding product with a smaller total capacity of around 300,000 USDT targeted at new and smaller depositors, rather than the exchange's primary, higher-capacity protocol vault. As with all ApeX Protocol Vaults, realised yield depends on exchange liquidation activity and no fixed rate is guaranteed."),
    ),
)
