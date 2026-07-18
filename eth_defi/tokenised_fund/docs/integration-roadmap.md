# Tokenised fund integration roadmap

This roadmap turns the July 2026 tokenised-fund contract research into
integration tracks. Each track requires a dedicated protocol implementation and
the corresponding verified curator mapping. Contract addresses are hardcoded
only after the adapter has verified the contract family and public lifecycle.

## Already covered

- Securitize: BUIDL and VBILL
- Kinexys: JLTXX
- Midas: mTBILL

## EVM integration tracks

| Track | Products and hardcoded EVM addresses | Curator evidence to establish |
| --- | --- | --- |
| Hashnote / Circle | USYC: `0x136471a34f6ef19fe571effc1ca711fdb8e49f2b` | Hashnote and/or Circle role in the fund lifecycle |
| Ondo | USDY: `0x96f6ef951840721adbf46ac996b59e0235cb985c`; OUSG: `0x1b19c19393e2d034d8ff31ff34c81252fcbbee92` | Ondo Finance protocol-managed fund role |
| Franklin Templeton | iBENJI: `0x90276e9d4a023b5229e0c2e9d4b2a83fe3a2b48c`; BENJI: `0x3ddc84940ab509c11b20b76b466933f40b750dc9` | Franklin Templeton issuer/manager role |
| Centrifuge | JTRSY: `0x8c213ee79581ff4984583c6a801e5263418c4b86` | Centrifuge/Anemoy platform and Janus Henderson investment-manager roles |
| WisdomTree | WTGXX: `0x1fecf3d9d4fee7f2c02917a66028a48c6706c179` | WisdomTree fund-manager role |
| Superstate | USTB: `0x43415eb6ff9db7e26a15b704e7a3edce97d31c4e` | Superstate issuer/manager role |
| Libeara CMTAT | CUMIU: `0x85d38585c3ac08268f598282a84b7c0ddfc0d04f`; BELIF: `0x237c717df1b60501f8d029d3fe7385fd090df180` | ChinaAMC/Bosera fund managers and Libeara platform roles |
| Spiko | USTBL: `0xe4880249745eac5f1ed9d8f7df844792d560e750` | Spiko fund-manager role |
| Kinexys FACT extension | MONY: `0x6a7c6aa2b8b8a6a891de552bdeffa87c3f53bd46` | J.P. Morgan/Kinexys fund-management role |
| Theo | thBILL: `0x5fa487bca6158c64046b2813623e20755091da0b` | Theo protocol-managed fund role |
| Libeara Delta | ULTRA: `0xc26af85ede9cc25d449bcebef866bb85afd5d346` | Wellington manager and Libeara platform roles |
| Sygnum | FILQ: `0x54a4fc78431f9201824643e99bec891bb7462a1d` | Fidelity International manager and Sygnum platform roles |
| DTCC / Fidelity | FDIT: `0x48ab4e39ac59f4e88974804b04a991b3a402717f` | Fidelity Investments manager role |
| KAIO | CASHx: `0x42975aae7a124257e7fda7f5e8382f51449b784a` | BlackRock manager and KAIO platform roles |
| Zeconomy | DCP: `0xb5710a6fede27d1048c75b157bd3403ba08cdbe0` | Guggenheim issuer and Zeconomy platform roles |
| OpenEden repair | TBILL: `0xdd50c053c096cb04a3e3362e2b622529ec5f2e8a` | OpenEden protocol-managed fund role |

## Deferred tracks

- SWEEP: the Solana launch has no public mint or programme identifier, and the
  current vault pipeline is EVM-only.
- Stellar-only representations: gBENJI and the Stellar variants of BENJI and
  JTRSY require Stellar ingestion support before they can be added.
