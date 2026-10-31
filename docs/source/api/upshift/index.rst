Upshift API
-----------

`Upshift <https://www.upshift.finance/>`__ vault protocol integration.

Upshift provides institutional onchain asset-management infrastructure for
non-custodial yield vaults. The metadata reader fetches public descriptions,
strategist identities and named operator wallets from the documented Upshift
vault API. Upshift does not publish an explicit curator field.

.. autosummary::
   :toctree: _autosummary_upshift
   :recursive:

   eth_defi.erc_4626.vault_protocol.upshift.vault
   eth_defi.erc_4626.vault_protocol.upshift.deployment
   eth_defi.erc_4626.vault_protocol.upshift.deposit_redeem
   eth_defi.erc_4626.vault_protocol.upshift.offchain_metadata

Account admission
~~~~~~~~~~~~~~~~~

Account admission varies by deployment. Multi-asset vaults with a non-zero
``sendersWhitelistAddress()`` require approved accounts and export
``deposit_permission=whitelisted``. USPC Custody Vault on Ethereum
(``0x0A152C5b8D48C26619FF3ae85Ef1B54A79e20870``) is a restricted example.
Its accepted USDC input and public app listing do not establish public access.
``assetsWhitelistAddress()`` concerns tokens, not depositor admission.

The adapter honours an explicit metadata block; otherwise it reads fresh state
for account policy and membership through the shared preflight. Unreadable policy
becomes ``unknown`` in the scanner. Supported legacy vaults have a separate
blacklist check. Pauses, deposit limits and queued withdrawal availability do
not change the account-admission classification.

Permissionless strategy shortlists must exclude restricted vaults even when
an individual account is admitted. Transaction capability remains independent;
an explicitly authorised integration can still use the membership preflight.
Numbered migration 018 repairs cached admission fields across existing Upshift
records; normal scanner publication is needed before consumers see the fix.

Denomination and deposit tokens
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For Upshift, **the denomination token is the withdrawal payout token** returned
by ``asset()``. It also supplies the units for share-price accounting. Extra
accepted deposit tokens do not change the denomination:

==================== ================== ============================
Vault                Denomination       Example deposit input
==================== ================== ============================
Axis Origin USDx     USDx               USDC
Tori Ecosystem       trUSD              USDC
earnAUSD             AUSD               USDC
==================== ================== ============================

``fetch_denomination_token()`` returns that single payout/accounting token.
The compatibility method ``fetch_all_denomination_tokens()`` lists accepted
deposit inputs, with the payout token first. For multi-asset vaults, these
inputs are exposed as ``deposit_manager.deposit_assets``. Do not relabel the
denomination as USDC because a vault accepts it. The share token is separate
from both.

A USDC strategy still needs a verified conversion from a different payout
token back to USDC. Defining denomination correctly does not add that swap.

Deposits and withdrawals
~~~~~~~~~~~~~~~~~~~~~~~

Multi-asset vaults accept their reference asset as well as extra whitelisted
inputs, and mint a separate LP token. The manager includes the LP approval
needed to escrow shares for a queued withdrawal. Legacy single-asset vaults
use ``requestRedeem`` rather than the standard ERC-4626 ``redeem``.

After maturity an admitted caller can claim a funded request directly. Public
batch processing can also pay it; the existing completion-recovery hook matches
its date, shares and actual token transfer. One receiver/date obligation must
belong to one request. Outside additions halt settlement instead of allocating
an aggregate payment. Pending legacy claims retain their fixed net entitlement;
multi-asset claims retain floating share value until they are paid.

Zero-lag legacy requests pay in the request transaction; zero-lag multi-asset
vaults use their validated instant selector. Sender whitelists, receiver quotas,
blacklists, pauses, caps and payout liquidity remain deployment-specific.
A USDC input does not establish a USDC payout: strategies must verify both
conversion directions before using a vault whose reference asset differs.
