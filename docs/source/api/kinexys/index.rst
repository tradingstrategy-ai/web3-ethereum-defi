Kinexys API
-----------

`Kinexys Digital Assets <https://www.jpmorgan.com/kinexys>`__ tokenised fund
integration using ODA-FACT contracts.

The ODA-FACT name comes from J.P. Morgan's token-standard terminology. Current
Kinexys material describes it as `Kinexys Digital Assets Fungible Asset Contract
<https://www.jpmorgan.com/kinexys/documents/portfolio-management-powered-by-tokenization.pdf>`__,
while the ODA prefix reflects the earlier Onyx Digital Assets branding before
Onyx was renamed to Kinexys.

The ODA-FACT adapter tracks permissioned ERC-20-compatible tokenised fund
contracts through the shared vault price pipeline. It is not an ERC-4626
implementation: historical reads use token supply from the ODA-FACT contract
and an explicitly labelled ``1.00`` USD NAV estimate for JLTXX until an
official NAV source is integrated. MONY is a separately deployed FACT Diamond
whose token surface has no on-chain NAV or share-price view; its supply is
tracked without deriving a fund valuation.

JLTXX fees are not available from the token contract. The adapter hardcodes the
Token Class fee disclosure from the `May 13, 2026 SEC prospectus
<https://www.sec.gov/Archives/edgar/data/1659326/000119312526217424/d44657d485bpos.htm>`__:
``0.71%`` gross total annual fund operating expenses and ``0.16%`` net total
annual fund operating expenses after waivers through June 30, 2028. The shared
vault fee model exposes the current net expense ratio as the management-like
annual fee.

MONY is J.P. Morgan Asset Management's My OnChain Net Yield Fund, powered by
Kinexys Digital Assets and distributed through Morgan Money according to the
`launch announcement <https://www.prnewswire.com/news-releases/jp-morgan-asset-management-launches-its-first-tokenized-money-market-fund-302642262.html>`__.
Its FACT Diamond includes issuer-controlled account activation, lock, stop-code
and upgrade controls. The adapter therefore does not represent its transfer or
request-based burn functions as a public subscription or redemption path.

.. autosummary::
   :toctree: _autosummary_kinexys
   :recursive:

   eth_defi.tokenised_fund.kinexys.vault
   eth_defi.tokenised_fund.kinexys.historical
   eth_defi.tokenised_fund.kinexys.backfill
