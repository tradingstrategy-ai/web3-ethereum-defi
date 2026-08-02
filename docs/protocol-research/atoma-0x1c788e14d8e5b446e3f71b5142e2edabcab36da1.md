# Atoma Vault Share 2 protocol research

- **Chain:** Arbitrum One (42161)
- **Address:** `0x1C788E14d8e5B446e3F71B5142e2edaBcAB36da1`
- **Explorer:** [Arbiscan](https://arbiscan.io/address/0x1C788E14d8e5B446e3F71B5142e2edaBcAB36da1), [Blockscout](https://arbitrum.blockscout.com/address/0x1C788E14d8e5B446e3F71B5142e2edaBcAB36da1)
- **Deployer:** `0x383baa22cC8b537Fae93Ee419Aff0590823e75DB` (no public explorer name)
- **Protocol name:** Atoma
- **Web page:** [Atoma](https://atoma.fi/)
- **Application:** [Atoma app](https://app.atoma.fi/)
- **GitHub repository:** No public Atoma vault contract repository was found. The proxy and implementation source are verified on Blockscout.
- **Documentation:** No separate Atoma vault documentation site was found.
- **DefiLlama:** [Atoma protocol listing](https://defillama.com/protocol/atoma), [ERC-4626 registry source](https://github.com/DefiLlama/DefiLlama-Adapters/blob/main/registries/erc4626.js)
- **Audits:** No public audit was found. The Atoma HackQuest project page says the team was seeking smart contract audit sponsorship before scaling beyond $1 million TVL.
- **Fee information:** The verified `AtomaVault` implementation declares a 20% high-water-mark performance fee, a 0.5% withdrawal fee, and a 100 USDC minimum deposit. No management fee is present in the verified source.
- **Notes:** The address is a verified ERC-1967 proxy whose current implementation is [`AtomaVault` at `0x9521B08303AE010e85e24fC15D5334A0E506641E`](https://arbitrum.blockscout.com/address/0x9521B08303AE010e85e24fC15D5334A0E506641E). Its initialiser names the share token `Atoma Vault Share 2` with symbol `AVS2`. Atoma's [RWA vault launch post](https://x.com/atoma_fi/status/2079672209400832319?s=46) describes AVS2 as a delta-neutral strategy for gold, oil and equity-index perpetuals, using offsetting Lighter and Trade.xyz positions to capture funding and price spreads. The verified source exposes the same `epochDuration()`, `requestWithdrawal()` and `claimWithdrawal()` interface used by the existing Atoma adapter, so it should map to `ERC4626Feature.atoma_like`.
