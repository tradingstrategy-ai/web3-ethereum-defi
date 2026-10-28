## Why

<!-- Concrete problem and why this change is needed. -->

## Lessons learnt

<!-- Reusable operational or implementation knowledge. -->

## Summary

<!-- What changed and why? 1-3 bullet points. -->

-

## Related issues and PRs

<!-- Link any related GitHub issues: Closes #123 -->

## Unrelated CI and test fixes

<!-- Describe unrelated fixes, or None. -->

## Checklist

- [ ] Database migration needed / N/A recorded; numbered scripts (legacy standalone repair format must not be used) and a **Run after merge** production reminder follow the [migration guide](https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/.claude/docs/migration-scripts.md)

<!-- If this PR adds or changes manually maintained narrative text, check it
against the writing style guide unless the same text was checked earlier.
Reuse the earlier review for unchanged text; check subsequent substantive edits.
For the style item below, record completion, the earlier review, or N/A when
there are no narrative changes. -->

- [ ] Narrative text checked against the [writing style guide](https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/.claude/docs/description-writing-style.md), or earlier review / N/A recorded
- [ ] Type hints on all new function arguments and return values
- [ ] Tests added or updated for new/changed behaviour
- [ ] `poetry run ruff format` run on changed files
- [ ] CHANGELOG.md updated (for new features — use format: `Description (YYYY-MM-DD, [#PR](url))`)
- [ ] Sphinx docstrings added for new public functions and classes
