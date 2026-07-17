Libeara
========

`Libeara <https://libeara.com/>`__ provides technology for regulated,
tokenised capital-market products. The integration tracks two reviewed
Ethereum CMTAT fund-share proxies: ChinaAMC USD Digital Money Market Fund
Class I USD (CUMIU) and Bosera Liquidity Income Fund SP (BELIF).

The products are permissioned. Their CMTAT contracts expose an
issuer-maintained NAV and a transfer rule engine, but not a public subscription
or redemption lifecycle. Accordingly, this integration is read-only: it reads
the share supply and current or historical NAV record, and it never presents a
generic deposit or redemption manager.

The contracts are upgradeable and contain issuer-controlled mint, burn, pause,
freeze and rule-engine functions. Consumers should check the fund's official
offering material, current eligibility status and dealing terms before relying
on a token balance or NAV value.
