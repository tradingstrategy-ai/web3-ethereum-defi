---
name: make-release
description: Prepare a new release by updating version, changelog, and building the package
---

# Make release

This skill prepares a new release of the `web3-ethereum-defi` package.

## Required inputs

1. **Version number** - The version string for the new release (e.g., `1.1`, `2.0`)

## Steps

### 1. Update version in pyproject.toml

Edit the `version` field in `pyproject.toml` to the new version number.

### 2. Update CHANGELOG.md

1. Find the `# Current` heading in `CHANGELOG.md`
2. Rename it to `# {version}` (the new version number)
3. Append today's date in parentheses to every changelog entry in that section that is missing a date
4. Insert a new `# Current` section at the very top of the file with a single placeholder entry:

```markdown
# Current

- TODO

# {version}

...existing entries...
```

### 3. Create a git commit

Create a commit with the message `Preparing a release`. Push to the master.

### 4. Build the package

Run:

```shell
poetry build
```

Verify the build succeeds and the output `.tar.gz` and `.whl` filenames contain the correct version number.

### 5. Prompt the user

After a successful build, tell the user:

> Release {version} built successfully. Please upload to PyPI manually.
