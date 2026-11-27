---
name: remove-slop-tests
description: Remove low-value pytest coverage and consolidate overlapping tests for a named component or subarea. Use only when the user explicitly requests remove-slop-tests.
disable-model-invocation: true
---

# Remove low-value tests

Invoke manually with `/remove-slop-tests <component or subarea>` in Claude or `$remove-slop-tests <component or subarea>` in Codex. If the target is missing, ask which part of the code tree to review.

1. Identify the pytest modules and cases that exercise the requested component, including integration and end-to-end tests outside its directory. Read the implementation and assertions. Collect a baseline with `source .local-test.env && poetry run pytest --collect-only -q <selection>` as described in the [repository pytest instructions](../../../CLAUDE.md#tests). Record the selection, test function count and collected case count.
2. **Always keep negative and adversarial tests for in-house smart contracts**, including [GuardV0](../../../contracts/guard/README.md), the [Safe integration](../../../contracts/safe-integration/README.md) and other contracts maintained by this project. Keep each rejection, permission boundary and attack scenario independently exercised, even when tested through a Python integration. Exclude these tests from the cleanup rules below.
3. For each remaining test, ask **“What plausible production regression would make this test fail, and which assertion would catch it?”** Use the criteria below to identify redundant checks. Trim an assertion when the rest of the test protects useful behaviour. Strengthen the test when it is the only coverage for a meaningful outcome. Remove a whole test when it contributes no distinct behavioural coverage.
4. Prefer integration and end-to-end tests through public entry points. Merge tests when one realistic workflow can cover their distinct outcomes, such as writing records, reopening the database and checking the export. Move unique assertions into the surviving test and retain independent cases where they expose different failures. Delete fixtures made unused by the cleanup.
5. Aim for a substantial reduction of up to 50% of the scoped collected cases, where the evidence supports it. Stop when further cuts would sacrifice distinct coverage, even if the reduction is small. Recollect under the same conditions, run the affected tests and explain any failures or skips.

## What to look for

- **Calculations performed entirely by the test.** Trace the actual value back to a production call. A hand-calculated expectation is useful when compared with an amount the application returned, encoded or persisted. Delete arithmetic exercises where both sides come from the fixture or a mock supplies the very result being asserted.
- **Assertions that can silently do no work.** Check whether an empty result, missing key or early return bypasses every meaningful assertion. For a populated path, use representative input and assert a known record or outcome. Keep legitimate empty-result behaviour as an explicit case.
- **Structure checks already covered by use.** Remove standalone import, inheritance, attribute-presence or callability checks when another test exercises the same public entry point. Check an option through its effect on output. Preserve independently supported contracts such as plugin discovery, registry dispatch, a public import path or a serialised schema.
- **Fixture values repeated as expectations.** A test that constructs a record and immediately reads the same fields back exercises little beyond assignment. The same applies to checks that restate constants or whole dictionaries. Prefer distinct raw inputs that expose swapped fields, incorrect units, chain-specific lookups or ignored overrides.
- **Checks implied by stronger checks.** Remove weaker assertions on the same value, such as approximate equality immediately after exact equality. Drop a repeated call whose sole purpose is to prove it runs without raising. Keep repeated operations when they verify a second-call effect such as idempotency, caching or retry behaviour.
- **Speculative failures in external dependencies.** Remove malformed-response scenarios when the provider contract, observed incidents and application code give no plausible route to that failure. Preserve documented failure modes and regressions the application has encountered.

## Report

State the component, pytest selection and collection environment. List the kept tests with the behaviour they protect, removed tests with the reason for deletion, and merged tests as source names mapped to the surviving test. Give totals for each group and identify the protected contract tests within the kept list. Report before and after counts and percentage reductions separately for test functions and collected cases, followed by the focused run result.
