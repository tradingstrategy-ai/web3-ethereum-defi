Shift
=====

`Shift <https://shiftprotocol.xyz/>`__ provides automated USDC yield strategies
using perpetual-DEX funding, basis-trading and stablecoin positions. The
reviewed vaults are custom ERC-20 ``ShiftVault`` share contracts: they do not
implement ERC-4626.

The integration uses a chain-aware hardcoded registry for the published
``ltPARA`` and ``extUSD`` Base vaults and the ``ltLLP`` Arbitrum vault. This
lets discovery track deployments that cannot be identified through ERC-4626
event or interface probes. Its historical reader combines
``getSharePrice()`` from Shift's TVL feed with ERC-20 total supply to calculate
TVL. A zero share price denotes a stale feed, not a reliable zero-TVL valuation.

Dealing and risks
-----------------

Shift vaults are DeFi vaults, rather than legally structured tokenised funds.
Investor admission follows each contract's ``whitelistEnabled()`` setting:
when disabled, any account can request a deposit. Administrators can enable
the allow-list later, and individual membership has no public getter.

Deposit requests need oracle valuation validation before completion.
Withdrawals are executor-batch resolved and are subject to a timelock measured
from the withdrawal request. These settlement dependencies do not establish
investor KYC or private admission. The adapter supports valuation reads;
transaction automation remains unimplemented for both directions.
Fees are read from the vault
and represented as treasury-minted fee shares. Shift's
`FAQ <https://shiftprotocol.gitbook.io/shift/resources/faq>`__ documents
strategy-dependent 0–2% annual management fees, 0–20% performance fees and
withdrawal processing that may take up to seven days.

Classification repair
---------------------

Older metadata incorrectly labelled all three reviewed Shift deployments as
tokenised funds with whitelisted deposits. The metadata-only repair validates
the exact chain/address pairs and reads current admission and closure policy.
It preserves unrelated metadata, historical prices, reader state and timestamp
caches. Writes take the scanner lock, back up the existing pickle and replace
it atomically. Repeated runs with unchanged policy do not rewrite the file.

Use the scanner's existing environment and mounted state. In a local checkout,
source ``.local-test.env`` first; production maintenance must use the mounted
state and configuration described in the repository's production instructions.

.. code-block:: shell

    DRY_RUN=true poetry run python scripts/erc-4626/fix-shift-vault-classification.py
    DRY_RUN=false poetry run python scripts/erc-4626/fix-shift-vault-classification.py

``VAULT_DB_PATH`` can select a copy of the metadata database for review.
After applying the repair, refresh the public report using the existing
exporter with the same pipeline data directory (``DATA_DIR``). Force the three
Shift records through its metrics cache so the correction appears immediately:

.. code-block:: shell

    poetry run python - <<'PY'
    from eth_defi.erc_4626.vault_protocol.shift.constants import SHIFT_VAULT_PRODUCTS
    from eth_defi.vault.base import VaultSpec
    from eth_defi.vault.top_vaults_json import main

    main(force_vault_ids={VaultSpec(chain_id, address).as_string_id() for chain_id, address in SHIFT_VAULT_PRODUCTS})
    PY

No historical rescan is required. This refresh leaves the scanner's reader
state and historical observations intact.

.. autosummary::
   :toctree: _autosummary_shift
   :recursive:

   eth_defi.erc_4626.vault_protocol.shift.constants
   eth_defi.erc_4626.vault_protocol.shift.descriptions
   eth_defi.erc_4626.vault_protocol.shift.vault
   eth_defi.erc_4626.vault_protocol.shift.historical
