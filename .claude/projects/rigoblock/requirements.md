# Rigoblock SmartPool mega-audit — requirements and tooling

## Summary

This document records all software used to run the 9-pipeline mega-audit of the Rigoblock SmartPool contracts.

## Core tools

| Tool | Version | How installed | Purpose |
|------|---------|---------------|---------|
| Foundry (forge) | 1.2.3 | `curl -L https://foundry.paradigm.xyz \| bash && foundryup` | Download verified source code via `forge clone` |
| Slither | 0.11.5 | `uv tool install slither-analyzer` | Static analysis (used by Archethect pipeline) |
| Aderyn | 0.6.8 | `cargo install aderyn` | Static analysis (used by Archethect pipeline) |
| Semgrep | 1.154.0 | `brew install semgrep` | Pattern-based analysis (available for Trail of Bits) |
| Node.js | v20.19.5 | System install | Required for Archethect MCP server |
| solc | 0.8.28 | `solc-select install 0.8.28 && solc-select use 0.8.28` | Solidity compiler for Slither |
| Python | 3.11+ | System | web3.py for on-chain queries |

## Skill repos used (all 9 Solidity-applicable repos)

| # | Repo | Version/commit | Installation | Notes |
|---|------|---------------|-------------|-------|
| 1 | [trailofbits/skills](https://github.com/trailofbits/skills) | Latest main | `git clone` — pure Markdown, no install | 58 skills, used Solidity vulnerability scanner |
| 2 | [pashov/skills](https://github.com/pashov/skills) | Latest main | `git clone` — pure Markdown, no install | Parallelised 4-agent audit pipeline |
| 3 | [Cyfrin/solskill](https://github.com/Cyfrin/solskill) | Latest main | `git clone` — pure Markdown, no install | Solidity security standards |
| 4 | [kadenzipfel/scv-scan](https://github.com/kadenzipfel/scv-scan) | Latest main | `git clone` — pure Markdown, no install | 36 vulnerability types knowledge base |
| 5 | [forefy/.context](https://github.com/forefy/.context) | Latest main | `git clone` — pure Markdown, no install | Multi-expert framework with protocol references |
| 6 | [quillai-network/qs_skills](https://github.com/quillai-network/qs_skills) | Latest main | `git clone` — pure Markdown, no install | OWASP Smart Contract Top 10 methodology |
| 7 | [Archethect/sc-auditor](https://github.com/Archethect/sc-auditor) | Latest main | `git clone && npm install && npm run build` | MCP server with Slither/Aderyn integration |
| 8 | [hackenproof-public/skills](https://github.com/hackenproof-public/skills) | Latest main | `git clone` — pure Markdown, no install | Bug bounty triage workflow |
| 9 | [auditmos/skills](https://github.com/auditmos/skills) | Latest main | `git clone` — pure Markdown, no install | 14 DeFi vulnerability skills |

## Repos not applicable

| Repo | Reason skipped |
|------|---------------|
| [Frankcastleauditor/safe-solana-builder](https://github.com/Frankcastleauditor/safe-solana-builder) | Solana/Rust only — target is Solidity |
| [The-Membrane/membrane-core](https://github.com/The-Membrane/membrane-core) | CosmWasm only — target is Solidity |

## Static analysis results

- **Slither**: 71 findings total (3 medium, 68 low/informational)
- **Aderyn**: 16 findings total (2 high, 14 low)

Both were run by the Archethect pipeline agent and findings were incorporated into its report.

## AI model

- **Claude Opus 4.6** — all 9 audit agents ran on this model
- **Orchestration**: Claude Code mega-audit skill with parallel agent dispatch

## Environment

- macOS Darwin 24.6.0
- Etherscan API used for source code download (via `forge clone`)
- JSON-RPC not required (no on-chain state queries needed beyond Etherscan)

## Reproducibility

To reproduce this audit:
1. Install Foundry, Slither, Aderyn, Semgrep, Node.js, solc-select
2. Clone all 9 skill repos into a working directory
3. Run `forge clone` with the proxy and implementation addresses
4. Run Claude Code with the mega-audit skill, providing the contract URL
5. The skill will dispatch 9 parallel agents, each following its repo's methodology
