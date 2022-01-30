# Compile all of Sushiswap fiels
sushi:
	(cd sushiswap && yarn install && yarn build)

# Extract all compilation artifacts from Sushi to our abi/ dump
copy-abi: sushi
	find sushiswap/artifacts/contracts -iname "*.json" -not -iname "*.dbg.json" -exec cp {} smart_contracts_for_testing/abi \;

all: copy-abi