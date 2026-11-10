K3
==

`K3 Capital <https://www.k3.capital/>`__ operates epoch-staged asynchronous
vaults. The chain-aware allowlist covers Ethereum cbBTC and USDe, Arc cirBTC, Base USDC and Monad AUSD.
Test deployments remain excluded. The official
`deployment registry <https://github.com/K3-Capital/k3-vault-contracts/tree/main/deployments>`__
provides optional identity metadata through ``K3Vault.k3_metadata`` for the three direct smart-account wrappers;
the two Amphor-backed wrappers use maintained source-linked copy. Failures retain individual
vault descriptions. Registry status and account wiring are historical evidence,
not live financial data or permission checks. Public descriptions use maintained
copy and source links without appending deployment-registry boilerplate.

The `architecture <https://github.com/K3-Capital/k3-vault-contracts/blob/main/ARCHITECTURE.md>`__
defines request, epoch close, NAV settlement and claim phases. NAV is trusted,
post-fee and unchanged between settlements. The adapter leaves fee rates and
technical risk unassigned: cbBTC documentation publishes default fees, but
vault-specific terms may override them. Zero-address maxDeposit/maxRedeem
measure claims, not request capacity; request availability uses paused().
Account admission remains unknown until source policy has fixed-block coverage.

The Base and Monad deployments use MetaVaultWrapper rather than
SmartAccountWrapper. Their share conversions value underlying Amphor shares
at the last settled epoch rate. They expose account blacklists and optional
request decreases, so the direct wrapper's no-cancellation policy must not be
applied to them. The common reader uses their compatible totalAssets and
convertToAssets reads and paused() request diagnostic; an unpaused wrapper
alone does not prove that its underlying vault can accept a request.

No transaction manager is advertised: synchronous calls cannot safely execute
this lifecycle. A dedicated manager and guarded fork lifecycle are needed
before enabling execution. The generic manager is explicitly blocked.
The Pashov audit covers a reviewed source revision; bytecode verification
alone does not prove coverage of later changes.

Run migration 057 after merge with the scanner stopped, then export and publish
metadata. It only refreshes existing rows. Generic reads already collected NAV;
this change does not alter price reads, reader state, leads or historical rows,
so no price backfill is included.
Check the preview's before-state: all five rows were unknown ERC-7540 vaults
in the research snapshot. Investigate an unexpected competing protocol feature
before applying, because existing features are preserved by the refresh.

Strategy evidence and announcement matching
-------------------------------------------

Research on 2026-11-10 matched the complete cirBTC launch thread to
``5042:0x56fb80282bb565de67c196924dda0138e30323be`` using the
`announcement's deposit link <https://x.com/k3_capital/status/2119852420981506238>`__.
The `opening post <https://x.com/k3_capital/status/2119852350034895084>`__
calls the strategy delta-neutral. The
`borrowing explanation <https://x.com/k3_capital/status/2119852361879556335>`__
specifies cirBTC collateral, USDC borrowing at a target health factor of 1.3,
and conversion of investment income less borrowing costs into cirBTC.
The `mandate post <https://x.com/k3_capital/status/2119852385367740877>`__
permits isolated borrowing markets, lending and selected principal tokens,
and identifies Aave and Morpho lending at launch. The
`current app catalogue <https://api-app.k3.capital/v1/earn/vaults>`__
independently matches the address and asset. Tags are ``delta_neutral``,
``lending``, ``carry_trade`` and ``risk_premium_harvest``. The opening post
explicitly names risk-premium harvesting; the other posts establish the
income mechanism. See :mod:`eth_defi.vault.strategy_tag` for its detailed
definition, classification criteria and linked vault example.
The description distinguishes income generation
from retained bitcoin price exposure, and explains borrowing costs and liquidation.
Neither a recursive borrowing loop nor perpetual-futures trading is established.
The mandate alone does not establish that principal tokens are currently held.

The `cbBTC product documentation <https://docs.k3.capital/vault-infra/introduction/cbbtc-vault.html>`__
identifies ``1:0x009c02a73706a68e0ae0209235408206e4f53709`` and describes
pledging cbBTC to borrow USDC, allocating it among permitted yield venues and
converting income net of borrowing costs into cbBTC. Its mandate explicitly
targets risk premia and liquidity-mining incentives. Tags are ``carry_trade``,
``risk_premium_harvest`` and ``lending``. The latter interprets the permitted
allocation of borrowed USDC to "selected Morpho and Euler markets" as lending;
it does not establish current holdings.
The source does not establish a recursive borrowing loop, vault-level perpetual
trading or an explicit delta-neutral mandate. Published default fees are 0%
management and 5% performance, subject to vault-specific terms. Monday and Friday
settlement is an operating target, not a guaranteed withdrawal schedule.

The follow-up research on 2026-11-10 covered exact addresses, share symbols and
product names, the official app, deployment registry, product documentation and
partner announcements. Authenticated X API pagination returned 386 original
K3 posts and replies back to June 2024, replacing the earlier incomplete profile
mirror research. Full-archive searches for exact target addresses and distinctive
product names returned no matches for the queried period from January 2026;
broader searches included partner posts. No matching strategy source was
established for these three deployments:

* USDe Enhanced Yield: ``1:0x333333c5def8368c3fcc9c378cedc0f709fa5ecb``.
  The deployment record and app establish identity, but do not establish how
  the vault earns additional income. Ethena's own backing strategy is not
  evidence of this vault's allocations.
* USDC: ``8453:0xf508f0096b966f657a6bee55b341e9309d5871fb``.
  Verified source establishes the underlying-vault claim and settlement rules,
  not the investments. The
  `cbMaxxxing launch link <https://x.com/k3_capital/status/2103147865317212406>`__
  points to Morpho vault ``0x0548a36028ffdc7d5aaa7a182f0d1fe0264f5a5c``,
  not this wrapper.
* AUSD: ``143:0x37aef9d5b058382024ffb5c08e2f4c9ca4978751``.
  Verified source establishes the underlying-vault claim and settlement rules,
  not the investments. The
  `Galaxy announcement links <https://x.com/k3_capital/status/2092959321504297100>`__
  point to Euler ``0x4a798ef3a5846af8e04de317dd7f3612a47ffa99`` and
  Accountable ``0x77410132fd468d67b820314d378be1fdbfa2baa4``. No evidence
  connects their lending mandate to this wrapper. The
  `K3 AUSD Earn announcement <https://x.com/k3_capital/status/2074487023583555878>`__
  also identifies a different product: Euler
  ``0x598035a2e984993229f6fa4c8c3ecf6c52ad51b5``. A matching name or symbol
  does not establish a shared mandate.

These three retain missing strategy tags (``None``); a missing matching
announcement is not proof of any particular strategy. All five descriptions
have been checked against the writing style guide. The still-unapplied onboarding
migration 057 supplies this copy and the reviewed tags, preserving its existing
five-vault scope. No successful migration is amended or rerun.

Real-provider verification
--------------------------

The focused live check requires JSON_RPC_ETHEREUM, JSON_RPC_ARC, JSON_RPC_BASE and JSON_RPC_MONAD from the
private test environment. It also fetches all three public K3 registry records and five live wrappers::

    source .local-test.env && RUN_K3_LIVE_TEST=1 poetry run pytest tests/erc_4626/vault_protocol/test_k3.py -q

A manual run on 2026-11-10 passed against the configured Ethereum, Arc, Base and Monad
providers and the public GitHub deployment registry. It covered real contract
identity, scanner records and JSON serialisation of both description fields;
it did not broadcast transactions. Repeat this command and add its result as
a PR comment when opening the integration PR.

Public copy was checked against the repository description style guide.
The protocol artwork reuses K3 Capital's existing reviewed curator assets.
