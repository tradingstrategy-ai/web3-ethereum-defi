Libeara
========

`Libeara <https://libeara.com/>`__ provides technology for regulated,
tokenised capital-market products. The integration tracks two reviewed
Ethereum CMTAT fund-share proxies, ChinaAMC USD Digital Money Market Fund
Class I USD (CUMIU) and Bosera Liquidity Income Fund SP (BELIF), plus the
Delta Wellington Ultra Short Treasury On-Chain Fund (ULTRA) on Arbitrum.

The products are permissioned. Their CMTAT contracts expose an
issuer-maintained NAV and a transfer rule engine, but not a public subscription
or redemption lifecycle. Accordingly, this integration is read-only: it reads
the share supply and current or historical NAV record, and it never presents a
generic deposit or redemption manager.

ULTRA does not expose the same reviewed CMTAT NAV interface. Its integration
therefore reports supply only and leaves share price and total assets unset;
it does not substitute a token market price or fabricate a NAV value.

The contracts are upgradeable and contain issuer-controlled mint, burn, pause,
freeze and rule-engine functions. Consumers should check the fund's official
offering material, current eligibility status and dealing terms before relying
on a token balance or NAV value.
