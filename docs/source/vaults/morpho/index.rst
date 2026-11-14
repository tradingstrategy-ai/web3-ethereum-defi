Morpho API
----------

`Morpho protocol <https://morpho.org/>`__ integration.

Morpho is a decentralised lending protocol. Its vaults allocate deposited
assets to lending markets or other configured allocations. The library
distinguishes two vault architectures:

**Morpho V1 (MetaMorpho)**: The original Morpho vault architecture that directly
integrates with Morpho markets. Vaults expose the ``MORPHO()`` function for identification.

**Morpho V2**: A newer adapter-based architecture that enables flexible allocation
across multiple yield sources through smart contract adapters. V2 vaults use
``adaptersLength()`` for identification and support:

- Multi-protocol yield allocation via adapters
- Granular ID & Cap system for risk management
- Performance fees (up to 50%) and management fees (up to 5%)
- Timelocked governance with curator/allocator roles
- Non-custodial exits via ``forceDeallocate``

.. _morpho-vault-classification:

Vault features and adapter selection
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This is the canonical guide to the Morpho-related
:py:class:`~eth_defi.erc_4626.core.ERC4626Feature` flags. Features describe
interfaces and select contract readers; they do not establish who operates a
vault, whether an investor may use it, or whether a trade can settle.

:py:func:`~eth_defi.erc_4626.classification.identify_vault_features` combines
the existing multicall probe results. The Morpho-specific rules are:

.. list-table:: Morpho interface detection
   :header-rows: 1
   :widths: 24 38 38

   * - Features
     - Probe result
     - Selected adapter
   * - ``morpho_like`` without ``vault_morpho_like``
     - ``MORPHO()`` succeeds; ``tvlCap()`` fails or is not one 32-byte word.
     - :py:class:`~eth_defi.erc_4626.vault_protocol.morpho.vault_v1.MorphoV1Vault`.
   * - ``morpho_like`` and ``vault_morpho_like``
     - ``MORPHO()`` succeeds and ``tvlCap()`` succeeds with a 32-byte result.
     - :py:class:`~eth_defi.erc_4626.vault_protocol.morpho.vault_morpho.VaultMorphoVault`.
   * - ``morpho_v2_like``
     - ``adaptersLength()`` succeeds and ``MORPHO()`` fails.
     - :py:class:`~eth_defi.erc_4626.vault_protocol.morpho.vault_v2.MorphoV2Vault`.

Canonical MetaMorpho exposes ``MORPHO()`` as the address of its underlying
Morpho Blue lending contract. Compatible implementations can expose the same
getter while changing deposit rules. Consequently, a successful probe is an
interface signal, not verification of the deployed bytecode. Other detected
features may coexist with these flags.

The shared factory,
:py:func:`~eth_defi.erc_4626.classification.create_vault_instance`, checks the
VaultMorpho flag before the ordinary V1 flag; protocol features matched earlier
in the factory retain precedence over this Morpho-specific branch. Both flags
are retained: ``morpho_like`` supplies lending-protocol classification used by
liquidity/utilisation readers and the existing protocol-name lookup, while
ignoring ``vault_morpho_like`` would select a reader that misses its cap.
Do not duplicate this selection in scanners, strategies or CLI consumers.

Why VaultMorpho needs a different reader
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The reviewed Solidity contract is named ``VaultMorpho``. This name describes
the implementation handled by the extra feature, rather than another catalogue
protocol. Its verified ``deposit()`` rejects a request when the sum of
``totalAssets()`` and the requested assets exceeds ``tvlCap()``. Its
``maxDeposit()`` instead derives capacity from its underlying market allocations,
so that reported maximum can remain positive when the vault-wide cap is full.
A positive ERC-4626 maximum alone therefore misses a real deposit restriction.

:py:class:`~eth_defi.erc_4626.vault_protocol.morpho.vault_morpho.VaultMorphoVault`
reuses the V1 integration and binds the verified ``VaultMorpho.json`` ABI.
Its capacity reader calculates::

    remaining = max(0, tvlCap - totalAssets)

Both inputs use the adapter's ``default_block_identifier`` when supplied;
otherwise the reader selects one numeric block for both calls. Values are raw
denomination-token units, not share units or a USD price estimate. The deposit
manager applies the tighter of this remaining capacity and any reported
ERC-4626 maximum through the existing capacity hook. Zero remaining capacity
produces a global closure reason; a positive remainder below the requested size
is an amount restriction. These reads are preflights: the eventual transaction
still determines permission, funding and execution success. This rule documents
the supported ``deposit()`` path, not an assumed cap on every contract entrypoint.

:py:func:`~eth_defi.erc_4626.core.get_vault_protocol_name` continues to return
``Morpho`` for the combined feature set. The catalogue slug therefore remains
``morpho``, preserving existing fee-mode, risk and protocol-filter lookups. The
additional flag changes adapter selection without introducing a new protocol
identity or applying a current cap retrospectively to historical membership.
Keeping that identity does not certify a compatible implementation's safety.

Verified deployment and maintenance
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The integration was checked on 14 November 2026 using Ethereum block
``26402309`` and vault ``0x21ed44c18c926c60092b1b2985e2c999421a5a69``. At that
snapshot its implementation was
``0xf392a5b96d3248440bb4841b4f50f0862060c1a3``. The exhausted global cap
explained a deposit refusal despite a positive advertised maximum. The resulting
closed-deposit Guard check did not execute a deposit or prove live trade support.
The snapshot is evidence for this implementation, not a claim about its present
availability or an allowlist limiting detection to this address.

The stored ABI comes from that `verified VaultMorpho implementation
<https://etherscan.io/address/0xf392a5b96d3248440bb4841b4f50f0862060c1a3#code>`__.
Canonical reference contracts are `MetaMorpho
<https://github.com/morpho-org/metamorpho/blob/87ee5faf910d7224cee70120a94b0921c15ecab7/src/MetaMorpho.sol>`__ and
`Morpho Vault V2
<https://github.com/morpho-org/vault-v2/blob/bcad37068507dba4193f79c48d539b241faa7f37/src/VaultV2.sol>`__.
These are different sources: the capped implementation must not be treated as
canonical MetaMorpho merely because both expose ``MORPHO()``. ABI retrieval
provenance is recorded in ``eth_defi/abi/morpho/README.md``. The capped
implementation's own upstream GitHub repository has not been identified; the
canonical MetaMorpho link is a reference for the common interface, not evidence
for the added cap. Do not substitute that link for the verified capped source.

Regression coverage checks the combined flags, ordinary MetaMorpho without the
cap getter, and an unrelated cap getter without ``MORPHO()`` in
``tests/erc_4626/test_classification.py``. The factory, tighter capacity and zero
capacity behaviour are covered in
``tests/erc_4626/vault_protocol/test_remaining_vault_preflights.py``. When changing
these predicates or their interpretation, update the canonical guide and both
feature comments together. Persisted feature corrections use numbered migration
``migration/scripts/075-vault-pause-cap-and-classification.py``; merging code
does not execute it.

Metadata requests
~~~~~~~~~~~~~~~~~

Warning and curator enrichment uses
:py:mod:`eth_defi.erc_4626.vault_protocol.morpho.session` for a shared request
budget and persisted provider cooldown. Existing warning data survives a
transient API failure; a successful refresh can clear obsolete warnings.

Deposit permissions
~~~~~~~~~~~~~~~~~~~

Canonical MetaMorpho V1 deposits are permissionless; compatible implementations
still need their own admission assessment. Morpho V2 is also permissionless
when ``receiveSharesGate`` and ``sendAssetsGate`` are both zero. A V2
deployment may configure custom implementations of the `Morpho gate interfaces
<https://github.com/morpho-org/vault-v2/blob/main/src/interfaces/IGate.sol>`__
for the share receiver and asset sender independently. The adapter therefore
reports a zero-gate deployment as permissionless and leaves an unrecognised
custom gate unknown instead of assuming either KYC or public access.

Robinhood Chain temporary API workaround
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Robinhood Chain (ID ``4663``) has active Morpho V2 vaults whose public API
coverage is currently incomplete. Until the API returns every production
Robinhood vault by address, a missing Morpho API record on that chain does not
apply the ``not_in_morpho_api`` hard-blacklist flag. The scanner still uses all
available on-chain metadata, but omits API-provided warning enrichment when no
record is available. This is intentionally a temporary exception and must be
removed once Morpho's Robinhood API coverage is complete.

Links
~~~~~

- `Listing <https://tradingstrategy.ai/vaults/protocols/morpho>`__
- `Homepage <https://morpho.org/>`__
- `App <https://app.morpho.org/>`__
- `Documentation <https://docs.morpho.org/>`__
- `Morpho V2 docs <https://docs.morpho.org/learn/concepts/vault-v2/>`__
- `GitHub (V2) <https://github.com/morpho-org/vault-v2>`__
- `Twitter <https://x.com/MorphoLabs>`__
- `DefiLlama <https://defillama.com/protocol/morpho>`__

Audits
~~~~~~

- `Morpho audits <https://docs.morpho.org/security/audits/>`__

Morpho V1 API
~~~~~~~~~~~~~

.. autosummary::
   :toctree: _autosummary_vault_morpho
   :recursive:

   eth_defi.erc_4626.vault_protocol.morpho.vault_v1

Morpho V2 API
~~~~~~~~~~~~~

.. autosummary::
   :toctree: _autosummary_morpho_v2
   :recursive:

   eth_defi.erc_4626.vault_protocol.morpho.vault_v2
