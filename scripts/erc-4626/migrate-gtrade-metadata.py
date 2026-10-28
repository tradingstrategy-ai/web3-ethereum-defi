"""Retired standalone repair; use the guarded numbered migration runner."""

if __name__ == "__main__":
    raise SystemExit("Use scripts/run-vault-migrations.sh run in production, or poetry run vault-migrate run locally. See .claude/docs/migration-scripts.md.")
