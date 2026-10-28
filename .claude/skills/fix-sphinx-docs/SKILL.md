---
name: fix-sphinx-docs
description: Fix Sphinx source errors and warnings using supplied build output or CI logs, without building the documentation locally.
---

# Fix Sphinx source errors and warnings

Use existing error output and source inspection to fix documentation issues.
`AGENTS.md` excludes local Sphinx builds from development verification.

## Steps

1. Read the supplied build output or the relevant CI job log. Locate the
   affected source page, referenced API module and index entries. If no log is
   available, inspect the reported source problem directly.
2. Correct the maintained RST, docstrings, links or index references. Do not
   edit generated `_autosummary*` files; trace their errors to the maintained
   source or autosummary declaration.
3. Check the edited sources and their references. Run focused Python checks
   when API code changed. Do not run Sphinx, `make html` or documentation build
   wrappers locally.
4. Finish with [clean-up-for-pr](../clean-up-for-pr/SKILL.md) at the outermost
   task's review handoff. Report the fixes and any build result that still needs
   CI confirmation. Open or update a PR only when the user requested it.

