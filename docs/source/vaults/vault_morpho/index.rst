.. _vault-morpho-unattributed:

Unattributed VaultMorpho implementation
======================================

``VaultMorpho`` is the Solidity name of a capped ERC-4626 implementation that
allocates assets to Morpho Blue markets. Its owning protocol and upstream GitHub
repository have not been identified. It is not the canonical MetaMorpho source,
and calling ``MORPHO()`` does not make it an officially attributed Morpho vault.

Source and deployment evidence
-----------------------------

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
---------------------------------

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
