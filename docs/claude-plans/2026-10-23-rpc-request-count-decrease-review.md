# RPC request count decrease plan review

Date: 2026-10-23. Scope: the [implementation plan](2026-10-23-rpc-request-count-decrease-plan.md) for historical scheduling, metadata batching and the subsequently requested increase in regular batches from 40 to 100 subcalls. No runtime implementation was reviewed or executed.

## Subsequent simplification

The user requested a scope sanity check and a shorter plan after these reviews. The current plan preserves the three optimisations and correctness safeguards, but removes the worker-backend change, universal chain allowlist, exhaustive live-test matrix, arbitrary 25% timing gate and separate live comparison framework. Independent paused/fee reads move into the first metadata pass. Batch-size rollback now explicitly covers token warmup, and zero decimals follow the user-confirmed unavailable-metadata assumption. Earlier hashes and verdicts below describe the earlier revisions; the trimmed revision has not received another external-agent review.

## Review method

Claude CLI 2.1.296 was invoked with the explicitly requested model `claude-opus-5-5`. Each prompt contained the complete numbered plan, its SHA-256, the worktree and base revision, repository constraints, and code facts inspected by the primary agent. Following the repository's agent guidance, these were inline plan reviews with tools disabled, not independent repository audits.

The invocation used `--model claude-opus-5-5 --effort high --tools "" --safe-mode --permission-mode dontAsk --output-format stream-json --verbose --no-session-persistence`, with a 900-second outer timeout. Review completion requires a successful final `result` event; streamed progress alone is insufficient. Raw CLI streams are temporary local artefacts and are not included in the repository.

The initial review completed successfully and its model-usage record confirmed `claude-opus-5-5`. It reviewed plan SHA-256 `289dc53b5e3dd686616845c05832f27e3b3802be88e03cd81dda42536169140e` against repository revision `9d366717a` and returned **Not ready to implement**, identifying three blockers and three high-priority issues.

## Findings and disposition

The findings below summarise the first review. Conditional concerns were checked against the code before revising the plan; they are not all confirmed runtime defects.

| Finding | Verification and plan revision |
|---|---|
| B1: The result-feedback contract could fail if the consumer looks ahead. | Confirmed that `VaultHistoricalReadMulticaller` processes the current combined result in memory before pulling the next one. Specify that contract and test through the real consumer and Parquet row buffering. |
| B2: Missing enrichment fallback conflicted with deferring transport failures. | Distinguish successfully served individual subcall failures from exhausted physical-request failures. The latter abort preparation and retain pending work; they never trigger a per-vault fallback burst. This deliberately differs from the reviewer's optional recommendation to fall back after enrichment transport failures. |
| B3: Pending recovery's source block was unspecified. | Verified that recovery already captures a fresh numeric head. Preserve it and add an old-candidate regression test covering preparation, workers and fallbacks. |
| H1: Frequent feedback barriers could serialise the scan. | Keep the worker pool alive across flushes, measure feedback frequency, and benchmark staggered cohorts before deployment. A representative median slowdown above 25% blocks rollout and requires design revision. No delayed recovery is introduced merely to preserve parallelism. |
| H2: Fixed sample indices conflicted with irregular timestamps. | Separate exact hourly fixtures from jitter fixtures; compare feedback-dependent readers to a serial reference and explicitly bound existing hourly speculation. |
| H3: Scheduler verification lacked a real-provider check. | Add a guarded live scheduler comparison using temporary output/state, cache-aware Hypersync timestamps and the real historical consumer, including interruption/resumption. |
| M1: Morpho v2's combined lending interface could affect other consumers. | Require a consumer audit and preserve the verified zero-assets, missing-token and unclamped utilisation semantics. |
| M2: Morpho v2 batching could exceed provider execution limits. | The review did not establish a production gas defect. Require provider measurement and gate that family on parity at the unchanged global batch limit. |
| M3: Lending threshold reuse was ambiguous. | Verified the existing `Decimal("5000")` constant and strict greater-than eligibility. Pass that constant into preparation explicitly, avoiding a reverse import into the scan module. |
| M4: Adapter identity checks could exclude intended families or bypass overrides. | Verified that Morpho/IPOR flag overrides call the base implementation. Require a per-family implementation table while preserving override logic. |
| M5: Fee assembly might leave uncounted RPC reads. | Verified RPC-free fee-mode and deposit/withdrawal defaults, plus Morpho v1 management fees. Count constructor, preparation and residual direct requests in acceptance tests. |
| M6: Correcting the task ceiling changes public scheduling behaviour. | Require a caller audit, documentation and a dated changelog entry in the subsequent implementation PR. |
| M7: Repeated flushes might double-count worker requests. | Assert exact physical totals across feedback/capacity flushes, merging each result once. |
| Lower-priority compliance and row-density concerns. | Require an ignored secrets loader, typed Python 3.14 interfaces, Sphinx docstrings, shared-pool rules for any new fork tests, and downstream freshness/resume coverage for sparser rows. |

Independent paused/fee fields could be moved into the core pass later to reduce partial-chunk rounding. That optional refinement is not required by this plan. Resource cleanup is limited to resources owned by the touched generator.

## Review of the original two workstreams

The second review is a blocking-only pass over the complete revised plan, SHA-256 `94bbfca7d60447bcfaf32008383a4f3a601ba32c1fce3ec06127e5edf65a9081`, with the verified facts and resolutions above included. It completed with `subtype=success`, `is_error=false`, and model usage confirming `claude-opus-5-5`. Its verdict was:

> No blocking findings.

Claude considered the revised plan ready for implementation. It left three optional notes: benchmark readers whose TVL remains unknown, update the status, and treat a live-check timeout as incomplete. These three clarifications were applied after the final review; the reviewed design is unchanged.

The reviewer explicitly limited its verdict to the supplied plan and code facts. Executor reuse, actual provider costs and the completeness of adapter identity checks still need implementation validation.

## Review of the 100-subcall expansion

The user subsequently added the increase from 40 to 100 regular subcalls. This supersedes the original plan's unchanged-limit boundary, including the first review's M2 disposition. The expansion covers the physical reader, upstream task chunks, metadata/admission helpers, size propagation and worker-cache identity. It preserves stricter chain and greylist limits and adds a provider validation gate, explicit rollback size and fixed-workload measurements including retries.

A further blocking-only Opus 5.5 review was requested using the complete expanded plan, SHA-256 `52c553f8c9e8fb5190c106df73835546cd3714d73b7bb0d2964893407a375717`, and newly inspected batching/retry code facts. It completed successfully using `claude-opus-5-5` and identified two blocking ambiguities:

- The shared 100-call default conflicted with a scanner-only validation policy. The revised plan now enforces chain caps in the shared reader for all callers, explicitly distinguishes operational provider validation from runtime policy, and prevents environment overrides from bypassing caps.
- Base was included in the benchmark gate without an explicit credential or workload. The revised plan now requires `JSON_RPC_BASE`, names the existing Base IPOR test target, and defines distinct-vault cohorts for full metadata, historical and admission payloads.

The optional commit-ordering note was also incorporated: introduce the shared constant at 40, then increase it in the batching commit.

A final blocking-only pass reviews the complete corrected plan, SHA-256 `ca0cd073d5e79646fd2e35471e7cab31be60a13f744cb51d2c55dc26e81729e9`. It completed successfully with model usage confirming `claude-opus-5-5`. The verdict was: “The plan is ready once the two clarifications above are made. Neither requires redesign.”

Those final clarifications have been applied:

- Recovery derives from the effective capped physical limit and every retry remains capped; a Mantle/Gnosis rejection test asserts no payload exceeds 16.
- Any candidate chain that fails or skips the required cohort/parity check is removed from the code-level 100-call set before merge, protecting library callers as well as scanners. Environment overrides are deployment controls, not a substitute for this gate.

The constant-ordering wording was also corrected. These narrow edits were made after the final CLI pass; they have not received a further independent review. The review remains a plan assessment, not runtime validation.

## Remaining implementation risks

The plan still requires evidence that frequent feedback barriers meet the throughput gate, that reduced row density preserves downstream freshness, and that supported provider batches preserve economic values. Established hourly readers retain bounded speculative work within a buffer. Persistent transport faults defer the affected chain's metadata and can reduce freshness even while previous economics remain intact. Request reductions must include preparation and retries, not just fewer encoded calls. A successful plan review does not establish runtime correctness or measured production savings.
