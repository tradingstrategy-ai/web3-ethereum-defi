# Claude Code smart contract auditing skills

Evaluation of Claude Code skill repositories for smart contract security auditing.

Source: https://x.com/moo9000/status/2029241982657139165 (2026-03-04), https://x.com/0xcastle_chain/status/2029540253514469859 (2026-03-05)

Data collected: 2026-03-05

## Summary table

| Repo | Stars | Skills | Lines | Languages | Contributors | Twitter |
|------|------:|-------:|------:|-----------|--------------|---------|
| [trailofbits/skills](https://github.com/trailofbits/skills) | 3,274 | 58 | 73,636 | Solidity, Cairo, Cosmos, Algorand, Substrate, Solana (Move), Go, Rust, Python, C/C++ | [dguido](https://github.com/dguido), [Ninja3047](https://github.com/Ninja3047), [GrosQuildu](https://github.com/GrosQuildu), [ahpaleus](https://github.com/ahpaleus), [dariushoule](https://github.com/dariushoule), [DarkaMaul](https://github.com/DarkaMaul), [hbrodin](https://github.com/hbrodin), [bsamuels453](https://github.com/bsamuels453), [mosajjal](https://github.com/mosajjal), [frabert](https://github.com/frabert), [sblackshear](https://github.com/sblackshear), [vanhauser-thc](https://github.com/vanhauser-thc) + 7 more | [@trailofbits](https://x.com/trailofbits) |
| [pashov/skills](https://github.com/pashov/skills) | 156 | 1 | 1,461 | Solidity | [pashov](https://github.com/pashov), [Daneided](https://github.com/Daneided) | [@pashov](https://x.com/pashov) |
| [Cyfrin/solskill](https://github.com/Cyfrin/solskill) | 96 | 1 | 350 | Solidity | [PatrickAlphaC](https://github.com/PatrickAlphaC) | [@PatrickAlphaC](https://x.com/PatrickAlphaC) |
| [kadenzipfel/scv-scan](https://github.com/kadenzipfel/scv-scan) | 77 | 1 | 2,784 | Solidity | [kadenzipfel](https://github.com/kadenzipfel) | [@0xkaden](https://x.com/0xkaden) |
| [forefy/.context](https://github.com/forefy/.context) | 70 | 3 | 15,371 | Solidity, Anchor (Solana), Vyper | [forefy](https://github.com/forefy) | [@forefy](https://x.com/forefy) |
| [quillai-network/qs_skills](https://github.com/quillai-network/qs_skills) | 62 | 10 | 8,528 | Solidity | [ChitranshVashney](https://github.com/ChitranshVashney), [michaeldim](https://github.com/michaeldim) | [@QuillAudits_AI](https://x.com/QuillAudits_AI) |
| [Archethect/sc-auditor](https://github.com/Archethect/sc-auditor) | 47 | 1 + 4 MCP tools | 1,285 | Solidity | [Archethect](https://github.com/Archethect) | [@archethect](https://x.com/archethect) |
| [hackenproof-public/skills](https://github.com/hackenproof-public/skills) | 7 | 1 | 300 | Solidity, general web/mobile | [dorsky](https://github.com/dorsky) | [@d0rsky](https://x.com/d0rsky) |
| [auditmos/skills](https://github.com/auditmos/skills) | 0 | 14 | 12,981 | Solidity | [tkowalczyk](https://github.com/tkowalczyk) | [@tomkowalczyk](https://x.com/tomkowalczyk) |
| [Frankcastleauditor/safe-solana-builder](https://github.com/Frankcastleauditor/safe-solana-builder) | 47 | 1 | 1,607 | Rust (Solana Anchor + Native) | [Frankcastleauditor](https://github.com/Frankcastleauditor), [Arrowana](https://github.com/Arrowana) | [@0xcastle_chain](https://x.com/0xcastle_chain) |
| [The-Membrane/membrane-core](https://github.com/The-Membrane/membrane-core/tree/new-age-cdp/.claude/skills/contract-audit) | 10 | 1 | 3,267 | CosmWasm (Rust) | [triccs](https://github.com/triccs) | — |

**Total: ~93 skills across 11 repos, ~121,000 lines of skill content.**

## Detailed evaluations

### 1. trailofbits/skills

- **GitHub:** https://github.com/trailofbits/skills
- **Stars:** 3,274 | **Forks:** 254 | **Created:** 2026-01-14
- **Licence:** CC-BY-SA-4.0
- **Skills:** 58 skills across 35 plugins
- **Lines:** 73,636

**Description:** Trail of Bits Skills is the largest and most comprehensive Claude Code skill collection, providing 58 specialised skills across 35 plugins for security research, vulnerability detection, and audit workflows. The plugins span smart contract security (vulnerability scanners for 6 blockchain ecosystems), code auditing (Semgrep, CodeQL, differential review, variant analysis), malware analysis (YARA), reverse engineering, mobile security, and cryptographic verification. It has already been used to discover real bugs including a timing side-channel in ML-DSA signing in the RustCrypto library.

**Contributors (19):** Led by Dan Guido (888 followers), CEO of Trail of Bits. 8 of 19 contributors are Trail of Bits employees. Notable external contributors include vanhauser-thc (3,546 followers, maintainer of AFL++ and THC-Hydra) and sblackshear (395 followers, CTO of Mysten Labs, creator of the Move language).

---

### 2. pashov/skills

- **GitHub:** https://github.com/pashov/skills
- **Stars:** 156 | **Forks:** 24 | **Created:** 2026-02-23
- **Skills:** 1 (`solidity-auditor`)
- **Lines:** 1,461

**Description:** A single Claude Code skill that orchestrates a parallelised Solidity audit pipeline -- it spawns 4 concurrent scanning agents (each armed with different attack vector reference files) plus an optional 5th adversarial reasoning agent, then merges and deduplicates findings into a confidence-ranked report in under 5 minutes. Built by Pashov Audit Group, one of the most well-known solo-turned-group smart contract auditing firms. Designed as a fast pre-commit check rather than a replacement for formal audit.

**Contributors (2):** Led by Krum Pashov (944 followers), who reportedly earned $600k+ in 20 months of solo smart contract auditing. His `pashov/audits` repo (1,270 stars) is one of the most-starred personal audit portfolios in the Solidity security space.

---

### 3. Cyfrin/solskill

- **GitHub:** https://github.com/Cyfrin/solskill
- **Stars:** 96 | **Forks:** 16 | **Created:** 2026-02-18
- **Skills:** 1 (`solskill`)
- **Lines:** 350

**Description:** A single Claude Code skill providing production-grade Solidity development standards from the Cyfrin security team. It instructs Claude Code to follow defensive coding practices (custom errors over `require`, stateless fuzz testing over unit tests, FREI-PI invariant patterns, multisig-first governance), along with detailed function ordering, file layout, and CI pipeline recommendations. Essentially a comprehensive Solidity style guide and security-mindset ruleset packaged as a Claude Code skill.

**Contributors (1):** Patrick Collins (11,071 followers) -- one of the most prominent smart contract educators. His Cyfrin Foundry course repo has 5,704 stars. Cyfrin org has 2,979 followers and produces Aderyn (a Solidity static analyser with 735 stars).

---

### 4. kadenzipfel/scv-scan

- **GitHub:** https://github.com/kadenzipfel/scv-scan
- **Stars:** 77 | **Forks:** 8 | **Created:** 2026-02-09
- **Skills:** 1 (`/scv`)
- **Lines:** 2,784

**Description:** A pure-Markdown Claude Code skill that scans Solidity codebases for security vulnerabilities by referencing 36 unique vulnerability types derived from the author's companion repo `smart-contract-vulnerabilities` (2,435 stars). It uses a four-phase workflow: loading a condensed cheatsheet, performing syntactic grep and semantic read-through passes, deep-validating candidates against detailed reference files with false-positive checks, and outputting a severity-ranked report. The entire repo is pure Markdown with no executable code -- Claude reads the reference files as a knowledge base.

**Contributors (1):** kadenzipfel / 0xkaden (726 followers). Author of `smart-contract-vulnerabilities` (2,435 stars, 323 forks), which is the foundational vulnerability catalogue that scv-scan draws from.

---

### 5. forefy/.context

- **GitHub:** https://github.com/forefy/.context
- **Stars:** 70 | **Forks:** 13
- **Skills:** 3 (`smart-contract-audit`, `infrastructure-audit`, `auditor-quiz`)
- **Lines:** 15,371

**Description:** A collection of AI agent skills for security auditing of smart contracts and infrastructure, compatible with both Claude Code and GitHub Copilot. The smart contract audit skill provides a multi-expert framework for Solidity, Anchor (Solana), and Vyper codebases, producing triaged findings with code locations, proof-of-concept exploits, and attacker story flow graphs. Includes an extensive vulnerability pattern reference library covering 30 categories across 3 languages and 21 protocol-specific reference files (bridges, lending, DEXes, staking, derivatives, etc.).

**Contributors (1):** Tomer / forefy (11 followers), affiliated with @Auditware organisation. The Auditware org's `radar` static analysis tool has 134 stars. Focused presence in smart contract security tooling.

---

### 6. quillai-network/qs_skills

- **GitHub:** https://github.com/quillai-network/qs_skills
- **Stars:** 62 | **Forks:** not available
- **Skills:** 10
- **Lines:** 8,528

**Description:** A collection of 10 AI agent skills that teach AI coding assistants the QuillShield methodology for smart contract security auditing, covering the OWASP Smart Contract Top 10 and beyond. Each skill encodes a specific vulnerability detection methodology -- from reentrancy and oracle manipulation to proxy upgrade safety and signature replay attacks. Derived from QuillShield's Semantic State Protocol research, augmented with data from CertiK, Halborn, Trail of Bits, and OpenZeppelin methodologies plus real-world exploit post-mortems covering $10.77B+ in DeFi losses.

**Contributors (2):** Led by ChitranshVashney (11 followers) from @Quillhash, a well-known smart contract auditing firm. Notable stargazers include dguido (CEO of Trail of Bits) and kirk-baird (Security Assessments Manager at Sigma Prime).

---

### 7. Archethect/sc-auditor

- **GitHub:** https://github.com/Archethect/sc-auditor
- **Stars:** 47 | **Forks:** 9 | **Created:** 2026-02-24
- **Skills:** 1 skill + 4 MCP tools
- **Lines:** 1,285

**Description:** A full Claude Code plugin with an MCP server providing four automated tools -- Slither static analysis, Aderyn static analysis, Cyfrin audit checklist loading, and Solodit findings search -- plus an interactive `/security-auditor` skill following a structured SETUP-MAP-HUNT-ATTACK methodology. Unlike pure-Markdown skills, this is a TypeScript application that runs an MCP server, executes real static analysis tools, queries external APIs, and integrates findings into a hypothesis-driven audit workflow. Supports both Claude Code and OpenAI Codex CLI.

**Contributors (1):** Archethect (9 followers). Minimal public profile but the repo demonstrates solid engineering with cross-platform agent support.

---

### 8. hackenproof-public/skills

- **GitHub:** https://github.com/hackenproof-public/skills
- **Stars:** 7 | **Forks:** 0 | **Created:** 2026-02-18
- **Skills:** 1 (`hackenproof-triage-marketplace`)
- **Lines:** 300

**Description:** A single Claude Code skill providing a structured bug bounty triage workflow for HackenProof programs, with a 12-step mandatory tool sequence covering scope verification, commit/version matching, duplicate detection, PoC validation, and severity classification across web/mobile, smart contract, and blockchain protocol domains. Includes reference files for global vulnerability policy, severity mapping, and triage comment templates. The skill references internal HackenProof tools not included in the repo, making it partially non-functional for external users.

**Contributors (1):** dorsky (12 followers), CTO at HackenProof. Hacken is a well-known blockchain security company, giving the severity classification content genuine domain authority despite the modest GitHub presence.

---

### 9. auditmos/skills

- **GitHub:** https://github.com/auditmos/skills
- **Stars:** 0 | **Forks:** 0 | **Created:** 2025-12-30
- **Skills:** 14
- **Lines:** 12,981

**Description:** A collection of 14 Claude Code skills packaged as a single plugin, each targeting a specific DeFi vulnerability class -- auctions, concentrated liquidity management, lending, liquidation, math precision, oracle manipulation, reentrancy, signatures, slippage, staking, and state validation. Each skill includes structured SKILL.md workflow instructions, vulnerability checklists, code examples, reference materials, and report templates. Skills auto-trigger based on code patterns and user request keywords.

**Contributors (1):** Tomasz Kowalczyk / tkowalczyk (83 followers), based in Poland. Background in Xamarin/mobile development, pivoted to blockchain security through the Auditmos organisation. The repo has had zero community engagement since creation.

### 10. Frankcastleauditor/safe-solana-builder

- **GitHub:** https://github.com/Frankcastleauditor/safe-solana-builder
- **Stars:** 47 | **Forks:** 5 | **Created:** 2026-03-01
- **Licence:** MIT
- **Skills:** 1 (`safe-solana-builder`)
- **Lines:** 1,607

**Description:** A single Claude Code skill for writing production-grade, security-first Solana programs, supporting both Anchor and Native Rust frameworks. It assesses risk levels and applies security rules derived from 70+ real audit findings covering CPIs, PDAs, account validation, arithmetic, and Token-2022 compatibility. The skill generates full project scaffolds with inline security documentation, test skeletons with edge cases pre-mapped, and a security checklist documenting every applied rule. Includes a SKILL.md orchestrator, 3 reference rulesets (shared-base, anchor, native-rust), and an example NFT whitelist mint with a 31-rule security checklist.

**Contributors (2):** Led by 0xFrankCastle (3,557 followers), a Rust/Solana auditor with 70+ Rust audits, 50+ Solana audits, and 250+ critical/high severity findings across protocols including Lido, Pump.fun, LayerZero, and Synthetix. Placed 2nd in the HydraDX Omnipool contest on Code4rena. Second contributor is Pierre / Arrowana (163 followers), a Solana developer.

---

### 11. The-Membrane/membrane-core (contract-audit skill)

- **GitHub:** https://github.com/The-Membrane/membrane-core/tree/new-age-cdp/.claude/skills/contract-audit
- **Stars:** 10 (parent repo) | **Forks:** 2 | **Created:** 2022-05-13 (parent repo)
- **Licence:** GPL-3.0
- **Skills:** 1 (`contract-audit`)
- **Lines:** 3,267

**Description:** A Claude Code skill for systematic security auditing of CosmWasm smart contracts, built from patterns extracted from 61 real Oak Security audit reports (2021--2025). Uses a four-phase pipeline: classify protocol type (lending/CDP, DEX/AMM, perpetuals, etc.), rapid scan across access control/unbounded iterations/state-after-external-calls, read-and-trace flagged code with execution flow analysis, and classify severity. Includes a substantial 2,724-line DeFi audit guide plus four specialised reference files covering access control, liquidation, oracle, and common pitfall patterns. The skill lives inside the Membrane Finance protocol repo rather than a standalone skills repository.

**Contributors (1):** triccs (1 follower), the primary developer of Membrane Finance -- a CosmWasm-based CDP stablecoin protocol deployed on Osmosis, audited by Oak Security. The skill draws directly from their protocol development and audit experience.

---

## Observations

- **Trail of Bits dominates** in breadth and quality -- 58 skills, 73k lines, 3.2k stars, and real bug discoveries
- **Highest author reputation:** Patrick Collins (Cyfrin) at 11k followers, though his skill is the smallest (350 lines)
- **Best depth-per-skill:** forefy/.context packs 15k lines into 3 skills with extensive vulnerability reference libraries
- **Most novel architecture:** pashov's parallelised 4-agent scanning pipeline; Archethect's MCP server with real tool integration
- **Pure knowledge vs tooling split:** Most repos are pure Markdown knowledge bases. Only Archethect/sc-auditor integrates real static analysis tools (Slither, Aderyn) via MCP
- **All repos are very new** -- the oldest is auditmos/skills (Dec 2025), most are from Feb 2026
- **Solidity focus is near-universal** -- forefy/.context covers Anchor (Solana) and Vyper, safe-solana-builder targets Solana/Rust, and membrane-core's skill covers CosmWasm
