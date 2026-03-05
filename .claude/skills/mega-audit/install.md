# Installing smart contract auditing skill dependencies

This guide covers how to install all required tools and dependencies for each auditing skill repo
used by the mega-audit pipeline.


## Quick reference

| Skill repo | Pure Markdown | External tools needed | npm/pip install |
|------------|:------------:|----------------------|:---------------:|
| [trailofbits/skills](https://github.com/trailofbits/skills) | Mostly | Semgrep, CodeQL, YARA (optional per plugin) | No |
| [pashov/skills](https://github.com/pashov/skills) | Yes | None | No |
| [Cyfrin/solskill](https://github.com/Cyfrin/solskill) | Yes | None | No |
| [kadenzipfel/scv-scan](https://github.com/kadenzipfel/scv-scan) | Yes | None | No |
| [forefy/.context](https://github.com/forefy/.context) | Yes | None | No |
| [quillai-network/qs_skills](https://github.com/quillai-network/qs_skills) | Yes | None | No |
| [Archethect/sc-auditor](https://github.com/Archethect/sc-auditor) | No | Slither, Aderyn, Solodit API key | npm install |
| [hackenproof-public/skills](https://github.com/hackenproof-public/skills) | Yes | None | No |
| [auditmos/skills](https://github.com/auditmos/skills) | Yes | None | No |
| [Frankcastleauditor/safe-solana-builder](https://github.com/Frankcastleauditor/safe-solana-builder) | Yes | None | No |
| [The-Membrane/membrane-core](https://github.com/The-Membrane/membrane-core/tree/new-age-cdp/.claude/skills/contract-audit) | Yes | None | No |

**9 out of 11 repos are pure Markdown** — only Archethect/sc-auditor requires real tool installation.

## Skill repo READMEs and installation instructions

| Skill repo | README / installation link |
|------------|---------------------------|
| trailofbits/skills | [README.md](https://github.com/trailofbits/skills/blob/main/README.md) |
| pashov/skills | [README.md](https://github.com/pashov/skills/blob/main/README.md) |
| Cyfrin/solskill | [README.md](https://github.com/Cyfrin/solskill/blob/main/README.md) |
| kadenzipfel/scv-scan | [README.md](https://github.com/kadenzipfel/scv-scan/blob/main/README.md) |
| forefy/.context | [README.md](https://github.com/forefy/.context/blob/main/README.md) |
| quillai-network/qs_skills | [README.md](https://github.com/quillai-network/qs_skills/blob/main/README.md) |
| Archethect/sc-auditor | [README.md](https://github.com/Archethect/sc-auditor/blob/main/README.md) |
| hackenproof-public/skills | [README.md](https://github.com/hackenproof-public/skills/blob/main/README.md) |
| auditmos/skills | [README.md](https://github.com/auditmos/skills/blob/main/README.md) |
| Frankcastleauditor/safe-solana-builder | [README.md](https://github.com/Frankcastleauditor/safe-solana-builder/blob/main/README.md) |
| The-Membrane/membrane-core (contract-audit) | [README.md](https://github.com/The-Membrane/membrane-core/blob/new-age-cdp/.claude/skills/contract-audit/README.md) |

## External tool READMEs and installation instructions

| Tool | Purpose | README / install docs |
|------|---------|----------------------|
| Foundry (forge) | Download verified contract source code via `forge clone` | [README.md](https://github.com/foundry-rs/foundry/blob/master/README.md), [Installation guide](https://book.getfoundry.sh/getting-started/installation) |
| Slither | Static analysis for Solidity | [README.md](https://github.com/crytic/slither/blob/master/README.md), [Installation guide](https://github.com/crytic/slither#how-to-install) |
| Aderyn | Static analysis for Solidity | [README.md](https://github.com/Cyfrin/aderyn/blob/dev/README.md), [Installation guide](https://github.com/Cyfrin/aderyn#quickstart) |
| Semgrep | Pattern-based static analysis (Trail of Bits plugin) | [README.md](https://github.com/semgrep/semgrep/blob/develop/README.md), [Installation guide](https://semgrep.dev/docs/getting-started/) |
| CodeQL | Variant analysis (Trail of Bits plugin) | [README.md](https://github.com/github/codeql-cli-binaries/blob/main/README.md), [Installation guide](https://docs.github.com/en/code-security/codeql-cli/getting-started-with-the-codeql-cli/setting-up-the-codeql-cli) |
| YARA | Malware pattern matching (Trail of Bits plugin) | [README.md](https://github.com/VirusTotal/yara/blob/master/README.md), [Documentation](https://yara.readthedocs.io/en/stable/gettingstarted.html) |
| solc-select | Solidity compiler version manager | [README.md](https://github.com/crytic/solc-select/blob/dev/README.md) |
| Node.js | Runtime for Archethect MCP server | [Installation guide](https://nodejs.org/en/download) |
| Rust toolchain (rustup) | Required for Aderyn and Zeroize audit plugin | [Installation guide](https://www.rust-lang.org/tools/install) |
| Solodit | Historical vulnerability search API | [Website](https://solodit.cyfrin.io), API key registration in the API Keys section |
| web3.py | On-chain queries during deployment analysis | [README.md](https://github.com/ethereum/web3.py/blob/main/README.md), [Documentation](https://web3py.readthedocs.io/en/stable/quickstart.html) |

## Prerequisites

These are needed regardless of which skill repos you use:

- **Foundry** (`forge`) — for downloading verified contract source code via `forge clone`
  ```bash
  curl -L https://foundry.paradigm.xyz | bash
  foundryup
  ```

- **Python 3.11+** with web3.py — for on-chain queries during deployment analysis
  ```bash
  pip install web3
  ```

## Archethect/sc-auditor (MCP server)

This is the only repo that requires real software installation. It provides 4 MCP tools:
`run-slither`, `run-aderyn`, `get_checklist`, and `search_findings`.

### Required: Node.js and npm

```bash
# Node.js >= 22 required
node --version  # must be >= 22

# Install the MCP server
cd <working-dir>/archethect-sc-auditor
npm install
npm run build
```

### Required: Solodit API key

Register at [solodit.cyfrin.io](https://solodit.cyfrin.io) and generate an API key in the API Keys section.

```bash
# Set in environment or .env file in the Solidity project root
export SOLODIT_API_KEY=sk_your_key_here
```

### Slither (static analysis)

```bash
# Install Slither
uv tool install slither-analyzer

# Install the correct solc version for the target contract
pip install solc-select
solc-select install 0.8.28
solc-select use 0.8.28

# Verify
slither --version
```

### Optional: Aderyn (static analysis)

```bash
# Requires Rust toolchain
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# Install Aderyn
cargo install aderyn

# Verify
aderyn --version
```

### Configuration

Create `config.json` in the Solidity project root (all fields optional):

```json
{
  "default_severity": ["CRITICAL", "HIGH", "MEDIUM"],
  "default_quality_score": 2,
  "report_output_dir": "audits",
  "static_analysis": {
    "slither_enabled": true,
    "slither_path": "slither",
    "aderyn_enabled": true,
    "aderyn_path": "aderyn"
  }
}
```

### MCP registration

Add to your `.mcp.json` or Claude Code settings:

```json
{
  "mcpServers": {
    "sc-auditor": {
      "type": "stdio",
      "command": "node",
      "args": ["<path-to>/archethect-sc-auditor/dist/mcp/main.js"]
    }
  }
}
```

**Note:** The sc-auditor skill and methodology (Map-Hunt-Attack) work as pure Markdown even
without the MCP tools installed. The MCP server just adds automated static analysis and
Solodit search capabilities.

## Trail of Bits skills (optional tools)

Most Trail of Bits skills are pure Markdown. Some specialised plugins require additional tools:

| Plugin | Tool required | Install command |
|--------|--------------|-----------------|
| Static analysis (Semgrep) | Semgrep | `pip install semgrep` or `brew install semgrep` |
| Variant analysis (CodeQL) | CodeQL CLI | Download from [github.com/github/codeql-cli-binaries](https://github.com/github/codeql-cli-binaries) |
| YARA authoring | YARA | `brew install yara` or `apt install yara` |
| Zeroize audit | Rust toolchain | `rustup` (see above) |
| Culture index | Python packages | `pip install opencv-python numpy pdf2image pytesseract` |


## Running without any tool installation

If you cannot install Slither, Aderyn, or the MCP server, the mega-audit pipeline still works.
All 11 skill repos provide Markdown-based audit methodologies that Claude Code can follow
directly. You lose:

- Automated static analysis (Slither/Aderyn findings)
- Solodit historical vulnerability search
- Cyfrin checklist loading via API

You keep:

- All vulnerability pattern knowledge bases (36+ vulnerability types from scv-scan alone)
- Parallelised multi-agent audit pipelines (pashov)
- Protocol-specific reference libraries (forefy — lending, DEX, bridges, etc.)
- OWASP Smart Contract Top 10 methodology (quillai)
- 14 specialised DeFi vulnerability skills (auditmos)
- Map-Hunt-Attack manual methodology (Archethect)
- Triage workflow (hackenproof)
- Coding standards (Cyfrin)
- Solana security rules (safe-solana-builder — Anchor + Native Rust)
- CosmWasm audit patterns from 61 Oak Security reports (membrane-core)
