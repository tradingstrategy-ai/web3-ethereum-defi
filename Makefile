# Compile all of Sushiswap and in-house contract files
sushi-and-inhouse:
	# Get our mock up contracts to the compiler bundle
	@cp contracts/inhouse/* contracts/sushiswap/contracts
	@(cd contracts/sushiswap && yarn install && yarn build) > /dev/null
	@find contracts/sushiswap/artifacts/contracts -iname "*.json" -not -iname "*.dbg.json" -exec cp {} eth_defi/abi \;

# Compile v3 core and periphery
uniswapv3:
	@(cd contracts/uniswap-v3-core && yarn install && yarn compile) > /dev/null
	@(cd contracts/uniswap-v3-periphery && yarn install && yarn compile) > /dev/null

# Extract ABI and copied over to our abi/uniswap_v3/ folder
copy-uniswapv3-abi: uniswapv3
	@find contracts/uniswap-v3-core/artifacts/contracts -iname "*.json" -not -iname "*.dbg.json" -exec cp {} eth_defi/abi/uniswap_v3 \;
	@find contracts/uniswap-v3-periphery/artifacts/contracts -iname "*.json" -not -iname "*.dbg.json" -exec cp {} eth_defi/abi/uniswap_v3 \;

# Copy Aave V3 contract ABIs from their NPM package, remove library placeholders (__$ $__)
aavev3:
	@(cd contracts/aave-v3 && npm install)
	@mkdir -p eth_defi/abi/aave_v3
	@find contracts/aave-v3/node_modules/@aave/core-v3/artifacts -iname "*.json" -not -iname "*.dbg.json" -exec cp {} eth_defi/abi/aave_v3 \;
	@find eth_defi/abi/aave_v3 -iname "*.json" -exec sed -e 's/\$$__\|__\$$//g' -i {} \;

# Compile and copy Enzyme contract ABIs from their Github repository
# Needs pnpm: curl -fsSL https://get.pnpm.io/install.sh | sh -
#
# NOTE: Currently needs Enzyme branch that is being ported to Forge.
#
enzyme:
	@rm -f eth_defi/abi/enzyme/*.json || false
	@(cd contracts/enzyme && pnpm install)
	@(cd contracts/enzyme && forge build)
	@mkdir -p eth_defi/abi/enzyme
	@find contracts/enzyme/artifacts -iname "*.json" -exec cp {} eth_defi/abi/enzyme \;

# Compile and copy dHEDGE
# npm install also compiles the contracts here
dhedge:
	@(cd contracts/dhedge && npm install)
	@find contracts/dhedge/abi -iname "*.json" -exec cp {} eth_defi/abi/dhegde \;

clean:
	@rm -rf contracts/sushiswap/artifacts/*
	@rm -rf contracts/uniswap-v3-core/artifacts/*
	@rm -rf contracts/uniswap-v3-periphery/artifacts/*

# Compile all contracts we are using
#
# Move ABI files to within a Python package for PyPi distribution
compile-projects-and-prepare-abi: sushi-and-inhouse copy-uniswapv3-abi aavev3 dhedge

all: clean-docs compile-projects-and-prepare-abi build-docs

# Export the dependencies, so that Read the docs can build our API docs
# See: https://github.com/readthedocs/readthedocs.org/issues/4912
rtd-dep-export:
	poetry export --without-hashes --with dev --with docs --with data -f requirements.txt --output docs/requirements.txt
	echo "-e ." >> docs/requirements.txt

# Build docs locally
build-docs:
	@poetry install -E docs
	@(cd docs && make html)

# Nuke the old docs build to ensure all pages are regenerated
clean-docs:
	@rm -rf docs/source/api/_autosummary*
	@rm -rf docs/build/html

docs-all: clean-docs build-docs

# Manually generate table of contents for Github README
toc:
	cat README.md | scripts/gh-md-toc -

# Open web browser on docs on macOS
browse-docs-macos:
	@open docs/build/html/index.html
