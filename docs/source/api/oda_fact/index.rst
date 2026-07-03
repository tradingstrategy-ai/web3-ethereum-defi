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
official NAV source is integrated.

JLTXX fees are not available from the token contract. The adapter hardcodes the
Token Class fee disclosure from the `May 13, 2026 SEC prospectus
<https://www.sec.gov/Archives/edgar/data/1659326/000119312526217424/d44657d485bpos.htm>`__:
``0.71%`` gross total annual fund operating expenses and ``0.16%`` net total
annual fund operating expenses after waivers through June 30, 2028. The shared
vault fee model exposes the current net expense ratio as the management-like
annual fee.

.. autosummary::
   :toctree: _autosummary_oda_fact
   :recursive:

   eth_defi.oda_fact.vault
   eth_defi.oda_fact.historical
