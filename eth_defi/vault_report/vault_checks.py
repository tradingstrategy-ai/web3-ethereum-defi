"""Investability check of the vault report's top lists.

Some vaults rank high in the report but are not investable in practice: they
lend against collateral nobody can sell, their depositors cannot exit, or they
are scams. The data we collect cannot show this. An LLM agent, Claude CLI or
Codex CLI running the ``check-top-list-vaults`` skill, researches the
candidates and decides. See ``.claude/plans/2026-09-26-vault-report-investability-check.md``
and ``.claude/skills/check-top-list-vaults/SKILL.md``.

The check runs in rounds:

1. The report's top lists are collected with a buffer of extra vaults, see
   :py:func:`eth_defi.vault_report.report.collect_top_lists`.
2. New candidates of in-scope protocols get deterministic facts, see
   :py:mod:`eth_defi.vault_report.vault_probes`, and the agent's decisions.
   Other candidates are ``not_in_scope``.
3. Excluded vaults are dropped and the lists are collected again. Vaults that
   moved up into a list are checked in the next round.

Version 1 checks Morpho, Euler and 40acres vaults for suspicious collateral
and missing exit liquidity; :py:data:`CHECK_SCOPE` grows as more protocols
are covered. Likely scams are also blacklisted in ``eth_defi/vault/flag.py``
by the agent, for review.
"""

import ast
import datetime
import hashlib
import json
import logging
import math
import os
import re
import signal
import subprocess
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

import pandas as pd

from eth_defi.compat import native_datetime_utc_now
from eth_defi.vault.flag import BAD_FLAGS
from eth_defi.vault_report.vault_probes import facts_to_json, fetch_candidate_facts

logger = logging.getLogger(__name__)

#: Version of the candidate and decision file formats
SCHEMA_VERSION = 1

#: Version of :py:data:`CHECK_SCOPE`
SCOPE_VERSION = 1

#: Version of the decision rules in the skill
RULES_VERSION = "2026-09-26"

#: Protocol slug -> checks applied. Vaults of other protocols are ``not_in_scope``.
CHECK_SCOPE: dict[str, tuple[str, ...]] = {
    "morpho": ("suspicious_collateral", "no_exit_liquidity"),
    "euler": ("suspicious_collateral", "no_exit_liquidity"),
    "40acres": ("no_exit_liquidity",),
}

#: Allowed decisions
DECISIONS = frozenset({"exclude", "keep", "uncertain", "not_in_scope"})

#: Allowed exclusion categories
CATEGORIES = frozenset({"suspicious_collateral", "no_exit_liquidity", "scam", "broken_data", "other"})

#: Allowed confidence levels
CONFIDENCE_LEVELS = frozenset({"high", "medium", "low"})

#: Path of the vault flag file the agent edits for blacklisting, relative to the repository root
FLAG_FILE = Path("eth_defi/vault/flag.py")

#: Skill the agent follows
SKILL_FILE = Path(".claude/skills/check-top-list-vaults/SKILL.md")

# Important: keep the check agent on a mid-tier model with medium thinking.
# One report run makes up to three agent rounds that each research dozens of
# vaults with web searches, web fetches, onchain reads and sub-agents, so a run
# consumes a lot of LLM tokens. The default Claude CLI model (Opus) with high
# thinking effort costs several times more for no better decisions on this
# task, and we do not want to overspend. Change these only after comparing
# the decisions and the token usage of a full run.

#: Claude CLI model for the check agent, pinned to a full model id so a CLI
#: update moving the ``sonnet`` alias cannot silently change the cost
CLAUDE_CHECK_MODEL = "claude-sonnet-5-5"

#: Claude CLI thinking effort for the check agent: ``low``, ``medium``, ``high``, ``xhigh`` or ``max``
CLAUDE_CHECK_EFFORT = "medium"

#: Supported agent CLIs
AgentName = Literal["claude", "codex"]


class CheckValidationError(ValueError):
    """The agent's decisions file is missing, stale or malformed."""


@dataclass(slots=True)
class CheckCandidate:
    """A vault that would appear in one of the report's top lists."""

    #: ``{chain_id}-{address}``
    vault_id: str

    #: Vault name
    name: str

    #: Chain name
    chain: str

    #: Vault address
    address: str

    #: Protocol name
    protocol: str

    #: Protocol slug
    protocol_slug: str

    #: Curator name, if any
    curator: str | None

    #: ERC-4626 feature names
    features: list[str]

    #: Current TVL in US dollars
    tvl_usd: float

    #: Annualised one-month and three-month returns
    one_month_return: float | None
    three_months_return: float | None

    #: Annualised three-month volatility
    three_months_volatility: float | None

    #: Vault page on the website
    link: str | None

    #: Protocol's own page for the vault
    protocol_link: str | None

    #: Lists the vault would appear in, with its rank, e.g. ``table:lending#3``
    lists: list[str] = field(default_factory=list)

    @property
    def in_scope(self) -> bool:
        """Whether version 1 checks this vault."""
        return self.protocol_slug in CHECK_SCOPE


@dataclass(slots=True)
class CheckDecision:
    """The check's verdict on one candidate."""

    #: ``{chain_id}-{address}``
    vault_id: str

    #: ``exclude``, ``keep``, ``uncertain`` or ``not_in_scope``
    decision: str

    #: Exclusion category, or ``None``
    category: str | None = None

    #: Short phrase naming the problem, e.g. ``RSS elephanToken collateral``
    suspicious_item: str | None = None

    #: One plain-text sentence for readers
    reason: str | None = None

    #: Evidence: ``{"source": url or data reference, "observed_at": ISO timestamp}``
    evidence: list[dict] = field(default_factory=list)

    #: ``high``, ``medium`` or ``low``
    confidence: str | None = None

    #: Whether the vault is blacklisted permanently in ``flag.py``
    blacklist: bool = False

    #: :py:class:`~eth_defi.vault.flag.VaultFlag` value of the blacklist entry
    vault_flag: str | None = None


@dataclass(slots=True)
class CheckRound:
    """Files of one check round, in the report bundle."""

    #: Round number, from 1
    number: int

    #: Candidate file
    candidates_path: Path

    #: Deterministic facts file
    facts_path: Path

    #: Agent decisions file
    decisions_path: Path

    #: SHA-256 of the candidates file
    candidates_digest: str


@dataclass(slots=True)
class VaultCheckSettings:
    """How to run the investability check."""

    #: Agent CLI, or ``None`` to only reuse existing decisions
    agent: AgentName | None = None

    #: Model override for the agent CLI; Claude CLI defaults to :py:data:`CLAUDE_CHECK_MODEL`
    model: str | None = None

    #: Thinking effort override for Claude CLI, defaults to :py:data:`CLAUDE_CHECK_EFFORT`
    effort: str | None = None

    #: Directories searched for reusable decisions, in addition to the report bundle
    reuse_dirs: list[Path] = field(default_factory=list)

    #: Hand-written decisions that override the agent, e.g. from an editor's review
    overrides_path: Path | None = None

    #: Agent timeout per round, in seconds
    timeout: float = 3600.0

    #: Repository root the agent runs in
    repository_root: Path = Path(__file__).parents[2]

    #: Parallel threads for the deterministic probes
    max_workers: int = 8

    @classmethod
    def from_env(cls, default_agent: str = "none") -> "VaultCheckSettings | None":
        """Read the settings from environment variables.

        See the *Investability check* section of ``README-vault-report.md``
        for the variables: ``VAULT_CHECK_AGENT``, ``VAULT_CHECK_MODEL``,
        ``VAULT_CHECK_EFFORT``, ``VAULT_CHECK_DECISIONS``, ``VAULT_CHECK_OVERRIDES``,
        ``VAULT_CHECK_TIMEOUT`` (minutes) and ``MAX_WORKERS``.

        :param default_agent:
            ``VAULT_CHECK_AGENT`` when unset: ``claude``, ``codex``, ``reuse`` or ``none``.

        :return:
            Settings, or ``None`` when the check is disabled with ``none``.
        """
        agent = os.environ.get("VAULT_CHECK_AGENT", default_agent).strip().lower()
        if agent in ("", "none"):
            return None
        assert agent in ("claude", "codex", "reuse"), f"VAULT_CHECK_AGENT must be claude, codex, reuse or none, got {agent}"
        overrides = os.environ.get("VAULT_CHECK_OVERRIDES")
        return cls(
            agent=None if agent == "reuse" else agent,
            model=os.environ.get("VAULT_CHECK_MODEL") or None,
            effort=os.environ.get("VAULT_CHECK_EFFORT") or None,
            reuse_dirs=[Path(path).expanduser() for path in os.environ.get("VAULT_CHECK_DECISIONS", "").split(",") if path],
            overrides_path=Path(overrides).expanduser() if overrides else None,
            timeout=float(os.environ.get("VAULT_CHECK_TIMEOUT", "60")) * 60,
            max_workers=int(os.environ.get("MAX_WORKERS", "8")),
        )


@dataclass(slots=True)
class CheckResult:
    """Decisions of all rounds."""

    #: Vault id -> decision
    decisions: dict[str, CheckDecision] = field(default_factory=dict)

    #: Vault id -> candidate
    candidates: dict[str, CheckCandidate] = field(default_factory=dict)

    #: Rounds that ran
    rounds: list[CheckRound] = field(default_factory=list)

    #: In-scope vaults still unchecked when the round limit was reached
    unchecked: list[str] = field(default_factory=list)

    @property
    def excluded(self) -> frozenset[str]:
        """Ids of excluded vaults."""
        return frozenset(vault_id for vault_id, decision in self.decisions.items() if decision.decision == "exclude")

    @property
    def excluded_candidates(self) -> list[CheckCandidate]:
        """Excluded candidates, in the order of the list and rank they would have appeared at."""
        return sorted((self.candidates[vault_id] for vault_id in self.excluded if vault_id in self.candidates), key=_list_order)

    @property
    def uncertain(self) -> list[CheckCandidate]:
        """Candidates the check could not decide."""
        return [self.candidates[vault_id] for vault_id, decision in self.decisions.items() if decision.decision == "uncertain" and vault_id in self.candidates]


def candidate_depth(target: int, buffer_ratio: float) -> int:
    """Number of candidates collected for a list showing ``target`` vaults.

    :param target:
        Vaults shown.

    :param buffer_ratio:
        Extra share, e.g. 0.5 for 50% more.

    :return:
        Candidates to collect.
    """
    return math.ceil(target * (1 + buffer_ratio))


def build_check_candidates(lists: dict[str, pd.DataFrame]) -> dict[str, CheckCandidate]:
    """Turn the report's top lists into deduplicated candidates.

    :param lists:
        List name -> ranked vaults, best first, from
        :py:func:`eth_defi.vault_report.report.collect_top_lists`.

    :return:
        Vault id -> candidate, each recording the lists and ranks it appears in.
    """
    candidates: dict[str, CheckCandidate] = {}
    for list_name, df in lists.items():
        for rank, (vault_id, vault) in enumerate(df.iterrows(), start=1):
            if vault_id not in candidates:
                candidates[vault_id] = CheckCandidate(
                    vault_id=vault_id,
                    name=vault.get("name") or vault.get("address"),
                    chain=vault.get("chain"),
                    address=vault.get("address"),
                    protocol=vault.get("protocol"),
                    protocol_slug=vault.get("protocol_slug"),
                    curator=vault.get("curator_name") if isinstance(vault.get("curator_name"), str) else None,
                    features=list(vault.get("features") or []) if isinstance(vault.get("features"), list) else [],
                    tvl_usd=float(vault.get("current_nav") or 0.0),
                    one_month_return=_optional_float(vault.get("one_month_cagr_best")),
                    three_months_return=_optional_float(vault.get("three_months_cagr_best")),
                    three_months_volatility=_optional_float(vault.get("three_months_volatility")),
                    link=vault.get("trading_strategy_link"),
                    protocol_link=vault.get("link"),
                )
            candidates[vault_id].lists.append(f"{list_name}#{rank}")
    return candidates


def _optional_float(value) -> float | None:
    """Convert a possibly missing number to ``float``."""
    return None if value is None or pd.isna(value) else float(value)


def write_candidates_file(path: Path, candidates: list[CheckCandidate], data_end_at: datetime.datetime) -> str:
    """Write the candidates the agent must check.

    :param path:
        Output JSON file.

    :param candidates:
        In-scope candidates.

    :param data_end_at:
        Report data date.

    :return:
        SHA-256 digest of the written file.
    """
    document = {
        "schema_version": SCHEMA_VERSION,
        "scope_version": SCOPE_VERSION,
        "rules_version": RULES_VERSION,
        "data_end_at": data_end_at.isoformat(),
        "scope": {slug: list(checks) for slug, checks in CHECK_SCOPE.items()},
        "candidates": [asdict(candidate) for candidate in candidates],
    }
    path.write_text(json.dumps(document, indent=2, sort_keys=True))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_decision(record: dict) -> CheckDecision:
    """Parse one decision record.

    :param record:
        Decision JSON object.

    :return:
        Decision.

    :raise CheckValidationError:
        On an unknown decision, category or confidence, or an incomplete exclusion or blacklist.
    """
    decision = CheckDecision(
        vault_id=record.get("vault_id", ""),
        decision=record.get("decision", ""),
        category=record.get("category"),
        suspicious_item=record.get("suspicious_item"),
        reason=record.get("reason"),
        evidence=list(record.get("evidence") or []),
        confidence=record.get("confidence"),
        blacklist=bool(record.get("blacklist", False)),
        vault_flag=record.get("vault_flag"),
    )
    if decision.decision not in DECISIONS:
        raise CheckValidationError(f"{decision.vault_id}: unknown decision {decision.decision!r}")
    if decision.confidence is not None and decision.confidence not in CONFIDENCE_LEVELS:
        raise CheckValidationError(f"{decision.vault_id}: unknown confidence {decision.confidence!r}")
    if decision.decision == "exclude":
        if decision.category not in CATEGORIES or not decision.suspicious_item or not decision.reason:
            raise CheckValidationError(f"{decision.vault_id}: an exclusion needs a category, a suspicious item and a reason")
        if not decision.evidence or not all(item.get("source") and item.get("observed_at") for item in decision.evidence):
            raise CheckValidationError(f"{decision.vault_id}: an exclusion needs evidence with a source and an observation time")
    if decision.blacklist:
        if decision.decision != "exclude" or decision.confidence != "high":
            raise CheckValidationError(f"{decision.vault_id}: only high-confidence exclusions can be blacklisted")
        bad_flags = {flag.value for flag in BAD_FLAGS}
        if decision.vault_flag not in bad_flags:
            raise CheckValidationError(f"{decision.vault_id}: blacklist flag {decision.vault_flag!r} is not one of the bad flags {sorted(bad_flags)}")
    return decision


def read_check_decisions(path: Path, candidates: list[CheckCandidate], candidates_digest: str, data_end_at: datetime.datetime, max_evidence_age: datetime.timedelta = datetime.timedelta(days=7)) -> dict[str, CheckDecision]:
    """Read and validate the agent's decisions for one round.

    :param path:
        Decisions JSON file.

    :param candidates:
        Candidates of the round.

    :param candidates_digest:
        SHA-256 of the round's candidates file.

    :param data_end_at:
        Report data date.

    :param max_evidence_age:
        Liquidity evidence must be observed at most this long before the data date.

    :return:
        Vault id -> decision.

    :raise CheckValidationError:
        When the file does not match the round or a decision is invalid. Never falls back to keeping everything.
    """
    if not path.exists():
        raise CheckValidationError(f"Decisions file {path} was not written")
    try:
        document = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise CheckValidationError(f"Decisions file {path} is not valid JSON: {e}") from e
    header = {"schema_version": SCHEMA_VERSION, "scope_version": SCOPE_VERSION, "rules_version": RULES_VERSION, "data_end_at": data_end_at.isoformat(), "candidates_digest": candidates_digest}
    for key, expected in header.items():
        if document.get(key) != expected:
            raise CheckValidationError(f"Decisions file {path} has {key}={document.get(key)!r}, expected {expected!r}")

    decisions: dict[str, CheckDecision] = {}
    for record in document.get("decisions") or []:
        decision = parse_decision(record)
        if decision.vault_id in decisions:
            raise CheckValidationError(f"Duplicate decision for {decision.vault_id}")
        decisions[decision.vault_id] = decision

    expected_ids = {candidate.vault_id for candidate in candidates}
    unknown = set(decisions) - expected_ids
    missing = expected_ids - set(decisions)
    if unknown:
        raise CheckValidationError(f"Decisions for vaults that are not candidates: {sorted(unknown)}")
    if missing:
        raise CheckValidationError(f"No decision for in-scope candidates: {sorted(missing)}")
    for decision in decisions.values():
        if decision.decision == "not_in_scope":
            raise CheckValidationError(f"{decision.vault_id} is in scope but was not checked")
        if decision.category == "no_exit_liquidity":
            for item in decision.evidence:
                observed = datetime.datetime.fromisoformat(item["observed_at"].replace("Z", "")).replace(tzinfo=None)
                if observed < data_end_at - max_evidence_age:
                    raise CheckValidationError(f"{decision.vault_id}: liquidity evidence from {observed} is older than {max_evidence_age}")
    return decisions


def read_overrides(path: Path) -> dict[str, CheckDecision]:
    """Read hand-written decisions that override the agent.

    Overrides use the decision record format, without a file header, so an
    editor can keep them across report runs.

    :param path:
        JSON file with a list of decision records.

    :return:
        Vault id -> decision.
    """
    return {decision.vault_id: decision for decision in (parse_decision(record) for record in json.loads(path.read_text()))}


def check_blacklist_entries(decisions: dict[str, CheckDecision], flag_file: Path) -> list[str]:
    """Check that every blacklisted vault has an entry in ``flag.py``.

    ``flag.py`` is parsed from source, because the agent edits it while this
    process already has the module loaded.

    :param decisions:
        Decisions of the round.

    :param flag_file:
        Path to ``eth_defi/vault/flag.py``.

    :return:
        Vault ids missing from ``VAULT_FLAGS_AND_NOTES``.
    """
    blacklisted = {vault_id: decision.vault_flag for vault_id, decision in decisions.items() if decision.blacklist}
    if not blacklisted:
        return []
    entries = read_flag_entries(flag_file)
    return [vault_id for vault_id, flag in blacklisted.items() if entries.get(vault_id.split("-", 1)[1].lower()) != flag]


def find_unreviewed_uncertain(decisions: dict[str, CheckDecision], flag_file: Path) -> list[str]:
    """Find ``uncertain`` vaults without an entry in ``flag.py``.

    The skill asks the agent to record every ``uncertain`` vault with
    ``VaultFlag.review_needed``, so the doubt survives the run. A vault with
    any entry, e.g. an older bad flag, counts as recorded.

    :param decisions:
        Decisions of the round.

    :param flag_file:
        Path to ``eth_defi/vault/flag.py``.

    :return:
        Vault ids of ``uncertain`` decisions missing from ``VAULT_FLAGS_AND_NOTES``.
    """
    uncertain = [vault_id for vault_id, decision in decisions.items() if decision.decision == "uncertain"]
    if not uncertain:
        return []
    entries = read_flag_entries(flag_file)
    return [vault_id for vault_id in uncertain if vault_id.split("-", 1)[1].lower() not in entries]


def read_flag_entries(flag_file: Path) -> dict[str, str | None]:
    """Read the vault flags of ``VAULT_FLAGS_AND_NOTES`` from the ``flag.py`` source.

    :param flag_file:
        Path to ``eth_defi/vault/flag.py``.

    :return:
        Lowercased vault address -> ``VaultFlag`` member name, or ``None`` for a note without a flag.
    """
    for node in ast.walk(ast.parse(flag_file.read_text())):
        target = node.target if isinstance(node, ast.AnnAssign) else node.targets[0] if isinstance(node, ast.Assign) else None
        if isinstance(target, ast.Name) and target.id == "VAULT_FLAGS_AND_NOTES" and isinstance(node.value, ast.Dict):
            entries = {}
            for key, value in zip(node.value.keys, node.value.values, strict=True):
                flag = value.elts[0] if isinstance(value, ast.Tuple) and value.elts else None
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    entries[key.value.lower()] = flag.attr if isinstance(flag, ast.Attribute) else None
            return entries
    raise CheckValidationError(f"VAULT_FLAGS_AND_NOTES not found in {flag_file}")


def build_agent_prompt(round_: CheckRound) -> str:
    """Create the prompt that points the agent to the skill and the round's files.

    :param round_:
        Check round.

    :return:
        Prompt text.
    """
    return f"Read {SKILL_FILE} and follow it exactly. Candidates: {round_.candidates_path}. Facts: {round_.facts_path}. Write the decisions to {round_.decisions_path}. Copy candidates_digest={round_.candidates_digest} into the decisions file header. Work unattended: do not ask questions, do not commit or push. Finish all research, including any background tasks or sub-agents you start, before you end your turn, and write the decisions file last."


def build_agent_command(agent: AgentName, prompt: str, model: str | None = None, effort: str | None = None) -> list[str]:
    """Build the unsandboxed, web-enabled CLI command for the check agent.

    - Claude CLI: print mode with web search, web fetch and shell tools,
      streaming JSON, on :py:data:`CLAUDE_CHECK_MODEL` with
      :py:data:`CLAUDE_CHECK_EFFORT` thinking unless overridden, to limit
      the token spend. See the `Claude Code CLI reference <https://docs.anthropic.com/en/docs/claude-code/cli-reference>`__.
    - Codex CLI: ``--search`` (a top-level flag) for live web search, no sandbox,
      streaming JSONL. See the `Codex CLI documentation <https://developers.openai.com/codex/cli>`__.

    :param agent:
        ``claude`` or ``codex``.

    :param prompt:
        Prompt text.

    :param model:
        Optional model override.

    :param effort:
        Optional Claude CLI thinking effort override; ignored by Codex.

    :return:
        Command and arguments.
    """
    if agent == "claude":
        return ["claude", "-p", prompt, "--model", model or CLAUDE_CHECK_MODEL, "--effort", effort or CLAUDE_CHECK_EFFORT, "--permission-mode", "dontAsk", "--allowedTools", "Bash,Read,Write,Edit,Grep,Glob,WebSearch,WebFetch", "--output-format", "stream-json", "--verbose", "--no-session-persistence"]
    if agent == "codex":
        return ["codex", "--search", "exec", "--json", "--ephemeral", "--sandbox", "danger-full-access", *(["-m", model] if model else []), prompt]
    raise ValueError(f"Unknown agent {agent!r}")


def run_check_agent(command: list[str], cwd: Path, log_path: Path, decisions_path: Path, timeout: float, poll_interval: float = 60.0) -> None:
    """Run the agent CLI, streaming its JSONL output to a log file.

    The decisions file is deleted first, so a stale file from an earlier run
    can never be accepted. Progress is logged every minute.

    :param command:
        Output of :py:func:`build_agent_command`.

    :param cwd:
        Repository root.

    :param log_path:
        JSONL log of the agent run.

    :param decisions_path:
        Decisions file the agent must write.

    :param timeout:
        Seconds before the agent is stopped.

    :param poll_interval:
        Seconds between progress log lines and timeout checks.

    :raise CheckValidationError:
        When the agent fails, times out or does not write the decisions file.
    """
    decisions_path.unlink(missing_ok=True)
    started = time.monotonic()
    with log_path.open("w") as log, log_path.with_suffix(".err").open("w") as errors:
        # A process group of its own, so a timeout also stops the tools the agent started
        # In print mode the Claude CLI stops waiting for the agent's background sub-agents after 600 s and exits
        # before the decisions are written; wait for them, bounded by our own timeout
        environment = os.environ | {"CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS": "0"}
        process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL, stdout=log, stderr=errors, start_new_session=True, env=environment)
        while True:
            try:
                process.wait(timeout=poll_interval)
                break
            except subprocess.TimeoutExpired:
                elapsed = time.monotonic() - started
                lines = sum(1 for _ in log_path.open()) if log_path.exists() else 0
                logger.info("Check agent running for %.0f minutes, %d events so far", elapsed / 60, lines)
                if elapsed > timeout:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    raise CheckValidationError(f"Check agent timed out after {timeout:.0f} s, see {log_path}") from None
    if process.returncode != 0:
        raise CheckValidationError(f"Check agent exited with code {process.returncode}, see {log_path}")
    if not decisions_path.exists():
        raise CheckValidationError(f"Check agent did not write {decisions_path}, see {log_path}")


def show_flag_diff(repository_root: Path) -> str:
    """Show the agent's uncommitted ``flag.py`` changes for review.

    :param repository_root:
        Repository root.

    :return:
        ``git diff`` output; empty when unchanged.
    """
    result = subprocess.run(["git", "diff", "--", str(FLAG_FILE)], cwd=repository_root, capture_output=True, text=True, check=False)
    return result.stdout


def _find_reusable(round_: CheckRound, candidates: list[CheckCandidate], data_end_at: datetime.datetime, reuse_dirs: list[Path]) -> dict[str, CheckDecision] | None:
    """Reuse decisions for the same candidates, from the bundle or a reuse directory."""
    for path in [round_.decisions_path, *(directory / round_.decisions_path.name for directory in reuse_dirs)]:
        if path.exists():
            try:
                decisions = read_check_decisions(path, candidates, round_.candidates_digest, data_end_at)
            except CheckValidationError as e:
                logger.info("Not reusing %s: %s", path, e)
                continue
            logger.info("Reusing check decisions from %s", path)
            return decisions
    return None


def _check_round(number: int, in_scope: list[CheckCandidate], data_end_at: datetime.datetime, prices_path: Path | None, output_dir: Path, settings: VaultCheckSettings, facts: dict | None = None) -> tuple[CheckRound, dict[str, CheckDecision]]:
    """Decide one batch of in-scope candidates: reuse earlier decisions or run the agent.

    :return:
        The round's files and its decisions.
    """
    candidates_path = output_dir / f"vault-check-candidates-{number}.json"
    round_ = CheckRound(number, candidates_path, output_dir / f"vault-check-facts-{number}.json", output_dir / f"vault-check-decisions-{number}.json", write_candidates_file(candidates_path, in_scope, data_end_at))
    decisions = _find_reusable(round_, in_scope, data_end_at, settings.reuse_dirs)
    if decisions is not None:
        return round_, decisions
    if settings.agent is None:
        raise CheckValidationError(f"No reusable decisions for round {number} and no check agent configured; set VAULT_CHECK_AGENT")
    if facts is None:
        facts = fetch_candidate_facts([asdict(candidate) for candidate in in_scope], prices_path, data_end_at, settings.max_workers)
    round_.facts_path.write_text(json.dumps(facts_to_json({c.vault_id: facts[c.vault_id] for c in in_scope if c.vault_id in facts}), indent=2, default=str))
    logger.info("Round %d: checking %d in-scope vaults with %s", number, len(in_scope), settings.agent)
    command = build_agent_command(settings.agent, build_agent_prompt(round_), settings.model, settings.effort)
    diff_before = show_flag_diff(settings.repository_root)
    run_check_agent(command, settings.repository_root, output_dir / f"vault-check-agent-{number}.jsonl", round_.decisions_path, settings.timeout)
    decisions = read_check_decisions(round_.decisions_path, in_scope, round_.candidates_digest, data_end_at)
    missing = check_blacklist_entries(decisions, settings.repository_root / FLAG_FILE)
    if missing:
        raise CheckValidationError(f"Blacklisted vaults missing from {FLAG_FILE}: {missing}")
    # A missing review entry loses the doubt after this run, but the decisions are still valid
    unreviewed = find_unreviewed_uncertain(decisions, settings.repository_root / FLAG_FILE)
    if unreviewed:
        logger.warning("Uncertain vaults without a review_needed entry in %s: %s", FLAG_FILE, unreviewed)
    # Warn only about changes made in this round, not uncommitted entries of earlier rounds
    diff = show_flag_diff(settings.repository_root)
    if diff and diff != diff_before:
        logger.warning("The check agent changed %s, adding blacklist or review_needed entries; review and commit the change:\n%s", FLAG_FILE, diff)
    return round_, decisions


def run_vault_checks(
    collect_lists,
    comparable_df: pd.DataFrame,
    data_end_at: datetime.datetime,
    prices_path: Path | None,
    output_dir: Path,
    settings: VaultCheckSettings,
    max_rounds: int = 3,
    aggregate_df: pd.DataFrame | None = None,
    prescreen_min_tvl: float = 1_000_000,
    max_escalations: int = 20,
) -> CheckResult:
    """Check the report's top lists in rounds until every listed vault is decided.

    After the top lists, a deterministic prescreen probes the in-scope vaults
    of the aggregate charts (average yields, risk and return) with at least
    ``prescreen_min_tvl`` TVL. The prescreen never excludes a vault itself:
    vaults with suspicion signals, the largest first, go to the agent in a
    final round, at most ``max_escalations`` of them.

    :param collect_lists:
        Function ``(comparable_df) -> dict[list name, DataFrame]`` returning the
        top lists with their buffers, see :py:func:`eth_defi.vault_report.report.collect_top_lists`.

    :param comparable_df:
        Eligible vaults with an identified protocol.

    :param data_end_at:
        Report data date.

    :param prices_path:
        Vault price Parquet, for the liquidity history.

    :param output_dir:
        Report bundle; the round files are written here.

    :param settings:
        Agent and reuse settings.

    :param max_rounds:
        Maximum number of top list rounds.

    :param aggregate_df:
        Vaults of the aggregate charts, or ``None`` to skip the prescreen.

    :param prescreen_min_tvl:
        Minimum TVL of a vault to prescreen, in US dollars.

    :param max_escalations:
        Maximum number of prescreened vaults sent to the agent.

    :return:
        Decisions of all rounds, with overrides applied.
    """
    result = CheckResult()
    overrides = read_overrides(settings.overrides_path) if settings.overrides_path else {}

    def remaining() -> pd.DataFrame:
        return comparable_df.drop(index=[vault_id for vault_id in result.excluded if vault_id in comparable_df.index])

    def triage(candidates: dict[str, CheckCandidate]) -> list[CheckCandidate]:
        # Overrides and out-of-scope vaults are decided without the agent
        for vault_id, candidate in candidates.items():
            result.candidates.setdefault(vault_id, candidate)
            if vault_id in result.decisions:
                continue
            if vault_id in overrides:
                result.decisions[vault_id] = overrides[vault_id]
            elif not candidate.in_scope:
                result.decisions[vault_id] = CheckDecision(vault_id=vault_id, decision="not_in_scope")
        return [candidate for vault_id, candidate in candidates.items() if vault_id not in result.decisions]

    def run_round(in_scope: list[CheckCandidate], facts: dict | None = None) -> None:
        round_, decisions = _check_round(len(result.rounds) + 1, in_scope, data_end_at, prices_path, output_dir, settings, facts)
        result.rounds.append(round_)
        result.decisions.update(decisions)

    for _ in range(max_rounds):
        in_scope = triage(build_check_candidates(collect_lists(remaining())))
        if not in_scope:
            break
        run_round(in_scope)
    else:
        result.unchecked = sorted(c.vault_id for c in triage(build_check_candidates(collect_lists(remaining()))))
        if result.unchecked:
            logger.warning("Round limit reached with %d unchecked in-scope vaults in the top lists: %s", len(result.unchecked), result.unchecked)

    if aggregate_df is not None:
        pool = aggregate_df.loc[aggregate_df["protocol_slug"].isin(CHECK_SCOPE.keys()) & (aggregate_df["current_nav"] >= prescreen_min_tvl) & ~aggregate_df.index.isin(list(result.decisions))]
        if len(pool):
            logger.info("Prescreening %d in-scope vaults of the aggregate charts", len(pool))
            screened = build_check_candidates({"aggregate": pool.sort_values("current_nav", ascending=False)})
            facts = fetch_candidate_facts([asdict(candidate) for candidate in screened.values()], prices_path, data_end_at, settings.max_workers)
            flagged = {vault_id: screened[vault_id] for vault_id in screened if facts.get(vault_id) and facts[vault_id].signals}
            escalated = dict(list(flagged.items())[:max_escalations])
            logger.info("Prescreen: %d of %d vaults raised signals, %d sent to the agent", len(flagged), len(screened), len(escalated))
            in_scope = triage(escalated)
            if in_scope:
                run_round(in_scope, facts)
    return result


def apply_check_decisions(vaults_df: pd.DataFrame, excluded: frozenset[str]) -> pd.DataFrame:
    """Drop excluded vaults from a vault set.

    :param vaults_df:
        Vaults indexed by vault id.

    :param excluded:
        Excluded vault ids, see :py:attr:`CheckResult.excluded`.

    :return:
        Vaults without the excluded ones.
    """
    return vaults_df.loc[~vaults_df.index.isin(excluded)]


def _list_order(candidate: CheckCandidate) -> tuple[str, int]:
    """Sort key: the first list and rank the vault would have appeared in."""
    first = candidate.lists[0] if candidate.lists else "~#0"
    name, _, rank = first.partition("#")
    return name, int(rank or 0)


def _markdown_cell(text: str | None) -> str:
    """Make agent-written text safe for one Markdown table cell."""
    return re.sub(r"\s+", " ", text or "").replace("|", "\\|").strip()


def _markdown_vault(candidate: CheckCandidate) -> str:
    """Vault name linked to its tradingstrategy.ai page, for a Markdown table."""
    name = _markdown_cell(candidate.name or candidate.address).replace("[", "\\[").replace("]", "\\]")
    return f"[{name}]({candidate.link})" if candidate.link and re.match(r"^https://\S+$", candidate.link) else name


def render_excluded_vaults_markdown(result: CheckResult, data_end_at: datetime.datetime, report_title: str) -> str:
    """Render the vaults the investability check left out of a report, as a dated Markdown document.

    The blog post does not list excluded vaults. This document is the record
    of what was left out and why, for the editor, the pull request review and
    the repository history. Agent-written text is flattened into plain table
    cells; the evidence URLs stay in the decisions files and ``report.json``.

    :param result:
        Check result.

    :param data_end_at:
        Report data date.

    :param report_title:
        Title of the report post.

    :return:
        Markdown document.
    """
    lines = [
        f"# Excluded vaults, {data_end_at:%Y-%m-%d}",
        "",
        f"Vaults the investability check left out of *{report_title}*, with data dated {data_end_at:%Y-%m-%d}. Written {native_datetime_utc_now():%Y-%m-%d %H:%M} UTC by `eth_defi.vault_report.vault_checks`, see `eth_defi/vault_report/README-vault-report.md`.",
        "",
        "They would have ranked or been charted, but are not investable in practice: their collateral cannot be valued or sold, their depositors cannot exit, or they show signs of a scam. The check is AI-assisted and not deterministic; review each decision.",
        "",
        "## Excluded vaults",
        "",
    ]
    excluded = result.excluded_candidates
    if excluded:
        lines += ["| Vault | Chain | Protocol | Suspicious item | Reason | Confidence | Blacklisted in `flag.py` |", "|---|---|---|---|---|---|---|"]
        for candidate in excluded:
            decision = result.decisions[candidate.vault_id]
            lines.append(f"| {_markdown_vault(candidate)} | {_markdown_cell(candidate.chain)} | {_markdown_cell(candidate.protocol)} | {_markdown_cell(decision.suspicious_item)} | {_markdown_cell(decision.reason)} | {_markdown_cell(decision.confidence)} | {'yes, ' + _markdown_cell(decision.vault_flag) if decision.blacklist else 'no'} |")
    else:
        lines.append("None.")
    lines += ["", "## Vaults under review", "", "The check could not decide on these vaults, so they stay in the report. They are recorded in `flag.py` with `VaultFlag.review_needed`.", ""]
    if result.uncertain:
        lines += ["| Vault | Chain | Protocol | Reason |", "|---|---|---|---|"]
        lines += [f"| {_markdown_vault(c)} | {_markdown_cell(c.chain)} | {_markdown_cell(c.protocol)} | {_markdown_cell(result.decisions[c.vault_id].reason)} |" for c in result.uncertain]
    else:
        lines.append("None.")
    counts = Counter(decision.decision for decision in result.decisions.values())
    lines += [
        "",
        "## Check run",
        "",
        f"- Rounds: {len(result.rounds)}; decisions: {', '.join(f'{count} {name}' for name, count in sorted(counts.items()))}",
        f"- In-scope vaults not checked before the round limit: {len(result.unchecked)}",
        f"- Decision files: {', '.join(f'`{r.decisions_path.name}`' for r in result.rounds)}",
        "",
    ]
    return "\n".join(lines)


def excluded_rows(result: CheckResult) -> list[dict]:
    """Excluded vaults as plain records for the CSV and the manifest.

    :param result:
        Check result.

    :return:
        One record per excluded vault, with the decision and its evidence.
    """
    rows = result.excluded_candidates
    return [{"vault_id": c.vault_id, "name": c.name, "chain": c.chain, "protocol": c.protocol, "lists": ";".join(c.lists), **{k: v for k, v in asdict(result.decisions[c.vault_id]).items() if k != "vault_id"}} for c in rows]


def summarise_checks(result: CheckResult) -> dict:
    """Summary of the check for ``report.json``.

    :param result:
        Check result.

    :return:
        Counts, round files and the excluded vaults with evidence.
    """
    return {
        "checked_at": native_datetime_utc_now().isoformat(),
        "scope_version": SCOPE_VERSION,
        "rules_version": RULES_VERSION,
        "counts": dict(Counter(decision.decision for decision in result.decisions.values())),
        "rounds": [{"number": r.number, "candidates": r.candidates_path.name, "decisions": r.decisions_path.name, "candidates_digest": r.candidates_digest} for r in result.rounds],
        "unchecked": result.unchecked,
        "excluded": excluded_rows(result),
        "uncertain": [c.vault_id for c in result.uncertain],
    }
