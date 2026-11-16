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
     - :py:class:`~eth_defi.erc_4626.vault_protocol.vault_morpho.vault.VaultMorphoVault`.
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
unattributed VaultMorpho flag before the ordinary V1 flag; protocol features matched earlier
in the factory retain precedence over this Morpho-specific branch. Both flags
are retained: ``morpho_like`` supplies lending-protocol classification used by
lending readers, while the more specific feature overrides protocol attribution;
ignoring ``vault_morpho_like`` would select a reader that misses its cap.
Do not duplicate this selection in scanners, strategies or CLI consumers.

Third-party implementations
^^^^^^^^^^^^^^^^^^^^^^^^^^^

A ``MORPHO()`` getter establishes a lending dependency, not official protocol
provenance. The capped ``VaultMorpho`` implementation is maintained separately;
see :ref:`vault-morpho-unattributed` for its verified source and unresolved
operator identity. Its additional feature selects a generic ERC-4626 adapter,
not :py:class:`~eth_defi.erc_4626.vault_protocol.morpho.vault_v1.MorphoV1Vault`.
The catalogue must not attach Morpho's website, API coverage or protocol risk
and fee defaults to that implementation.

Canonical Solidity references are `MetaMorpho
<https://github.com/morpho-org/metamorpho/blob/87ee5faf910d7224cee70120a94b0921c15ecab7/src/MetaMorpho.sol>`__
and `Morpho Vault V2
<https://github.com/morpho-org/vault-v2/blob/bcad37068507dba4193f79c48d539b241faa7f37/src/VaultV2.sol>`__.
These are source references for the canonical contracts, not substitutes for a
third-party implementation's own source repository.

Metadata requests
~~~~~~~~~~~~~~~~~

Warning and curator enrichment uses
:py:mod:`eth_defi.erc_4626.vault_protocol.morpho.session` for a shared request
budget and persisted provider cooldown. Existing warning data survives a
transient API failure; a successful refresh can clear obsolete warnings.

Deposit permissions
~~~~~~~~~~~~~~~~~~~

Canonical MetaMorpho V1 deposits are permissionless; compatible implementations
still need their own admission assessment. Morpho V2's outer deposit gate slots
are disabled when ``receiveSharesGate`` and ``sendAssetsGate`` are both zero.
This does not establish permissionless access through an inner vault or prove
that the share owner can exit. A V2
deployment may configure custom implementations of the `Morpho gate interfaces
<https://github.com/morpho-org/vault-v2/blob/main/src/interfaces/IGate.sol>`__
for the share receiver and asset sender independently.
``is_whitelisted_deposit()`` reports whether either outer deposit gate is
configured; it does not interpret the gate's policy or certify complete-route
access. The separate ``is_permissionless(owner)`` inspection described below
reports a refused account as restricted and leaves an unreviewed gate's policy
unknown even when that account is admitted.

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
   eth_defi.erc_4626.vault_protocol.morpho.deposit_redeem
   eth_defi.erc_4626.vault_protocol.morpho.recovery

Complete-route admission inspection
-----------------------------------

``MorphoV2Vault.is_permissionless(owner)`` is an opt-in policy reader, separate
from the outer ``is_whitelisted_deposit()`` classification. It pins the entry,
exit and recognised inner-route reads to one block. A refused owner/adapter
returns false. A gate admitting one account still has unresolved policy and
raises ``NotImplementedError`` rather than proving public access.

Nonzero liquidity adapters must match a reviewed deployed code hash and their
parent vault. Initial support is deliberately bounded to the Keyrock Ethereum
market adapter ``0x7CA32dCD9269b0BeB21a4F364DED5cfFCB7b3635`` at block 26402309
and the two Muscadine Base adapters ``0x8B6E43CCE1961D3671a39Fe8D9E711E69ddD74ce``
and ``0x5b211DA4Cd92cfb9CCCFbd1De78289955EB236CD`` at block 56688126.
Their verified contract names are ``MorphoMarketV1AdapterV2`` and
``MorphoVaultV1Adapter`` respectively. Matching a getter alone is insufficient.
The latter getter's name does not prove the inner vault is MetaMorpho V1: these
reviewed routes lead to gate-bearing vaults. Unreviewed adapters, inner vaults
without the required policy interface, and further nested routes remain unknown;
this is not a general permission certificate for every canonical V1/V2 route.

The outer deposit reader reports only outer gate configuration. Disabled outer
gates can coexist with a refused inner adapter or share-owner exit; complete-route
admission requires its separate policy inspection. Transaction managers
retain their exact-call admission checks and typed refusals. These observations
are dated policy evidence, not guarantees against later upgrades or liquidity
changes.

Public liquidity recovery
-------------------------

The Ethereum Api3 dCOMP USDC vault
``0x36cfe1568461e499391ef0a555300f1ae2da2439`` and Purinta USDC vault
``0xc92a37fd0250f4eecf092960a2f70a1334217528`` demonstrated a distinct exit
failure at block 26402309. After a 1,001 USDC deposit and 24 hours of fork time,
the whole-vault share claim exceeded the configured exit market's supply
position. Morpho Blue's supply-share subtraction underflowed with
``Panic(0x11)``. This failure was not repaired by changing price tolerances.

:py:class:`~eth_defi.erc_4626.vault_protocol.morpho.deposit_redeem.MorphoV2DepositManager`
first constructs and simulates the exact direct redemption. Only a proven
arithmetic panic, a separately verified exit-position shortfall and an explicit
``vault.redemption_recovery_max_fee_bps`` authorisation can select recovery.
``None`` disables recovery; zero permits no positive penalty rate. The library
contains no built-in address allowlist: callers must supply their reviewed policy.
The executor's manual rule enables these two identities with a one-basis-point
maximum. This execution authorisation does not establish permissionless access.

Recovery calls public ``forceDeallocate`` on one other market in the same
registered adapter. It selects the largest non-exit supply position that covers
the gross claim, then requires that exact call, including its cash and permission
checks, to succeed. A refusal does not try another market or build a multi-market
plan. Its receipt proves the penalty shares burned
and assets retained by the vault. A second transaction redeems only the remaining
shares. The ticket reserves the original disposal and records the shares still
in custody; net cash is recognised only from the finishing receipt. The
disclosed penalty must not be subtracted from that net payout again.

This is a staged synchronous exit, not a protocol withdrawal queue. Persist
the request's preflight data before broadcast, then its receipt-derived ticket.
Use the existing transaction records when resuming the finishing step; do not
submit another recovery or retry the original share amount. Freed idle assets
can be consumed between the two transactions, leaving a paid ticket pending.
No payout assets are injected, no allocator is impersonated and no oracle
checks are relaxed by this recovery path.

Guarded deployments require a linked ``MorphoLib`` and owner-configured recovery
cap in addition to ordinary ERC-4626 whitelisting. The cap limits the per-call
penalty rate, not cumulative losses. Existing modules require replacement;
enabling the Python rule alone does not enable the guarded call.

See the detailed source analysis, measured shortfalls and recorded Safe/Guard
run in ``tradeexecutor/strategy/vault_protocol/morpho.py`` and
`the research case <https://github.com/tradingstrategy-ai/vault-notebooks/pull/25#issuecomment-6896601999>`__.
