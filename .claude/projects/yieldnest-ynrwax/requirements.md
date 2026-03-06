# Mega-audit pipeline requirements

## Date

2026-03-06

## Target

YieldNest ynRWAx Vault v0.4.2 — Ethereum mainnet at `0x01Ba69727E2860b37bc1a2bd56999c1aFb4C15D8`

## Software used

### Source code retrieval

| Tool | Version | Purpose | Installation |
|------|---------|---------|-------------|
| Blockscout V2 API | Public API | Downloaded 60 verified source files (main + 59 additional) | No installation — HTTP API at `eth.blockscout.com/api/v2/smart-contracts/{address}` |

Note: `forge clone` was attempted first but failed due to missing Etherscan API key. Sourcify also failed (contract not verified there). Blockscout V2 API worked without authentication.

### Static analysis tools (available but partially used)

| Tool | Version | Purpose | Installation | Status |
|------|---------|---------|-------------|--------|
| Slither | 0.11.5 | Static analysis for Solidity | `uv tool install slither-analyzer` | Available but failed on Blockscout-extracted source (missing remappings) |
| Aderyn | 0.6.8 | Static analysis for Solidity | `cargo install aderyn` | Available but failed on Blockscout-extracted source (missing remappings) |
| Semgrep | 1.154.0 | Pattern-based static analysis | `brew install semgrep` | Available, not used (Trail of Bits skill uses manual analysis) |
| Foundry (forge) | 1.2.3 | Source code download | `curl -L https://foundry.paradigm.xyz \| bash && foundryup` | Available but `forge clone` requires Etherscan API key |
| Node.js | 20.19.5 | Archethect MCP server runtime | Pre-installed | Available |

### On-chain analysis

| Tool | Version | Purpose |
|------|---------|---------|
| web3.py | (via Poetry environment) | On-chain role verification, proxy slot reading, address type analysis, event log scanning |
| JSON-RPC (Ethereum archive node) | via `JSON_RPC_ETHEREUM` env var | Block queries for deployment history |

### Audit skill repos

All 9 skill repos were cloned at `--depth 1` into `.claude/projects/yieldnest-ynrwax/`:

| # | Skill repo | Version (commit) | Software needed | Applicable |
|---|-----------|------------------|-----------------|------------|
| 1 | trailofbits/skills | HEAD (2026-03-06) | None (pure Markdown) | Yes — Solidity vulnerability scanner |
| 2 | pashov/skills | HEAD (2026-03-06) | None (pure Markdown) | Yes — parallelised audit pipeline |
| 3 | Cyfrin/solskill | HEAD (2026-03-06) | None (pure Markdown) | Yes — coding standards review |
| 4 | kadenzipfel/scv-scan | HEAD (2026-03-06) | None (pure Markdown) | Yes — 36 vulnerability types |
| 5 | forefy/.context | HEAD (2026-03-06) | None (pure Markdown) | Yes — multi-expert framework |
| 6 | quillai-network/qs_skills | HEAD (2026-03-06) | None (pure Markdown) | Yes — OWASP Smart Contract Top 10 |
| 7 | Archethect/sc-auditor | HEAD (2026-03-06) | Slither, Aderyn (optional) | Yes — Map-Hunt-Attack (manual mode, MCP tools not used) |
| 8 | hackenproof-public/skills | HEAD (2026-03-06) | None (pure Markdown) | Yes — triage workflow |
| 9 | auditmos/skills | HEAD (2026-03-06) | None (pure Markdown) | Yes — 14 DeFi vulnerability skills |

### Skipped skill repos

| Repo | Reason |
|------|--------|
| Frankcastleauditor/safe-solana-builder | Solana/Rust only — not applicable to Solidity |
| The-Membrane/membrane-core | CosmWasm only — not applicable to Solidity |

## Execution

- 10 parallel agents were launched (9 audit pipelines + 1 deployment analysis)
- Each agent independently read the source code and applied its methodology
- Agents ran as Claude Code Task subagents with `run_in_background: true`
- Total execution time: ~6-7 minutes for all agents
- Total API token usage: ~900k+ tokens across all agents

## LLM

- Model: Claude Opus 4.6 (`claude-opus-4-6`)
- All agents used the same model

## Output

All reports saved to `.claude/projects/yieldnest-ynrwax/reports/`:
- `trailofbits-skills.md`
- `pashov-skills.md`
- `kadenzipfel-scv-scan.md`
- `forefy-context.md`
- `quillai-qs-skills.md`
- `archethect-sc-auditor.md`
- `cyfrin-solskill.md`
- `hackenproof-skills.md`
- `auditmos-skills.md`
- `ai-audit-summary.md` (deduplicated cross-pipeline summary)
- `../deployment.md` (on-chain deployment analysis)
