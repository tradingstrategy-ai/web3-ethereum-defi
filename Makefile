# Compile all of Sushiswap and in-house contract files
sushi:
	# Get our mock up contracts to the compiler bundle
	@(cd contracts/sushiswap && yarn install && yarn build) > /dev/null
	@mkdir -p eth_defi/abi/sushi
	@find contracts/sushiswap/artifacts/contracts -iname "*.json" -not -iname "*.dbg.json" -exec cp {} eth_defi/abi/sushi \;

# Compile our custom integration contracts
#
# forge pollutes the tree with dependencies from Enzyme,
# so need to pick contracts one by one
#
# TODO: Currently depends on Enzyme, because OpenZeppelin went and changed
# their path structure and we need to be compatible with import paths in Enzyme source tree
#
in-house: enzyme
	# Get our mock up contracts to the compiler bundle
	@(cd contracts/in-house && forge build)
	# TODO: Fix this mess,
	# as Forge is bundling all compiled dependencies in the same folder
	# as our contracts
	@find contracts/in-house/out \(  \
	    -name "ChainlinkAggregatorV2V3Interface.json" \
	    -o -name "ERC20MockDecimals.json" \
	    -o -name "MalformedERC20.json" \
	    -o -name "MockChainlinkAggregator.json" \
	    -o -name "ERC20MockDecimals.json" \
	    -o -name "RevertTest.json" \
	    -o -name "RevertTest2.json" \
	    -o -name "VaultSpecificGenericAdapter.json" \
	    -o -name "MockEIP3009Receiver.json" \
	    -o -name "VaultUSDCPaymentForwarder.json" \
	    -o -name "TermedVaultUSDCPaymentForwarder.json" \
	    -o -name "GuardedGenericAdapter.json" \
	    \) \
	    -exec cp {} eth_defi/abi \;

# Guard and simple vault contracts
guard:
	@mkdir -p eth_defi/abi/guard
	@(cd contracts/guard && forge build)
	@find contracts/guard/out \
		\(  \
		-name "GuardV0.json" \
		-o \
		-name "SimpleVaultV0.json" \
		\) \
		-exec cp {} eth_defi/abi/guard \;

# Terms of service acceptance manager contract
terms-of-service:
	@mkdir -p eth_defi/abi/terms-of-service
	@(cd contracts/terms-of-service && forge build)
	@find contracts/terms-of-service/out \
		\(  \
		-name "TermsOfService.json" \
		\) \
		-exec cp {} eth_defi/abi/terms-of-service \;

# Compile v3 core and periphery
uniswapv3:
	@(cd contracts/uniswap-v3-core && yarn install && yarn compile) > /dev/null
	@(cd contracts/uniswap-v3-periphery && yarn install && yarn compile) > /dev/null

# Extract ABI and copied over to our abi/uniswap_v3/ folder
copy-uniswapv3-abi: uniswapv3
	@mkdir -p eth_defi/abi/uniswap_v3
	@find contracts/uniswap-v3-core/artifacts/contracts -iname "*.json" -not -iname "*.dbg.json" -exec cp {} eth_defi/abi/uniswap_v3 \;
	@find contracts/uniswap-v3-periphery/artifacts/contracts -iname "*.json" -not -iname "*.dbg.json" -exec cp {} eth_defi/abi/uniswap_v3 \;

aavev3:
	@(cd contracts/aave-v3-deploy && npm ci && npm run compile) > /dev/null
	@mkdir -p eth_defi/abi/aave_v3
	@find contracts/aave-v3-deploy/artifacts/@aave -iname "*.json" -not -iname "*.dbg.json" -exec cp {} eth_defi/abi/aave_v3 \;

aavev2:
	@(cd contracts/aave-v2 && npm ci && npm run compile) > /dev/null
	@mkdir -p eth_defi/abi/aave_v2
	@find contracts/aave-v2/artifacts/contracts -iname "*.json" -not -iname "*.dbg.json" -exec cp {} eth_defi/abi/aave_v2 \;

# Compile and copy Enzyme contract ABIs from their Github repository
# Needs pnpm: curl -fsSL https://get.pnpm.io/install.sh | sh -
#
# NOTE: Currently needs Enzyme branch that is being ported to Forge.
#
# We also remove AST statements (source mappings) from Enzyme ABI files,
# because they add dozens of megabytes of data. These are not likely
# needed unless you want to see Solidity level stack traces.
#
# See https://github.com/pypi/warehouse/issues/13962
#
enzyme:
	@rm -f eth_defi/abi/enzyme/*.json || false
	@(cd contracts/enzyme && pnpm install)
	@(cd contracts/enzyme && forge build)
	@mkdir -p eth_defi/abi/enzyme
	@find contracts/enzyme/artifacts -iname "*.json" -exec cp {} eth_defi/abi/enzyme \;
	@scripts/clean-enzyme-abi.sh


# Compile and copy dHEDGE
# npm install also compiles the contracts here
dhedge:
	@(cd contracts/dhedge && npm install)
	@mkdir -p eth_defi/abi/dhedge
	@find contracts/dhedge/abi -iname "*.json" -exec cp {} eth_defi/abi/dhedge \;

# Compile Centre (USDC) contracts
centre:
	@(cd contracts/centre && yarn install)
	@(cd contracts/centre && yarn compile)
	@mkdir -p eth_defi/abi/centre
	@find contracts/centre/build -iname "*.json" -exec cp {} eth_defi/abi/centre \;

# Compile and copy 1delta contracts
1delta:
	@cp .1delta.env.example contracts/1delta/.env
	@(cd contracts/1delta && yarn install)
	@mkdir -p eth_defi/abi/1delta
	@find contracts/1delta/artifacts/contracts/1delta -iname "*.json" -not -iname "*.dbg.json" -exec cp {} eth_defi/abi/1delta \;

# TODO: Not sure if this step works anymore
clean:
	@rm -rf contracts/*
	@rm -rf contracts/uniswap-v3-core/artifacts/*
	@rm -rf contracts/uniswap-v3-periphery/artifacts/*

clean-abi:
	@rm -rf eth_defi/abi/*

# Compile all contracts we are using
#
# Move ABI files to within a Python package for PyPi distribution
compile-projects-and-prepare-abi: clean-abi sushi in-house guard copy-uniswapv3-abi aavev3 enzyme dhedge centre 1delta

all: clean-docs compile-projects-and-prepare-abi build-docs

# Export the dependencies, so that Read the docs can build our API docs
# See: https://github.com/readthedocs/readthedocs.org/issues/4912
# terms_of_service is in-place dev dependency, only used for tests and must be removed for RTD
rtd-dep-export:
	@poetry export --without-hashes --with dev --extras docs --extras data -f requirements.txt --output /tmp/requirements.txt
	@grep -v 'terms-of-service' < /tmp/requirements.txt > docs/requirements.txt
	@echo "-e ." >> docs/requirements.txt

# Build docs locally
build-docs:
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
