Kinexys
=======

`Kinexys by J.P. Morgan <https://www.jpmorgan.com/kinexys/index>`__ is the
bank's blockchain platform for programmable payments, tokenised assets and
near-real-time settlement. The vault scanner integration currently tracks the
JPMorgan OnChain Liquidity-Token Money Market Fund token, JLTXX, which is
published through the Kinexys Digital Assets Fungible Asset Contract
contract surface.

JLTXX is not an ERC-4626 vault. It is a permissioned ERC-20-compatible
tokenised money market fund instrument using the ODA-FACT contract surface.
The integration therefore routes it through a scan-only adapter that reads
on-chain token supply and uses an explicitly labelled ``1.00`` USD NAV
estimate until an official historical NAV source is available.

Fees
----

JLTXX fee data is disclosed off-chain. The `May 13, 2026 SEC prospectus
<https://www.sec.gov/Archives/edgar/data/1659326/000119312526217424/d44657d485bpos.htm>`__
advertises ``0.71%`` gross total annual fund operating expenses and ``0.16%``
net total annual fund operating expenses after waivers through June 30, 2028.
The adapter exposes the current net expense ratio as the management-like annual
fee and reports no separate performance, deposit or withdrawal fee.

Links
-----

- `Tokenized Money Market Funds page <https://www.jpmorgan.com/kinexys/tokenized-money-market-funds>`__
- `Fact sheet <https://am.jpmorgan.com/content/dam/jpm-am-aem/americas/us/en/literature/fact-sheet/money-market/fs-ocltmm-t.pdf>`__
- :doc:`Kinexys ODA-FACT API documentation </api/kinexys/index>`
