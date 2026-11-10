# Lagoon vaults missing descriptions

Snapshot checked on 2026-11-10. The [published vault feed](https://top-defi-vaults.tradingstrategy.ai/top_vaults_by_chain.json) was generated at `2026-11-10T13:34:48Z` from commit `d8f43988805f144672d2799920db0375cfcf7ed7`.

Of 160 published Lagoon vaults, 119 lack both the short and long description. Missing means null, empty or whitespace-only text. All description gaps in this snapshot affect both fields; there are no short-only or long-only gaps. This list covers published vaults, not every deployment or every vault in Lagoon’s public catalogue.

Of the 119 entries, 105 carry the `unofficial` flag and 14 do not. This flag records the exported classification; it is not an independent judgement of legitimacy. Entries are grouped by that flag and ordered by snapshot TVL. The exported `current_nav` value is included only for prioritisation; its denomination has not been independently checked.

The [coverage CSV](lagoon-description-coverage-2026-11-10.csv) records all 160 published entries, their identifiers, flags and missing-field indicators, including strategy tag coverage. There are 116 published entries without strategy tags. No descriptions, tags, eligibility rules or production data were changed.

## Missing short and long descriptions

| # | Vault | Chain | Rounded exported NAV | Unofficial flag | Address |
| ---: | --- | --- | ---: | --- | --- |
| 1 | TowerBridge | Ethereum (1) | 4,560,925 | No | `0x22f99228f3ba7cfc7189ddf14366970fe0cef0cb` |
| 2 | Der base USDC | Ethereum (1) | 4,191,492 | No | `0xba6cfe8a9d199cd7f3e50114c4e4ec66f2d52c87` |
| 3 | Stable Vault | Ethereum (1) | 2,723,543 | No | `0xcda323c2df692d989b24ba51d0acca924cf9a344` |
| 4 | Angmar Capital | Arbitrum (42161) | 2,651,813 | No | `0x1723cb57af58efb35a013870c90fcc3d60174a4e` |
| 5 | Dynamic Alpha Fundamental 2X | Arbitrum (42161) | 2,331,634 | No | `0xc047d64dafe9e6ac76508835c17c6719f9278c1c` |
| 6 | Der USDC | Ethereum (1) | 2,119,006 | No | `0xf10801bcc3deaf467fb8b3dbb7430111822e6dab` |
| 7 | Muchacho USDC | Ethereum (1) | 1,177,537 | No | `0xef39d77c7fb6224ac974c5fa4e3151a6c6ce9594` |
| 8 | Odyssey Stablecoins Discretionary | Ethereum (1) | 1,039,670 | No | `0xfd104766499a3ff60ea85b5c6015ba9e32b8c891` |
| 9 | Moon Digital AM USDC | Ethereum (1) | 885,837 | No | `0xa00f63e85b3d242568a9edecb48f5e2cf879b07b` |
| 10 | Balanced Vault | Ethereum (1) | 819,852 | No | `0xa5ae405242f42c47996a0c6857ff10a77f9bdee6` |
| 11 | Boosted Vault | Ethereum (1) | 689,503 | No | `0x9e0db8f43bb91e2148b0db920e21370525cf3aab` |
| 12 | Turtle Avalanche USDC | Avalanche (43114) | 16,705 | No | `0x3048925b3ea5a8c12eecccb8810f5f7544db54af` |
| 13 | 722 Capital USDC | Ethereum (1) | 4,626 | No | `0x7d798ce99ee2adc183a865d76b112810d5395ea2` |
| 14 | Turtle Linea USDC | Linea (59144) | 2,086 | No | `0x7df7e45ab573ace8f872b5d5a1689af7ff1a07f7` |
| 15 | Supro Vault | Binance (56) | 2,073,563 | Yes | `0x2795f40bf9a20575125a8f42daa6461b4c0e5615` |
| 16 | DAMM Stablecoin Fund | Arbitrum (42161) | 1,090,955 | Yes | `0xc55c9be465a44717062669d9ebef4a7785cfc2a3` |
| 17 | Trinity Trust | Ethereum (1) | 928,960 | Yes | `0x71652d4898de9a7dd35e472a5fe4577ec69d82f2` |
| 18 | Odyssey USDC | Ethereum (1) | 893,932 | Yes | `0xd167e1362d97f5ca0cb87b2e08c1fb231f574c52` |
| 19 | Flint EUR | Ethereum (1) | 698,154 | Yes | `0xe5185818dc3c7deb3327fa7f594026e66cfbaae7` |
| 20 | Evergreen.001 | Ethereum (1) | 631,704 | Yes | `0xb1e65871a38a483eee9436791ac016ddce3ae4ad` |
| 21 | Stablecoin Market Making | Ethereum (1) | 571,785 | Yes | `0x8595f5ee036e1f7c4f3ca66327ba5f38c21cb7fb` |
| 22 | GUSDC | Base (8453) | 485,438 | Yes | `0xd5c22fa3f7ee979ed7c28e36669b29797ab277e4` |
| 23 | Odyssey EURC | Ethereum (1) | 402,672 | Yes | `0xbc61b1398d2eefa0bc5c3e34511d67606551a6bf` |
| 24 | Der EURC | Ethereum (1) | 372,995 | Yes | `0xec5dfbb37bf296980d15086b6edfbd12d2836e50` |
| 25 | USDC Yield | Arbitrum (42161) | 363,755 | Yes | `0x307139c61d3f884def18c8dc1676eb88e1ed7839` |
| 26 | Ultimatum | Arbitrum (42161) | 334,237 | Yes | `0xfc47d601f205ef6b73ed5e03d5fef69617b085ba` |
| 27 | pyUSDC | Ethereum (1) | 325,237 | Yes | `0x175ea882b492c9b7a6d5852fe9da560dc7af1c72` |
| 28 | Dynamic Alpha Sentiment 2X | Arbitrum (42161) | 290,257 | Yes | `0x57587aee66d70d99024b02919901cfe552f5a0d4` |
| 29 | Strada USDC | Ethereum (1) | 273,063 | Yes | `0xcb58582b0d52ce5feecb06ba9ce66598b0d57886` |
| 30 | Block4Block | Ethereum (1) | 261,187 | Yes | `0x9fdbaaa76194d56e49cade12c1f216f47d2b865e` |
| 31 | MSA - Portefeuille Dynamique | Base (8453) | 243,873 | Yes | `0xf56bfe07b8d6e6d74258cdb6969a633629b06b08` |
| 32 | Unity Trust | Ethereum (1) | 240,558 | Yes | `0x66dcb62da5430d800a1c807822c25be17138fda8` |
| 33 | TruMarket | Base (8453) | 218,045 | Yes | `0xbe7db44f4ce20dac83b578b94fd35087f66e9754` |
| 34 | Noon STS USDC | Ethereum (1) | 199,999 | Yes | `0xb993c32f578e5156369330787cf8c8fe033bf40e` |
| 35 | Odyssey Stablecoins Systematic | Ethereum (1) | 176,123 | Yes | `0x06940f2a8db4d579eebbc03f8dfa2abf46d64a41` |
| 36 | Excellion Plasma Vault | Plasma (9745) | 166,905 | Yes | `0x3b9846b9341c16e334b2bb83897fb53eca74922b` |
| 37 | Gami D1 | Ethereum (1) | 158,196 | Yes | `0x16b85563902556d205f5c2b4485df3d26b153f96` |
| 38 | RB Capital Yield | Base (8453) | 156,350 | Yes | `0x63b04d3ce2c14f6d308657ab73ac92fc1a0b1075` |
| 39 | fsmartDCA | Ethereum (1) | 155,045 | Yes | `0xeee210457620c065a23ead4ade27d54234d2d43e` |
| 40 | AltaETF | Ethereum (1) | 134,455 | Yes | `0x3be67ba2d3fec744d1d2b5d564c83f57372578e4` |
| 41 | Mt Pelerin x Montaigne - Strategy pool | Ethereum (1) | 129,542 | Yes | `0x14b8de97d078cabf38ba3d0b7e067618f0e8ab7d` |
| 42 | Syntropia <> Resupply | Ethereum (1) | 128,093 | Yes | `0xa96bc6e084aad6976d25df9431525ed2c4d3cae4` |
| 43 | Cassa Yen Carry & Divergence Trade | Plasma (9745) | 115,475 | Yes | `0xff5ef6464896516c6ccf60da9e199680929fb01c` |
| 44 | TID Capital Perp Market Maker | Arbitrum (42161) | 104,301 | Yes | `0x7f14715bc12214f1ff26a6863334e7c8b644f4f7` |
| 45 | gsmartDCA | Ethereum (1) | 99,239 | Yes | `0xa65bb6b69427fc3bc00d3479e358efd9c60cdc4b` |
| 46 | Kamui Gearbox PoC | Ethereum (1) | 80,000 | Yes | `0xe9fc51df9168361c9cf00383c644ae9dcf2ad6b0` |
| 47 | USDC High Yield | Ethereum (1) | 67,335 | Yes | `0x47491305479635357df29fe0cb6f1af10e51d4da` |
| 48 | FLEXIBLE CRYPTO FUND | Ethereum (1) | 62,000 | Yes | `0x06973fbca7c589d10dfbe45d694dce634bff6165` |
| 49 | Bayalis EUR | Base (8453) | 57,746 | Yes | `0x5f79a4d8400e95399f868b5966d68881568684e5` |
| 50 | esmartBTC | Ethereum (1) | 55,309 | Yes | `0x6c8dc1bf9b5945543bcb046b1e6ab258e57b33d2` |
| 51 | ismartDCA | Ethereum (1) | 55,077 | Yes | `0x29bac08f328f03d7ab36b8ba7aed48153d3afe5c` |
| 52 | Etesia Multistrat | Hyperliquid (999) | 50,109 | Yes | `0xf38760cf03b1271746bdc4628e811d8e9c4eb09e` |
| 53 | HyperAI Vault | Hyperliquid (999) | 46,913 | Yes | `0xc723add84ee4646044ff28e552808e0a3ac48b54` |
| 54 | Bayalis EUR | Ethereum (1) | 46,781 | Yes | `0x09857dd84fde6cf5fec2849d703461157ad7f562` |
| 55 | MSA - Portefeuille Défensif | Base (8453) | 39,059 | Yes | `0xb725f2277f3f62ad017a6dc418433b326a9f6334` |
| 56 | TID Capital Plasma Yield | Plasma (9745) | 33,466 | Yes | `0x0c1c83c9c203f6920726a0045ff1ea95e9698d0a` |
| 57 | mUSDCx | Ethereum (1) | 31,815 | Yes | `0x8a52dc763a89cab9ab0aad4044e6b089af053323` |
| 58 | BCMUSDC | Arbitrum (42161) | 26,192 | Yes | `0xae82fdac3397751e099516c9a7220922b651dcbe` |
| 59 | TRX staking | Arbitrum (42161) | 24,938 | Yes | `0x433d416cb1437ea5712d82db57ce4221b01c4b39` |
| 60 | Odyssey Altcoins | Ethereum (1) | 24,818 | Yes | `0x128272086dde4bd54b5a85110f97f07559bf7331` |
| 61 | R3USDC | Ethereum (1) | 24,203 | Yes | `0xd07f2b0db0152f420ce142381f4f43e1c6945d3c` |
| 62 | MVP_HOPPER_USDC | Arbitrum (42161) | 22,549 | Yes | `0x49e09ec55a2d278adc841151a4bc07f34947072a` |
| 63 | 722Capital-USDC | Base (8453) | 21,843 | Yes | `0xb09f761cb13baca8ec087ac476647361b6314f98` |
| 64 | TetLAg | Arbitrum (42161) | 20,010 | Yes | `0x4bffc9c90bd68b60886bbab59d9bfad89f426776` |
| 65 | BTC DCA - Regime Vault | Base (8453) | 18,531 | Yes | `0x87b433543660001cf64ad50afc51cc2a27e73e1d` |
| 66 | ATTRIUS CAP USD | Ethereum (1) | 17,781 | Yes | `0x8fa232a3d471cb2a3426b16dec1482f90e79e126` |
| 67 | F(X)N | Ethereum (1) | 15,106 | Yes | `0x040604bb6f9a909c4eefc65f0e2565f34e0b1389` |
| 68 | Smart Beta Fundamental 2X | Arbitrum (42161) | 14,314 | Yes | `0x96eeac5402e5ceda9dc8c0b2c34354408430145e` |
| 69 | Septim USD | Ethereum (1) | 13,315 | Yes | `0x122c6032d6ab8c133f553e84d2433d6137302d5c` |
| 70 | Lighter AI multistrategy | Ethereum (1) | 13,271 | Yes | `0xaadc62356890f6c581d88bd06d310beb2a90f1b5` |
| 71 | AB Nexus USD Vault | Arbitrum (42161) | 12,293 | Yes | `0x14f2a9ebbd7b4d60b925053c6cc7900b48f96400` |
| 72 | Cross-Sectional Statistical Arbitrage | Arbitrum (42161) | 11,454 | Yes | `0x0f30d4cc24024e51bee5e7805bc7f3e6a88ea59f` |
| 73 | Master Vault | Arbitrum (42161) | 11,321 | Yes | `0x92e07d29b9982d719dc0b38d1a096b52e089d97c` |
| 74 | Fixed-Rate USDC Lending | Base (8453) | 11,087 | Yes | `0x79238962903c62865f319f660177e75652ee4780` |
| 75 | Test | Base (8453) | 11,000 | Yes | `0xe1dd29a45e930059f39dd5b77620d811012d2a41` |
| 76 | TiD Capital Avalanche Yield | Avalanche (43114) | 10,150 | Yes | `0xe33e36aac3d791b55d8bd3f1fc8fc84dc5515b5b` |
| 77 | Autonomous Liquidity Plus | Ethereum (1) | 10,091 | Yes | `0x5a97b0b97197299456af841f8605543b13b12ee3` |
| 78 | Smart Beta Sentiment 2X | Arbitrum (42161) | 10,090 | Yes | `0xb414a1f53b987e6b5a33546a165757d3f948b434` |
| 79 | LongUSDshortEURO x10 | Ethereum (1) | 10,003 | Yes | `0x2fb692ab4bb91ec5e3d2153d102220b19b7effdb` |
| 80 | DeTrade Applefarm USDC | Base (8453) | 10,000 | Yes | `0x551490f2379d4d814fb222bc877c0dbdf9d8fd6a` |
| 81 | Risk-Adjusted Yield Vault | Arbitrum (42161) | 9,642 | Yes | `0x9b96f5e55d55d38187dedaa68bebbf6983a479ff` |
| 82 | Scyther USD | Ethereum (1) | 9,188 | Yes | `0xb9516ba4687a170fbd8d24fb2f7f4b30e7ab42f8` |
| 83 | Dynamic Alpha Sentiment | Arbitrum (42161) | 9,117 | Yes | `0xa1c2c6eaee2999f04286c134e7fe3781f95fc0ea` |
| 84 | Resolved-USR | Ethereum (1) | 7,484 | Yes | `0x58126ea908ee01caaa2c9e52ca67a69220fe4089` |
| 85 | Nutria | Arbitrum (42161) | 7,421 | Yes | `0x7c43710b240d1810d9799001ec0964512ffdb077` |
| 86 | AI SMART-Mix Beta | Arbitrum (42161) | 7,132 | Yes | `0x6a8f28da57d471fe9401728cbe815f34d5eb34ad` |
| 87 | Dynamic Alpha Fundamental | Arbitrum (42161) | 6,298 | Yes | `0x17cb7a2056f0b9048d247cd16466d722bbe42e4e` |
| 88 | All-time high (Base) | Base (8453) | 5,300 | Yes | `0x7d8fab3e65e6c81ea2a940c050a7c70195d1504f` |
| 89 | aiUSDT | Hyperliquid (999) | 4,855 | Yes | `0x69c96a82b8534aae25b43644d5964c6b8f215676` |
| 90 | Gami Stake DAO USDC | Ethereum (1) | 3,419 | Yes | `0x33e1339567c183fbadcb43f72d11c47229d468ab` |
| 91 | Lagoon DeFi Vault | Arbitrum (42161) | 3,368 | Yes | `0x13b9f3709b40fc282b6b4def56b0aa1e877cd81a` |
| 92 | Hyper Futures Vault | Arbitrum (42161) | 3,303 | Yes | `0x8c9e5487bbb195a4d34245cf88cf71e7e9eb7472` |
| 93 | Memex memecoin index strategy (Base) | Base (8453) | 2,425 | Yes | `0xdc3c7b473fb67739074e3d5483a9e381d3337ac5` |
| 94 | Tulipa TAC USR | Ethereum (1) | 1,805 | Yes | `0x33c12e0183a3f0c63db925a333da3ec27831624a` |
| 95 | Test SCR | Ethereum (1) | 1,664 | Yes | `0x2f6396db30eeaea8248cd5547da6bed8a13479c9` |
| 96 | Gami Capital lvlUSD | Ethereum (1) | 1,604 | Yes | `0x59b7942f7d2afd085691ce65c152e0d38d4eff22` |
| 97 | Yield Algo Trading | Arbitrum (42161) | 1,335 | Yes | `0x99cd0b8b32b15922f0754fddc21323b5278c5261` |
| 98 | Based Autonomous Liquidity USD | Base (8453) | 1,060 | Yes | `0xb523eee5d77fa3e5a03e4fcd45cdd2b2c762be58` |
| 99 | STABLE RECURRING REVENUES | Ethereum (1) | 987 | Yes | `0xca76fd338ecf8d7b152bd6e9914ffbf155cbe5c6` |
| 100 | Hub Capital crvUSD Vault | Ethereum (1) | 475 | Yes | `0x9a458370556a68bdc130a80c35b294d0ad947a6a` |
| 101 | Bayalis USD | Ethereum (1) | 424 | Yes | `0x5029db6c80de46b6fea2835cfbfe49b9d97c46eb` |
| 102 | For Yield v2 | Base (8453) | 316 | Yes | `0x2bff679b1a9fbcc202316c1402172747ba2fbf56` |
| 103 | LiquidityLand Yield Strategy Vault | Ethereum (1) | 162 | Yes | `0xb68890e973439c01db9d9cd4d8acd64d7844b829` |
| 104 | Test Plupen Stable Compounder | Base (8453) | 80 | Yes | `0xd2685507978190f4a49048732d0039c924c2451f` |
| 105 | MuDigital Tulipa USDC | Monad (143) | 77 | Yes | `0x0da39b740834090c146dc48357f6a435a1bb33b3` |
| 106 | BOLD friendly forks Earn | Ethereum (1) | 56 | Yes | `0x68c2243619760abe0982378197c8fef2f651bd31` |
| 107 | DeTrade Applefarm USDC | Base (8453) | 22 | Yes | `0x0a63471da694fc822f5a0a0b65978dda8c8edbe5` |
| 108 | Yield-ETH Algo Trading | Arbitrum (42161) | 15 | Yes | `0xc0335a0f2f19d9e578be4e031490d0c14ad36735` |
| 109 | Noon STS USDC | Ethereum (1) | 13 | Yes | `0xcf0abd5c22f0beac4dcbce0eda04a7e01251bb53` |
| 110 | Coco Vault USDC Core | Ethereum (1) | 7 | Yes | `0x51ad629843790388505220f8f2c4817efede4f9b` |
| 111 | ForYield USDFY | Base (8453) | 4 | Yes | `0x075f4d545fda11cd0750ff5c6d2859858d2dfd18` |
| 112 | DeTrade AUSD-MON Uni v4 Delta-Neutral | Monad (143) | 1 | Yes | `0x73758f23fa72d85438a27a9df6220244550e020d` |
| 113 | MoneyFi FlowForge Vault | Base (8453) | 0 | Yes | `0x4efc07dca8697792119484af33549f33ab11bf3c` |
| 114 | DeTrade Curve LST Delta-Neutral | Monad (143) | 0 | Yes | `0x2b7c3d4f7066ae24c483a361e7778632c24f95ae` |
| 115 | TID Capital Arbitrum Yield | Arbitrum (42161) | 0 | Yes | `0x647b5ad322c9173a8d3bdf8041b8bddfc456ddc4` |
| 116 | Noon tacUSN | Ethereum (1) | 0 | Yes | `0x7895a046b26cc07272b022a0c9bafc046e6f6396` |
| 117 | Flint USD | Ethereum (1) | 0 | Yes | `0x7f35dea44a192764aa50d50e5f0ece1d5a8b0e45` |
| 118 | Odyssey Stablecoins Discretionary | Ethereum (1) | 0 | Yes | `0xea5e87d05c883fb51c2437466e003811001a2e25` |
| 119 | Volta90 Multi-asset trading strategy | Arbitrum (42161) | 0 | Yes | `0x9e6de1f581d63129af453fe1ea7253b280b4dca8` |

## Follow-up boundaries

Ten of the 14 missing-description entries without an `unofficial` flag are reviewed private deployments with no public description text in their detail responses: TowerBridge, Der base USDC, Der USDC, Muchacho USDC, Odyssey Stablecoins Discretionary, Angmar Capital, Dynamic Alpha Fundamental 2X, and the three Kamui vaults (Stable, Balanced and Boosted). Their empty descriptions should not be treated as evidence of a broken fetch. Moon Digital AM USDC is separately flagged `paused` and has a note that it is missing from the protocol website.

Missing source text and missing exported text need separate follow-up. A live exact-address API check during the audit found text for 22 of the 116 entries with successful detail responses; the other three missing-description entries returned HTTP 404. Those 22 entries were all outside Lagoon’s paginated public catalogue and all flagged unofficial in the export. Source text availability alone does not establish public inclusion or export eligibility. The normal adapter metadata cache uses the public catalogue plus a reviewed private-vault allowlist.

The live public catalogue had 58 vaults. Two lacked both descriptions at the source: 722Capital-ETH on Base (`0xfce2064b4221c54651b21c868064a23695e78f09`) and Avalanche USDC (`0x3048925b3ea5a8c12eecccb8810f5f7544db54af`). The former was absent from the published export; the latter appears above under its exported name, Turtle Avalanche USDC.

This is a dated completeness inventory, not a factual review of existing strategy descriptions or classifications. Any later metadata repair needs a separate implementation and the repository’s numbered migration workflow.
