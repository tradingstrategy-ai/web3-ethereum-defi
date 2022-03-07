# Compile all of Sushiswap fiels
sushi:
	# Get our mock up contracts to the compiler bundle
	@cp contracts/* sushiswap/contracts
	@(cd sushiswap && yarn install && yarn build) > /dev/null
	@echo "Sushi is ready"

# Extract all compilation artifacts from Sushi to our abi/ dump
copy-sushi-abi: sushi
	@find sushiswap/artifacts/contracts -iname "*.json" -not -iname "*.dbg.json" -exec cp {} eth_hentai/abi \;

# Compile v3 core and periphery
uniswapv3:
	@(cd uniswap_v3/v3-core && yarn install && yarn compile) > /dev/null
	@(cd uniswap_v3/v3-periphery && yarn install && yarn compile) > /dev/null

# Extract ABI and copied over to our abi/uniswap_v3/ folder
copy-uniswapv3-abi: uniswapv3
	@find uniswap_v3/v3-core/artifacts/contracts -iname "*.json" -not -iname "*.dbg.json" -exec cp {} eth_hentai/abi/uniswap_v3 \;
	@find uniswap_v3/v3-periphery/artifacts/contracts -iname "*.json" -not -iname "*.dbg.json" -exec cp {} eth_hentai/abi/uniswap_v3 \;

clean:
	@rm -rf sushiswap/artifacts/*
	@rm -rf uniswap_v3/v3-core/artifacts/*
	@rm -rf uniswap_v3/v3-periphery/artifacts/*

all: clean-docs copy-sushi-abi build-docs

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
	@rm -rf docs/source/_autosummary
	@rm -rf docs/build/html

docs-all: clean-docs build-docs

# Manually generate table of contents for Github README
toc:
	cat README.md | scripts/gh-md-toc -

# Open web browser on docs on macOS
browse-docs-macos:
	@open docs/build/html/index.html
