# Requirements to run the mega-audit pipeline

## Source code acquisition

- **Blockscout API** (no API key required): `GET https://eth.blockscout.com/api?module=contract&action=getsourcecode&address=0x...`
- Etherscan V2 API requires an API key; Sourcify did not have this contract verified
- The source code was returned as a single flattened Solidity file (930 lines)

## Software used

### Core tools

| Tool | Version | Installation | Notes |
|------|---------|-------------|-------|
| Claude Code (Opus 4.6) | 2.1.61 | Pre-installed | Main orchestrator and auditing agent |
| git | system | Pre-installed | Cloning skill repos |
| curl | system | Pre-installed | Fetching contract source via Blockscout API |
| Python 3 | system | Pre-installed | Parsing API responses |

### Skill repos (all cloned with `git clone --depth 1`)

| Repo | Commit | Installation required | Notes |
|------|--------|----------------------|-------|
| [trailofbits/skills](https://github.com/trailofbits/skills) | latest | None | Pure Markdown knowledge base |
| [pashov/skills](https://github.com/pashov/skills) | latest | None | Pure Markdown knowledge base |
| [kadenzipfel/scv-scan](https://github.com/kadenzipfel/scv-scan) | latest | None | Pure Markdown knowledge base with grep-based scanning |
| [forefy/.context](https://github.com/forefy/.context) | latest | None | Pure Markdown knowledge base |
| [quillai-network/qs_skills](https://github.com/quillai-network/qs_skills) | latest | None | Pure Markdown knowledge base |
| [auditmos/skills](https://github.com/auditmos/skills) | latest | None | Pure Markdown knowledge base |
| [Cyfrin/solskill](https://github.com/Cyfrin/solskill) | latest | None | Pure Markdown knowledge base |

### Skipped skill repos

| Repo | Reason |
|------|--------|
| [Archethect/sc-auditor](https://github.com/Archethect/sc-auditor) | Requires MCP server with Slither and Aderyn integration (TypeScript, npm install); would need separate tool installation |
| [hackenproof-public/skills](https://github.com/hackenproof-public/skills) | Bug bounty triage workflow referencing internal HackenProof tools; not applicable for external audit |

## Pipeline execution

- **8 agents** ran in parallel total: 1 deployment info agent + 7 audit agents
- Batch 1 (4 parallel agents): trailofbits, pashov, kadenzipfel, forefy
- Batch 2 (3 parallel agents): quillai, auditmos, cyfrin
- **Total wall-clock time:** ~6 minutes (all agents ran concurrently)
- **No external software installation was required** -- all skill repos are pure Markdown knowledge bases that Claude reads as reference material

## Output files

```
.claude/projects/euler-earn/
  src/EulerEarn.sol           # Downloaded source code
  abi/EulerEarn.json          # Downloaded ABI (149 entries)
  deployment.md               # Deployment info and privileged addresses
  reports/
    pashov-skills.md          # Pashov solidity-auditor report
    kadenzipfel-scv-scan.md   # SCV-scan vulnerability report
    forefy-context.md         # Forefy multi-expert audit report
    trailofbits-skills.md     # Trail of Bits combined skills report
    quillai-skills.md         # QuillAI combined skills report
    auditmos-skills.md        # Auditmos combined skills report
    cyfrin-solskill.md        # Cyfrin code quality assessment
    ai-audit-summary.md       # Deduplicated consolidated summary
  requirements.md             # This file
```

## Reproducibility

To reproduce this audit:

1. Clone this repository
2. Run `git clone --depth 1` for each of the 7 skill repos listed above into `.claude/projects/euler-earn/`
3. Download the source code from Blockscout API
4. Use Claude Code (Opus 4.6 or later) to run each skill's SKILL.md methodology against the source code
5. Synthesise findings into a deduplicated summary

No special API keys, environment variables, or external tool installations are needed beyond Claude Code and git.
