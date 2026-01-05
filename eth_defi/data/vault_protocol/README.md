# Vault protocol metadata

This folder contains metadata for vault protocols.

1. Vault protocol names can be found in [eth_defi.erc_4626.core.get_vault_protocol_name()](../../erc_4626/core.py)

2. Each protocol is identified by its slug, which name lowercase, spaces replaced by dash. E.g. `Lagoon Finance` becomes `lagoon-finance`.

3. Each slug has a corresponding YAML file in [Strict YAML format](https://github.com/crdoconnor/strictyaml). E.g. for Lagoon Finance there is `lagoon-finance.yml`

4. Each protocol can contain multiple original logos and up to three formatted logos.

- Subfolder `original_logos` contains the logos obtained through the homepage or web search
- Subfoder `formatted_logos` contain the logos that have been adapted to the style guides
- The formatted logo is 256 x 256 px PNG file.
- For generic theme there is `{protocol slug}.generic.png`
- For white-background theme there is `{protocol slug}.light.png`
- For dark-background theme there is `{protocol slug}.dark.png`
- Either of the logo files might be present or missing

## Example YAML file fields

```yaml
name: { protocol name here }
slug: { same slug as in the filename }
short_description: |
  {one line description of the protocol}
long_description: |
  {multi-paragraph description of the protocol in Markdown format}
fee_description: |
  {multi-paragraph description of the fees the user might pay when using the vaults in Markdown format}
links:
  homepage: { web page link }
  app: { direct link to the Dapp page of vaults if available }
  twitter: { link to twitter account }
  github: { link to smart contracts github repo }
  documentation: { link to the developer documentation }
  defillama: { link to protocol defillama page if any }
  audits: { link to an audits page of protocol or a single audit }
  fees: { link to the page that describes fee structure }
  trading_strategy: { link to the protocol on the TradingStrategy.ai website, listed here https://tradingstrategy.ai/trading-view/vaults/protocols }
  integration_documentation: { link to protocol page here https://web3-ethereum-defi.readthedocs.io/vaults/index.html}
  
# List of links to the vault smart contracts on a blockchain explorer like Etherscan, Routescan.
# Can be anywhere between zero to multiple links.
# If there are no examples, this list is not present.
example_smart_contracts:
  - { example link to a smart contract on a blockchain explorer }
```

If any of the information missing the corresponding field is present, but left empty.
