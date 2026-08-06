Withdrawal-period audit
=======================

This is the protocol-level audit of withdrawal timing for the adapters under
``eth_defi.erc_4626.vault_protocol``. It records what the current
``WithdrawalPeriod`` accessors can safely promise, rather than turning every
protocol's marketing estimate into a machine-readable guarantee. Sources were
checked against the protocol documentation linked in each adapter and, where
available, the deployed contract accessor or verified ABI.

Structured export
-----------------

The following adapters now return ``WithdrawalPeriod`` and populate
``min_withdrawal_period``, ``max_withdrawal_period`` and
``withdrawal_delay_type`` in lifetime JSON:

* **Atoma** — zero to one ``epochDuration()`` interval; the verified vault
  uses ``requestWithdrawal``/``claimWithdrawal``. See the
  `Atoma vault implementation <https://arbitrum.blockscout.com/address/0xd4242FD8DE6E3128f0435b52DCe29155098CbBFF>`__.
* **Avant, Ethena, Mainstreet, USDX Money** — the live
  ``cooldownDuration()`` value, as a fixed ``delay``. The accessor is read
  from each deployment instead of using the documented seven-day default.
* **D2 Finance** — zero to one current trading epoch, marked ``epoch``.
* **Gains and Ostium** — the existing version-specific ranges: one to three
  Gains epochs for V1 and the onchain settlement interval plus scheduling
  window for Ostium V1.5, marked ``delay`` for the request/claim lifecycle.
* **IPOR** — the live ``REDEMPTION_DELAY_IN_SECONDS`` value, marked ``delay``.
  Liquidity can still prevent a transaction after the delay.
* **KiloEx** — one to three documented three-day epochs, marked ``epoch``.
* **NaraUSD+** — the live ``cooldownDuration()`` value, marked ``delay``.
* **Teller** — the pool-specific ``withdrawDelayTime()`` value, marked
  ``delay``.
* **3Jane** — the tranche-specific lock (one month for sUSD3 and zero for
  USD3), marked ``delay``.
* **Upshift** — the vault-specific ``lagDuration()`` value, marked ``delay``.
* **USDai** — zero to the current 30-day redemption window, marked ``epoch``.
* **Aave, Brink, Curvance, Dolomite, Foxify, Gearbox, Sentiment, Singularity
  Finance and Term Finance** — direct ERC-4626 redemption with no protocol
  request, cooldown or epoch, marked ``instant``. This does not promise that
  the vault or its underlying lending market has sufficient liquidity.

Instant adapters
----------------

Adapters that explicitly report a zero legacy lock-up are normalised by the
scanner into a zero-to-zero ``instant`` period. This applies to
Auto Finance, Deltr, Euler, Fluid, Frax, Goat, Harvest, HyperLend, HypurrFi,
Inverse Finance, Kiln, LlamaLend, Morpho, Resolv, sBOLD, Silo, Sky, Spark,
Spectra, Summer, USDD and Yearn (including its compounder variants). This
means synchronous redemption is distinguished from an unavailable timing
value, while the JSON caveat about liquidity still applies.

Timing that is not representable
---------------------------------

The current JSON model intentionally does **not** fabricate a period for the
following adapters:

* **Queue or liquidity dependent:** Aarna, Accountable, Aera, BaseVol, Bulla,
  cSigma, ETH Strategy, 40acres, Hyperdrive HL, Infinifi, Maple, NashPoint,
  Renalta, Royco, Secured Finance, Superform, Symbiotic, TrueFi, Untangle,
  YieldFi, Yo, Yuzu Money and ZeroLend. Their contracts or operator processes
  make redemption timing depend on liquidity, a queue position, an underlying
  strategy or an unbounded operator action. The current ``WithdrawalPeriod``
  cannot express that conditional or unbounded wait.
* **Variable or policy estimates:** Altura, Axis, Centrifuge, CrystalClear,
  Ember, ForgeYields, Frankencoin, Lagoon, Liquid Royalty, Maple AQRU, Plutus,
  Umami and YieldNest.
  Their existing ``get_estimated_lock_up()`` values describe an epoch estimate,
  maturity date, operator service target, reward-interest delay, or strategy
  policy rather than a guaranteed request-to-redemption bound. They remain in
  the legacy ``lockup`` field only for compatibility; their structured
  withdrawal fields are null.

Several of these protocols have useful YAML descriptions, but YAML is not a
safe substitute for a per-vault onchain timing read. In particular, a generic
``withdrawal_period`` YAML field would become stale when governance changes a
cooldown, when a curator changes the underlying strategy, or when an operator
queue has no deadline. Add a protocol-specific accessor once a canonical
contract field or a bounded, source-backed SLA is available.

Known model gaps
---------------

The two-value export cannot currently represent all of the following without
new fields:

* a maturity date that is independent of the withdrawal request;
* an operator service target with no enforceable upper bound;
* liquidity-dependent completion where the minimum is zero but the maximum is
  unbounded;
* penalty-free windows where withdrawal is possible immediately; or
* multiple phase calendars and per-vault epoch lengths in one record.

Until such fields are added, ``null`` is preferable to a misleading number.
Consumers should also treat a non-null period as a timing rule only: it does
not promise liquidity, permission, keeper execution, or a successful
transaction.
