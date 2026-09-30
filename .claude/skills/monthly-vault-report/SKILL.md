---
name: monthly-vault-report
description: Generate or update the monthly "The best-performing stablecoin vaults" blog post as an unpublished Ghost draft, following the report's writing rules
---

# Monthly vault report

This skill produces the monthly *The best-performing stablecoin vaults* post:
an unpublished Ghost draft that the editor finishes and publishes. Use it
whenever you generate the report, change its post templates, or update its
draft.

## 1. Read the rules first

Read these before changing anything or running the report:

- the *Writing rules* in `eth_defi/vault_report/README-blog-post-outline.md`:
  the section order, headings, introductions, statistics wording, section
  notes, charts and publishing rules the editor has already decided. Follow
  them without being asked, and ask before breaking one;
- `eth_defi/vault_report/README-best-vaults-news.md`: the Ghost draft and PR
  comment workflows;
- `eth_defi/vault_report/README-vault-report.md`: environment variables, the
  investability check and the editor workflow.

When the editor gives a new rule, change the generator, then add the rule to
the *Writing rules* and, if it affects the workflow, to this skill.

## 2. Run the report

The run needs `GHOST_ADMIN_API_KEY` (an Admin API key, not the read-only
Content API key) in the secrets file sourced by `.local-test.env`. Never print
it.

```shell
source .local-test.env && \
    OUTPUT_DIR=/tmp/vault-report \
    VAULT_CHECK_AGENT=claude \
    poetry run python scripts/erc-4626/generate-monthly-vault-report.py
```

- The investability check runs the Claude CLI on Sonnet with medium effort to
  limit the token spend. To iterate on the text or charts, reuse its decisions
  with `VAULT_CHECK_AGENT=reuse VAULT_CHECK_DECISIONS=<previous bundle>`
  while the downloads are younger than six hours and `flag.py` is unchanged.
- The agent may add blacklist or `review_needed` entries to
  `eth_defi/vault/flag.py`. Never commit them without the user's review.
- For template work without Ghost, set `GHOST_DRAFT=false` and open
  `preview.html` in the bundle.

## 3. Update the draft in place

The script refuses to replace an existing draft. Before setting
`GHOST_OVERWRITE_DRAFT=true`, read the draft's `updated_at` through the Admin
API: replace it only if nobody has edited it since the pipeline last wrote
it. Otherwise stop and ask the user. Never publish.

After the run, read the draft back through the Admin API and check the
headings, image and table counts, the empty feature image, and that no
excluded vault appears in the post. The Ghost site is private, so the draft
can be viewed only in the Ghost editor.

## 4. Excluded vaults

The run writes `eth_defi/vault_report/excluded-vaults/{date}-excluded-vaults.md`.
Commit it with the report's pull request and post it as a PR comment:

```shell
gh pr comment <pr> --body-file eth_defi/vault_report/excluded-vaults/<date>-excluded-vaults.md
```

## 5. Report back

Give the user the Ghost editor link the script prints, the excluded and
undecided vault counts, any `flag.py` changes awaiting review, and anything
you could not verify.
