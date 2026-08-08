Ember API
---------

`Ember <https://ember.so/>`__ integration.

Ember is the investment platform and infrastructure for launching, accessing,
and distributing traditional and onchain financial products through crypto capital markets.
The protocol enables issuers to tokenise and distribute products while providing investors
access to yield-bearing vaults through a unified interface.

Ember vaults operate on Ethereum and Sui. They follow ERC-4626 principles but use custom events:
``VaultDeposit`` instead of the standard ``Deposit`` event, and ``RequestRedeemed``/``RequestProcessed``
instead of the standard ``Withdraw`` event. Platform fees are embedded in the vault rate updates.
Deposits are synchronous, while withdrawals call ``redeemShares`` to enter a
pending queue. The Ember operator later processes the request and transfers
assets directly to its chosen receiver: there is no depositor claim transaction.
The displayed T+4 period is an offchain operator service estimate, not an
onchain settlement deadline.

Withdrawal timing metadata
~~~~~~~~~~~~~~~~~~~~~~~~~~

The scanner reads Ember's per-vault offchain ``withdrawalPeriodDays`` metadata
and exports it as ``estimated_settlement`` (in seconds) for backtesting. This
field is a historical or operational estimate of the queue's redemption wait,
not a promise that a request is claimable at that time. Ember does not expose
a binding contract timing bound for this lifecycle, so
``min_withdrawal_period`` and ``max_withdrawal_period`` are ``null`` and
``withdrawal_delay_type`` is ``delay``. See :doc:`../withdrawal-period-audit`
for the public export contract.

Links
~~~~~

- `Listing <https://tradingstrategy.ai/trading-view/vaults/protocols/ember>`__
- `Homepage <https://ember.so/>`__
- `App <https://ember.so/earn>`__
- `Documentation <https://learn.ember.so/>`__
- `Twitter <https://x.com/EmberProtocol>`__
- `Audit report <https://ember.so/documents/ember_protocol_audit.pdf>`__
- `DefiLlama <https://defillama.com/protocol/ember>`__
- `Example vault on Etherscan <https://etherscan.io/address/0xf3190a3ecc109f88e7947b849b281918c798a0c4>`__

.. autosummary::
   :toctree: _autosummary_ember
   :recursive:

   eth_defi.erc_4626.vault_protocol.ember.vault
   eth_defi.erc_4626.vault_protocol.ember.deposit_redeem
   eth_defi.erc_4626.vault_protocol.ember.settlement
   eth_defi.erc_4626.vault_protocol.ember.offchain_metadata
