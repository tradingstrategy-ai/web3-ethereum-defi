# Aera ABI sources

Aera ABI fragments used by the ERC-4626 vault protocol adapter.

## Sources

- `AeraStrategy.json` combines the verified `AeraStrategy` getter ABI from Etherscan with the Yearn `TokenizedStrategy.performanceFee()` fallback getter exposed by deployed Aera strategy proxies.
- `AeraVaultV2.json` contains the verified Aera V2 vault fee getter ABI from Etherscan.

Example contracts:

- Aera Strategy USDC: https://etherscan.io/address/0x6593bb7272237f36444dee44df46ab3b0233a9a0
- Aera Strategy WBTC: https://etherscan.io/address/0x8041ba598f0e656ebe80c67289efb42c09e86ae3
- Aera V2 vault: https://etherscan.io/address/0xFA60E843a52eff94901f43ac08232b59351192cc
- Aera V2 vault: https://etherscan.io/address/0x14c79C24b2A82ce36e3F3D693aEea17e268F5a98

