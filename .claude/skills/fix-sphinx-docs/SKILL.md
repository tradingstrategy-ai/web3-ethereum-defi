---
name: fix-sphix-docs
description: Fix errors and warnings in Sphinx docs build
---

# Fix errors and warnings in Sphinx docs build

This skill iterates over Sphinx docs build and attempt to fix easily addressable errors and warnings.

# Steps

1. Run Sphinx build: `(source ./.venv/bin/activate && cd ./docs/ && make html)`, but with a twist:  figure out how to abort after 60 second do not try to wait for the full build takes too long.
2. Check the output warnings and errors you could attempt to fix. DON'T TOUCH AUTO GENERATED RST FILES. These are in `_autosummary` folders like `_autosummary_d2`. These folders will be recreated by user with a special command you do not know.
3. Report made changes
4. Ask a permission to open a PR

