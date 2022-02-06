# Compile all of Sushiswap fiels
sushi:
	# Get our mock up contracts to the compiler bundle
	@cp contracts/* sushiswap/contracts
	@(cd sushiswap && yarn install && yarn build) > /dev/null
	@echo "Sushi is ready"


clean:
	@rm -rf sushiswap/artifacts/*

# Extract all compilation artifacts from Sushi to our abi/ dump
copy-abi: sushi
	@find sushiswap/artifacts/contracts -iname "*.json" -not -iname "*.dbg.json" -exec cp {} smart_contracts_for_testing/abi \;

all: copy-abi

# Export the dependencies, so that Read the docs can build our API docs
# See: https://github.com/readthedocs/readthedocs.org/issues/4912
rtd-dep-export:
	poetry export --without-hashes --dev -f requirements.txt --output requirements-dev.txt

# Build docs locally
build-docs:
	@poetry install -E docs
	@(cd docs && make html)

# Nuke the old docs build to ensure all pages are regenerated
clean-docs:
	@rm -rf docs/build/html

# Manually generate table of contents for Github README
toc:
	cat README.md | scripts/gh-md-toc -

# Open web browser on docs on macOS
browse-docs-macos:
	@open docs/build/html/index.html
