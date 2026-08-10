Bulla Network
-------------

`Bulla Network <https://www.bulla.network/>`__ provides infrastructure for
financing trade invoices and direct loan offers. Its pools bring together
stablecoin liquidity from participants and businesses seeking financing
against amounts they expect to receive.

Some pools restrict who can participate. Withdrawals may also be delayed when
cash is committed to outstanding financing or other participants are ahead in
the queue. Participants should assess borrower repayment, collection,
liquidity and underwriter-selection risk. Published `audit reports
<https://github.com/bulla-network/factoring-contracts/tree/main/audits>`__
are useful security evidence, but do not cover those economic risks.

Withdrawal timing metadata
~~~~~~~~~~~~~~~~~~~~~~~~~~

For the reviewed TCS Settlement Pool, Bulla publishes a 30-day average
redemption period. The scanner exports this as ``estimated_settlement`` (in
seconds) for backtesting, with ``withdrawal_delay_type`` set to ``delay``.
Invoice repayment and available pool liquidity determine the actual queue
duration, so it is not a binding contract period: both
``min_withdrawal_period`` and ``max_withdrawal_period`` are ``null``. Bulla's
published 40-day maximum remains descriptive pool material rather than an
exported smart-contract bound. See :doc:`../withdrawal-period-audit` for the
public export contract.

Links
~~~~~

- `Bulla pools <https://banker.bulla.network/#/yield>`__
- `Documentation <https://docs.bulla.network/>`__
- `GitHub <https://github.com/bulla-network>`__
- `Twitter <https://x.com/BullaNetwork>`__
- `Example Arbitrum pool <https://arbiscan.io/address/0xc099773267308D8e9E805f47EABf9ab13bBc9e37>`__

.. autosummary::
   :toctree: _autosummary_bulla
   :recursive:

   eth_defi.erc_4626.vault_protocol.bulla.vault
