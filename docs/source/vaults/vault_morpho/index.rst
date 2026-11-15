.. _vault-morpho-unattributed:

Unattributed VaultMorpho implementation
=======================================

``VaultMorpho`` is the Solidity name of a capped ERC-4626 implementation that
allocates assets to Morpho Blue markets. Its owning protocol and upstream GitHub
repository have not been identified. It is not the canonical MetaMorpho source,
and calling ``MORPHO()`` does not make it an officially attributed Morpho vault.

Source and deployment evidence
------------------------------

The reviewed Ethereum vault is
``0x21ed44c18c926c60092b1b2985e2c999421a5a69``. At block ``26402309`` it used
implementation ``0xf392a5b96d3248440bb4841b4f50f0862060c1a3``. The implementation's
`verified Solidity source
<https://etherscan.io/address/0xf392a5b96d3248440bb4841b4f50f0862060c1a3#code>`__
and ABI were retrieved on 13 November 2026. ``eth_defi/abi/vault_morpho/README.md``
records their provenance; an upstream GitHub link remains unavailable rather
than being substituted with canonical MetaMorpho's different contract source.

Read-only checks on 14 November 2026 found owner
``0x1C742c63C348CE9918B671c4002B6c7e5aB83B4a`` at both blocks ``26402309`` and
``26414947``. Explorer creation records identify that address as the direct
creator of both the vault and implementation. The vault's ``MORPHO()`` points
to Morpho Blue at ``0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb``. These facts
establish the lending dependency and deployment control; they do not identify
a product operator or confer Morpho's protocol attribution. The existing
unofficial warning remains in force.

Feature detection and attribution
---------------------------------

The existing multicall classifier adds ``vault_morpho_like`` when both
``MORPHO()`` and ``tvlCap()`` succeed and the latter returns one 32-byte word.
The ``morpho_like`` compatibility flag remains available to lending-capability
consumers. The specific flag takes precedence in the Morpho branch of the
factory and in protocol attribution. It selects
:py:class:`~eth_defi.erc_4626.vault_protocol.vault_morpho.vault.VaultMorphoVault`
and returns the existing generic protocol label ``ERC-4626``.

The adapter derives from generic ERC-4626 support, not ``MorphoV1Vault``.
It therefore does not inherit a Morpho app link, Morpho API warning enrichment,
canonical risk/fee defaults, a permissionless admission claim or an instant
withdrawal estimate. Those values remain unknown unless established through
an independent source. The address-scoped unofficial warning is retained.
The implementation name is not invented as a new protocol brand.

The verified lending behaviour is retained: the adapter keeps its lending
strategy tag, idle-balance liquidity measurement and utilisation fraction. Its
historical reader adds the same balance measurement to the generic ERC-4626
price reads without assuming any fee schedule. Idle assets exclude liquidity
that would first need to be withdrawn from an underlying market.

Deposit capacity
----------------

The verified ``deposit()`` rejects a request when
``totalAssets() + assets > tvlCap()``. Its ``maxDeposit()`` derives capacity from
underlying market allocations and can stay positive after this additional cap
is full. The selected reader calculates::

    remaining = max(0, tvlCap - totalAssets)

Both inputs are raw denomination-token units, not shares or a USD valuation.
They use ``default_block_identifier`` when supplied; otherwise the adapter
selects one numeric block for both reads. The deposit manager honours the
tighter of this remainder and any advertised ERC-4626 maximum through the
existing capacity hook. Zero remainder means global closure; a smaller positive
remainder means the requested amount is too large.

This rule covers the supported ``deposit()`` path. It does not assume that
``mint()`` or every entrypoint has the same check. Normal onchain validation
still determines whether an account can trade. A prior Guard-only closure check
at block ``26402309`` did not execute a deposit or establish live trade support.

Persisted metadata and maintenance
----------------------------------

The prepared, unapplied migration
``migration/scripts/075-vault-pause-cap-and-classification.py`` re-detects the
reviewed address and saved VaultMorpho-family candidates on their own chains.
The implementation-name screen only finds candidates; the cap feature must
still be detected before a candidate is reclassified.

The refresh changes attribution-dependent fields, including the protocol/link,
fees, permissions, withdrawal estimates and Morpho API enrichment. It retains
explicit address warnings, NAV, shares, discovery boundaries and historical
prices. Protocol-based selection can change after correcting the wrong
attribution; that is a data correction, not grounds to retain a false Morpho
label. No strategy backtest or funded validation is implied by this repair.

Regression coverage in ``tests/erc_4626/test_classification.py`` and
``tests/erc_4626/vault_protocol/test_remaining_vault_preflights.py`` checks
feature combinations, separate adapter selection, generic attribution, absence
of inherited Morpho metadata, the tighter cap and exhausted capacity.
``tests/vault/test_pause_cap_metadata_migration.py`` checks metadata correction
and economic-history preservation, including another chain's saved candidate.

API
---

.. autosummary::
   :toctree: _autosummary_vault_morpho_unattributed
   :recursive:

   eth_defi.erc_4626.vault_protocol.vault_morpho.vault

Permission inspection
---------------------

The adapter now verifies the EIP-1967 implementation before classifying access.
At Ethereum block 26402309 the four reviewed proxies use implementations
``0xf392a5b96d3248440bb4841b4f50f0862060c1a3``,
``0xfa0d6e598d13e9220e5de48db4ed087c8f8847b3``,
``0x816dcddcb7949bd8556058b47e2f638b163d658d`` and
``0x13d8602fa8dc916704194ec25bf73bffd6b39fed``. Their verified Solidity
source is identical (SHA-256
``4707c3641938c3d30f3c753de36a5a0ffe973a40a52f35bfbb067cb36308bf4c``).
The reviewed deposit, mint, transfer, withdraw and redeem paths have no account
allowlist. Administrative curator/allocator roles do not restrict depositors.
An unreviewed implementation remains unknown after an upgrade.

``is_permissionless(owner)`` is an opt-in complete-route policy check. It does
not certify capacity, fees or successful execution. Existing cap checks and
protocol attribution remain separate. Migration 078 refreshes only the six
reviewed permission records, including two stale canonical Morpho V2 records;
prices and discovery data are preserved. Production application and normal
export publication remain operator actions. No price backfill is needed.

The permission check is an implementation-version support boundary, not a Safe
or depositor allowlist. ``False`` means no whitelist is required; an unreviewed
implementation or chain raises ``NotImplementedError`` so the scanner records
unknown policy. It must not be interpreted as a proven account restriction.

The proxy-to-implementation mapping and verified source links are recorded in
``eth_defi/abi/vault_morpho/README.md``. ``fetch_implementation()`` reads the
standard `ERC-1967 logic-contract slot <https://eips.ethereum.org/EIPS/eip-1967#logic-contract-address>`__
at the chosen block. The slot is derived from the standard label instead of an
unexplained hexadecimal literal. The proxy need not expose an ABI getter for
this storage value; ordinary contract calls continue to use ABI-bound methods.
