---
name: Identify vault protocol
description: Identify an unknown vault protocol based on its smart contract address
---

# Identify vault protocol

This skill attempts to identify a vault protocol based on its smart contract source.

## Required inputs

1. **Chain and smart contract address**: Given as an explorer link

## Step 1. Get the smart contract source code and deployer name

If there a named deployer address for the smart contract, save it as a clue as well. The "deployer" word in the name is not part of the protocol name.

Get it from the blockchain explorer.

If you cannot get the smart contract source code, abort.

The deployer name is the strongest indicator of the protocol name if available.

## Step 2. Github search for the smart contract keyword and adress

Do two separate searches on Github public repositories.

- Deployer name
- One for the smart contract contract address and smart contract address only
- One for the smart contract name and smart contract name only

Try to identify the main repository where the contract development happens.

Use Github MCP tool.

If it looks like the protocol is using smart contracts from someone else, then make a note of this "Smart contracts are developed int the project X and protocol Y is using them." In this case we are interested in protocol Y in the further steps.

If there is a separation between who owns the smart contract and who is the deployer, then follow the deployer clue for the next steps.

## Step 3: Web search for the smart contract keyword

Same search steos as above but do web search.

IGNORE ALL RESULTS ON TRADING STRATEGY WEBSITE AND ETH_DEFI REPOSITORY, AS WE CANNOT REFLECT BACK TO OURSELVES.

## Step 4: Twitter search

Same search steos as above but do web search.

## Step 5: Check web properties of a protocol

If it looks like there are is a good match for some protocol we have not yet listed

- Try to find its homepage
- Try to find its Twitter
- Try to find its documentation link
- Try to find a page on the protocol website which allows you to deposit the page. Usually called "app", "vaults", "strategies", "markets", "earn", "staking" or similar and is linkned from homepage.

If the search results from the earlier steps do not give good results, then ask for the human input what to attempt next.

## Step 6: Audit reports

Check the protocol website, documentation site and web search for smart contract audits.

For web search, use keywords

- {protocol name} and audit and Solidity

Also if there is a sepearate developer, do another search with use keywords

- {developer name} and audit and Solidity

Usually audit reports are available as PDF or report-like web page.

## Analyse

Give bullet points output that contains:

- Chain
- Address
- Explorer link
- Protocol name
- Web page
- Github repository
- Documentation link
- Link to audit documents
- Notes
- If the smart cotract is developed by someone else, name the developer and link the developer Github repository

The output format should be a Markdown block.

- Display the output in the chat
- Save the result in `docs/protocol-research/{protocol name slugged}.md`.
