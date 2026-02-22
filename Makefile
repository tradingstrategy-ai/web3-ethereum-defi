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
		-o \
		-name "HypercoreVaultLib.json" \
		-o \
		-name "CowSwapLib.json" \
		-o \
		-name "GmxLib.json" \
		-o \
		-name "MockCoreWriter.json" \
		-o \
		-name "MockCoreDepositWallet.json" \
		\) \
		-exec cp {} eth_defi/abi/guard \;

# Guard as  a safe module
safe-integration:
	@mkdir -p eth_defi/abi/safe-integration
	@(cd contracts/safe-integration && forge clean && forge build)
	@find contracts/safe-integration/out \
		\(  \
		-name "TradingStrategyModuleV0.json" \
		-o \
		-name "MockSafe.json" \
		\) \
		-exec cp {} eth_defi/abi/safe-integration \;

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

aavev3_old:
	@(cd contracts/aave-v3-deploy && npm ci && npm run compile) > /dev/null
	@mkdir -p eth_defi/abi/aave_v3_old
	@find contracts/aave-v3-deploy/artifacts/@aave -iname "*.json" -not -iname "*.dbg.json" -exec cp {} eth_defi/abi/aave_v3_old \;

aavev3:
	@(cd contracts/aave-v3-origin && forge clean && forge install && forge build) > /dev/null
	@mkdir -p eth_defi/abi/aave_v3
	@find contracts/aave-v3-origin/out -iname "*.json" -exec cp {} eth_defi/abi/aave_v3 \;

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

# Compile and copy Lagoon Finance contracts.
lagoon:
	@(cd contracts/lagoon-v0 && soldeer install)
	@(cd contracts/lagoon-v0 && make build)
	@mkdir -p eth_defi/abi/lagoon
	@mkdir -p eth_defi/abi/lagoon/v0.4.0
	@mkdir -p eth_defi/abi/lagoon/v0.5.0
	@mkdir -p eth_defi/abi/lagoon/protocol-v2
	@find contracts/lagoon-v0/out/v0.5.0 -iname "*.json" -not -iname "*.dbg.json" -exec cp {} eth_defi/abi/lagoon/v0.5.0 \;
	@find contracts/lagoon-v0/out/v0.4.0 -iname "*.json" -not -iname "*.dbg.json" -exec cp {} eth_defi/abi/lagoon/v0.4.0 \;
	@cp contracts/lagoon-v0/out/BeaconProxyFactory.sol/BeaconProxyFactory.json eth_defi/abi/lagoon
	@cp contracts/lagoon-v0/out/ProtocolRegistry.sol/ProtocolRegistry.json eth_defi/abi/lagoon
#	@find contracts/lagoon-v0/out/protocol-v2 -iname "*.json" -not -iname "*.dbg.json" -exec cp {} eth_defi/abi/lagoon/protocol-v2 \;

# Compile and copy Velvet capital contracts
velvet:
	@(cd contracts/velvet-core && npm i --legacy-peer-deps && npx hardhat compile)
	@mkdir -p eth_defi/abi/velvet
	@find contracts/velvet-core/artifacts/contracts -iname "*.json" -not -iname "*.dbg.json" -exec cp {} eth_defi/abi/velvet \;

# Compile and copy Orderly contracts
orderly:
	@(cd contracts/orderly-contract-evm && \
		forge install && \
		mkdir -p out/custom && \
		forge in src/vaultSide/Vault.sol:Vault abi > out/custom/Vault.json && \
		forge in src/Ledger.sol:Ledger abi > out/custom/Ledger.json && \
		forge in src/OperatorManager.sol:OperatorManager abi > out/custom/OperatorManager.json && \
		forge in src/zip/OperatorManagerZip.sol:OperatorManagerZip abi > out/custom/OperatorManagerZip.json && \
		forge in src/vaultSide/Vault.sol:Vault abi > out/custom/Vault.json && \
		forge in src/VaultManager.sol:VaultManager abi > out/custom/VaultManager.json && \
		forge in src/FeeManager.sol:FeeManager abi > out/custom/FeeManager.json && \
		forge in src/MarketManager.sol:MarketManager abi > out/custom/MarketManager.json \
	) > /dev/null
	@mkdir -p eth_defi/abi/orderly
	@find contracts/orderly-contract-evm/out/custom -iname "*.json" -exec cp {} eth_defi/abi/orderly \;


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
compile-projects-and-prepare-abi: clean-abi sushi in-house guard safe-integration copy-uniswapv3-abi aavev3 enzyme dhedge centre 1delta

all: clean-docs compile-projects-and-prepare-abi build-docs

# HACK: poetry export is broken
# https://github.com/python-poetry/poetry-plugin-export/issues/176
# Export the dependencies, so that Read the docs can build our API docs
# See: https://github.com/readthedocs/readthedocs.org/issues/4912
# terms_of_service is in-place dev dependency, only used for tests and must be removed for RTD
rtd-dep-export:
	@pip freeze > /tmp/requirements.txt
	@grep -v 'terms-of-service' < /tmp/requirements.txt > /tmp/requirements2.txt
	@grep -v 'git+ssh' < /tmp/requirements2.txt > docs/requirements.txt
	@echo "-e ." >> docs/requirements.txt


# Build docs locally
build-docs:
	@(cd docs && make html)

# Nuke the old docs build to ensure all pages are regenerated
clean-docs:
	@find docs/source -iname "_autosummary*" -exec rm -rf {} +
	@rm -rf docs/build/html

docs-all: clean-docs build-docs

# Manually generate table of contents for Github README
toc:
	cat README.md | scripts/gh-md-toc -

# Open web browser on docs on macOS
browse-docs-macos:
	@open docs/build/html/index.html

# Deploy documentation to Cloudflare Pages
#
# Prerequisites:
# 1. Install wrangler: npm install -g wrangler
# 2. Create Cloudflare API token:
#    - Go to https://dash.cloudflare.com/profile/api-tokens
#    - Click "Create Token"
#    - Use "Edit Cloudflare Workers" template (includes Pages permissions)
#    - Or create custom token with: Account > Cloudflare Pages > Edit
# 3. Get your Account ID:
#    - Log in to Cloudflare dashboard
#    - Account ID is in the right sidebar under "Account details"
# 4. Set environment variables:
#    - export CLOUDFLARE_API_TOKEN=your_token_here
#    - export CLOUDFLARE_ACCOUNT_ID=your_account_id_here
#
# Usage:
#   make deploy-docs-cloudflare       (builds and deploys)
#   make deploy-docs-cloudflare-only  (deploys existing build)
#
deploy-docs-cloudflare: build-docs deploy-docs-cloudflare-only

deploy-docs-cloudflare-only:
	@if [ -z "$$CLOUDFLARE_API_TOKEN" ]; then echo "Error: CLOUDFLARE_API_TOKEN not set"; exit 1; fi
	@if [ -z "$$CLOUDFLARE_ACCOUNT_ID" ]; then echo "Error: CLOUDFLARE_ACCOUNT_ID not set"; exit 1; fi
	npx wrangler pages deploy docs/build/html --project-name=web3-ethereum-defi --commit-dirty=true
