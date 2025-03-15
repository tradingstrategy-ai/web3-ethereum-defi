# GMX Integration for eth_defi: Implementation Plan

## Goal

The goal of this project is to integrate GMX with eth_defi, enabling users to interact with GMX's decentralized perpetual futures exchange through eth_defi's infrastructure. This integration will allow users to execute trades, manage positions, and access real-time and historical market data directly from eth_defi.

## Use Cases

1. **User Trading**: User X wants to open a leveraged long position on WETH using USDC as collateral. For this, we create a function `open_position` that interacts with GMX's smart contracts to execute the trade.
2. **Position Management**: User Y wants to close a portion of their existing short position on ETH and withdraw part of their collateral. For this, we create a function `manage_position` that handles partial closures and collateral withdrawals.
3. **Real-Time Data**: User Z wants to monitor the funding rate and open interest for a specific market in real-time. For this, we create a function `get_real_time_data` that fetches and displays this information.
4. **Historical Data**: User W wants to analyze the performance of their past trades. For this, we create a function `get_historical_data` that retrieves historical trade data from GMX.

## Architecture and Integration

### Architecture

The integration will be structured into several modules:

##### Modules

1. **GMXClient**:
   - `GMXClient(config)`: Initializes the client with the given configuration.
     ```python
     config = {
         "web3_provider": "https://arbitrum-rpc.com",
         "chain_id": 42161,
         [..]
     }
     ```
   - `open_position(market_key, collateral_address, index_token_address, is_long, size_delta_usd, initial_collateral_delta_amount, slippage_percent, swap_path)`: Opens a new position.
   - `close_position(market_key, collateral_address, index_token_address, is_long, size_delta_usd, initial_collateral_delta_amount, slippage_percent, swap_path)`: Closes an existing position.
   - `get_data(market_key, start_block, end_block)`: Fetches market data for a given block range.

2. **TradingModule**:
   - `open_position(user_address, market_symbol, collateral_symbol, is_long, size_usd, leverage)`: Opens a leveraged position.
   - `close_position(user_address, market_symbol, collateral_symbol, is_long, size_usd)`: Closes a position.
   - `manage_position(user_address, market_symbol, collateral_symbol, is_long, size_usd, collateral_amount)`: Manages an existing position.

3. **DataModule**:
   - `get_data(market_symbol, start_block, end_block)`: Fetches data for a specific market and block range.

4. **Utils**:
   - `get_contract(contract_name)`: Returns the contract instance for a given contract name.
   - `format_date(date)`: Formats a date string to the required format.
   
   
   
   **N.B.** Exact parameters and design of choice  may vary 

### Events and Smart Contracts

#### Involved Events and Smart Contracts

- **Smart Contracts**:
  - `ExchangeRouter`: For creating and managing orders.
  - `ExchangeRouter.sol`
    - https://github.com/gmx-io/gmx-synthetics/blob/main/contracts/router/ExchangeRouter.sol
  - `Reader`: For reading market and position data. 
    - `Reader.sol`
        - https://github.com/gmx-io/gmx-synthetics/blob/main/contracts/reader/Reader.sol
        - https://arbiscan.io/address/0x0537C767cDAC0726c76Bb89e92904fe28fd02fE1#readContract
    
  - `GlvReader`: For reading GLV-specific data.
    - `GivReader.sol`
        - https://github.com/gmx-io/gmx-synthetics/blob/main/contracts/reader/GlvReader.sol
  - DataStore: DataStore for all general state values. It acts as a single source of truth where many different kinds of data—from numeric values to addresses, booleans, strings, arrays, and even sets—is stored and managed. Only authorized controllers (as defined by the GMX access control modules) are allowed to modify this data.  
    - `DataStore.sol`
        - https://github.com/gmx-io/gmx-synthetics/blob/main/contracts/data/DataStore.sol
- **Source Code**:
  - GMX smart contract source code will be integrated as a git submodule in `eth_defi/contracts`.

#### How to Build

1. Clone the GMX smart contract repository as a git submodule.

```sh
# Clone and setup
git submodule add https://github.com/gmx-io/gmx-contracts.git eth_defi/contracts/gmx
cd eth_defi/contracts/gmx

# To compile contracts:

npx hardhat compile

# To run all tests:

npx hardhat test

export NODE_OPTIONS=--max_old_space_size=4096 # may be needed to run tests.

# To print code metrics:

npx ts-node metrics.ts


# To print test coverage:

npx hardhat coverage
```

2. Set up the development environment with the required Python libraries. (Information will be provided on the readme page of the module)
3. Implement the modules and functions as described. (Module README)
4. Write tests to ensure the functionality and reliability of the integration.

### Other Integrations

- **Subgraphs/Indexers**: The subgraphs provide data like `Token Information, RewardToken, LiquidityPoolFee, DerivPerpProtocol, UsageMetricsDailySnapshot, UsageMetricsHourlySnapshot, FinancialsDailySnapshot` etc.
    - Endpoints: 
        - Devs Suggested: https://gmx.squids.live/gmx-synthetics-arbitrum:live/api/graphql
        - Example:
            ```graphql
            query MyQuery {
                marketInfo {
                    id
                    longOpenInterestInTokens
                    longOpenInterestInTokensUsingLongToken
                    longOpenInterestInTokensUsingShortToken
                    shortOpenInterestInTokens
                    shortOpenInterestInTokensUsingLongToken
                    shortOpenInterestInTokensUsingShortToken
                    longOpenInterestUsd
                    longOpenInterestUsingLongToken
                    longOpenInterestUsingShortToken
                    shortOpenInterestUsd
                    shortOpenInterestUsingLongToken
                    shortOpenInterestUsingShortToken
                }
            }
            ```

            ```json
            {
                "data": {
                    "marketInfos": [
                        {
                            "id": "0xe2fEDb9e6139a182B98e7C2688ccFa3e9A53c665",
                            "longOpenInterestInTokens": "0",
                            "longOpenInterestInTokensUsingLongToken": "0",
                            "longOpenInterestInTokensUsingShortToken": "0",
                            "shortOpenInterestInTokens": "0",
                            "shortOpenInterestInTokensUsingLongToken": "0",
                            "shortOpenInterestInTokensUsingShortToken": "0",
                            "longOpenInterestUsd": "0",
                            "longOpenInterestUsingLongToken": "0",
                            "longOpenInterestUsingShortToken": "0",
                            "shortOpenInterestUsd": "0",
                            "shortOpenInterestUsingLongToken": "0",
                            "shortOpenInterestUsingShortToken": "0"
                        },
                        {
                            "id": "0x47c031236e19d024b42f8AE6780E44A573170703",
                            "longOpenInterestInTokens": "34804233385",
                            "longOpenInterestInTokensUsingLongToken": "3588784280",
                            "longOpenInterestInTokensUsingShortToken": "31215449105",
                            "shortOpenInterestInTokens": "30052902248",
                            "shortOpenInterestInTokensUsingLongToken": "11023337021",
                            "shortOpenInterestInTokensUsingShortToken": "19029565227",
                            "longOpenInterestUsd": "30144834183408837182019585682814599010",
                            "longOpenInterestUsingLongToken": "3233510783148258137752918913918705000",
                            "longOpenInterestUsingShortToken": "26911323400260579044266666768895894010",
                            "shortOpenInterestUsd": "24325893965336468729215281830714953267",
                            "shortOpenInterestUsingLongToken": "8317431370090659206876246234024058727",
                            "shortOpenInterestUsingShortToken": "16008462595245809522339035596690894540"
                        },
                        {
                            "id": "0x45aD16Aaa28fb66Ef74d5ca0Ab9751F2817c81a4",
                            "longOpenInterestInTokens": "0",
                            "longOpenInterestInTokensUsingLongToken": "0",
                            "longOpenInterestInTokensUsingShortToken": "0",
                            "shortOpenInterestInTokens": "0",
                            "shortOpenInterestInTokensUsingLongToken": "0",
                            "shortOpenInterestInTokensUsingShortToken": "0",
                            "longOpenInterestUsd": "0",
                            "longOpenInterestUsingLongToken": "0",
                            "longOpenInterestUsingShortToken": "0",
                            "shortOpenInterestUsd": "0",
                            "shortOpenInterestUsingLongToken": "0",
                            "shortOpenInterestUsingShortToken": "0"
                        }
                    ]
                }
            }
            ```


    
    
    
    - `Arbitrum One`: https://thegraph.com/explorer/subgraphs/E15amJKR3s5Wsaa4GeVhHcCzoo7jSu1Kk8SNqY4XXH9i?view=Query&chain=arbitrum-one
    - `Avalanche`: https://thegraph.com/explorer/subgraphs/6pXgnXcL6mkXBjKX7NyHN7tCudv2JGFnXZ8wf8WbjPXv?view=Query&chain=arbitrum-one

        - Example:
            ```sh
            curl -X POST \
            -H "Content-Type: application/json" \
            -d '{"query": "{ tokenStats(first: 2) { id token poolAmount poolAmountUsd period reservedAmount reservedAmountUsd usdgAmount timestamp } hourlyVolume(id: "") { burn margin mint swap } }", "operationName": "Subgraphs", "variables": {}}' \
            https://gateway.thegraph.com/api/{api-key}/subgraphs/id/E15amJKR3s5Wsaa4GeVhHcCzoo7jSu1Kk8SNqY4XXH9i
            ```

            ```json
            {
            "data": {
                "hourlyVolume": null,
                "tokenStats": [
                {
                    "id": "1629936000:weekly:0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f",
                    "period": "weekly",
                    "poolAmount": "256630533",
                    "poolAmountUsd": "123664756691541501322200000000000000",
                    "reservedAmount": "47741",
                    "reservedAmountUsd": "22698860083365099600000000000000",
                    "timestamp": 1629936000,
                    "token": "0x2f2a2543b76a4166549f7aab2e75bef0aefc5b0f",
                    "usdgAmount": "123473085701470000000000"
                },
                {
                    "id": "1629936000:weekly:0x82af49447d8a07e3bd95bd0d56f35241523fbab1",
                    "period": "weekly",
                    "poolAmount": "255847036218295555644",
                    "poolAmountUsd": "975986894059865462212712735159400000",
                    "reservedAmount": "24746566571469294133",
                    "reservedAmountUsd": "93389510350686684637389469175750000",
                    "timestamp": 1629936000,
                    "token": "0x82af49447d8a07e3bd95bd0d56f35241523fbab1",
                    "usdgAmount": "917727094391062984147017"
                }
                ]
            }
            ```

### Events
All events are emitted on the `EventEmitter` contract. Each event from the `EventEmitter` will have an `eventName`, so events can be monitored just by specifying the EventEmitter address and the eventName to be monitored. [`EventEmitter.sol`](https://github.com/gmx-io/gmx-synthetics/blob/main/contracts/event/EventEmitter.sol) contract. 

Deployments:
- Arbitrum: https://arbiscan.io/address/0xC8ee91A54287DB53897056e12D9819156D3822Fb#events
- Avalanche: https://snowtrace.io/address/0xDb17B211c34240B014ab6d61d4A31FA0C0e20c26/events

### Dependencies

- **New Python Libraries**:
  - `web3.py`: For interacting with Ethereum smart contracts.
  - `requests`: For making API calls to GMX's REST endpoints.
  - `pandas`: For handling and analyzing historical data.

## Deliverables

### Created New

#### Modules

1. **GMXClient**:
   - `GMXClient(config)`: Initializes the client with the given configuration.
   
    Example:
    - Config can be defined as a separate class like this
    ```py
        class ConfigManager:

        def __init__(self, chain: str):

            self.chain = chain
            self.rpc = None
            self.chain_id = None
            self.user_wallet_address = None
            self.private_key = None
            self.tg_bot_token = None
        [..]
    ```

   - `open_position(market_key, collateral_address, index_token_address, is_long, size_delta_usd, initial_collateral_delta_amount, slippage_percent, swap_path)`: Opens a new position.
   - `close_position(market_key, collateral_address, index_token_address, is_long, size_delta_usd, initial_collateral_delta_amount, slippage_percent, swap_path)`: Closes an existing position.
   - `get_real_time_data(market_key)`: Fetches real-time market data.
   - `get_historical_data(market_key, start_date, end_date)`: Fetches historical market data.

2. **TradingModule**:
   - `open_position(user_address, market_symbol, collateral_symbol, is_long, size_usd, leverage)`: Opens a leveraged position.
   - `close_position(user_address, market_symbol, collateral_symbol, is_long, size_usd)`: Closes a position.
   - `manage_position(user_address, market_symbol, collateral_symbol, is_long, size_usd, collateral_amount)`: Manages an existing position.

3. **DataModule**:
   - `get_real_time_data(market_symbol)`: Fetches real-time data for a specific market.
   - `get_historical_data(market_symbol, start_date, end_date)`: Fetches historical data for a specific market.

4. **Utils**:
   - `get_contract(contract_name)`: Returns the contract instance for a given contract name.
   - `format_date(date)`: Formats a date string to the required format.

#### Tests

- Unit tests for each function in the modules.
- Integration tests to ensure the modules work together seamlessly.

#### Documentation

- Detailed documentation for each module and function.
- Examples and usage guides for developers and users.

#### Other Integrations

- GMX smart contract source code tree in `eth_defi/contracts` as a git submodule.

### For Each Deliverable, Include Phase of the Project

1. **Phase 1: Setup and Initial Integration**
   - Clone GMX smart contract repository.
   - Set up the development environment.
   - Implement `GMXClient` module.

2. **Phase 2: Trading Functionality**
   - Implement `TradingModule`.
   - Write tests for trading functionality.

3. **Phase 3: Data Functionality**
   - Implement `DataModule`.
   - Write tests for data functionality.

4. **Phase 4: Utils and Final Integration**
   - Implement `Utils` module.
   - Integrate all modules and ensure they work together.
   - Write final documentation and examples.

By following this specification, we will create a robust integration of GMX with eth_defi, enabling users to leverage the decentralized perpetual futures exchange directly from eth_defi's infrastructure.

---

## Project Structure

```plaintext
eth_defi/
├── contracts/
│   └── gmx/                  # Git submodule for GMX contracts
├── gmx/
│   ├── __init__.py
│   ├── trading.py            # GMX leveraged trading
│   ├── staking.py            # GMX staking and rewards
│   ├── events.py             # Event listeners
│   ├── api.py                # GMX API interactions
│   ├── constants.py          # Contract addresses, ABIs
├── tests/
│   ├── test_gmx_trading.py
│   ├── test_gmx_staking.py
│   ├── test_gmx_events.py
│   ├── test_gmx_api.py
```

---

## Core Modules

### `gmx/trading.py`

```python
from web3 import Web3
from eth_defi.gmx.constants import ARBITRUM_GMX_VAULT_ADDRESS

class GMXTrading:
    def __init__(self, web3: Web3, chain: str = "arbitrum"):
        self.web3 = web3
        self.vault_address = ARBITRUM_GMX_VAULT_ADDRESS if chain == "arbitrum" else AVALANCHE_GMX_VAULT_ADDRESS
        self.vault_contract = self.web3.eth.contract(
            address=self.vault_address,
            abi=load_contract_abi("IGmxVault.json")
        )

    def open_leveraged_position(
        self,
        user_address: str,
        collateral_token: str,
        index_token: str,
        size_delta: int,
        is_long: bool,
        leverage: int,
        slippage: float
    ) -> dict:
        """Open a leveraged position on GMX."""
        # Implementation using vault contract methods
        return tx_receipt
```

### `gmx/staking.py`

```python
from web3 import Web3

class GMXStaking:
    def __init__(self, web3: Web3):
        self.web3 = web3
        self.staking_contract = self.web3.eth.contract(
            address=STAKING_CONTRACT_ADDRESS,
            abi=load_contract_abi("IGmxStaking.json")
        )

    def stake(self, amount: int) -> dict:
        """Stake GMX tokens."""
        return self.staking_contract.functions.stake(amount).build_transaction({
            'from': self.web3.eth.default_account,
            'nonce': self.web3.eth.get_transaction_count(self.web3.eth.default_account)
        })
```

### `gmx/api.py`


#### According to the devs
Originally the prices are fetched from chain link oracles. Our api relays the calls to it. 

The GMX API endpoints (e.g., `https://arbitrum-api.gmxinfra.io`) are centralized servers provided by GMX's infrastructure partners. These endpoints offer:
- **Price Tickers:** Quick access to current asset prices.
- **Candlestick Data:** Historical market data for constructing charts.



```python
import requests

GMX_API_ENDPOINTS = {
    "arbitrum": {
        "prices": "https://arbitrum-api.gmxinfra.io/prices/tickers",
        "candles": "https://arbitrum-api.gmxinfra.io/prices/candles"
    }
}

class GMXAPI:
    def get_historical_prices(self, token: str, period: str = "1d", chain: str = "arbitrum"):
        params = {
            "tokenSymbol": token,
            "period": period
        }
        response = requests.get(
            GMX_API_ENDPOINTS[chain]["candles"],
            params=params
        )
        return response.json()
```

### `gmx/events.py`

```python
from web3 import Web3

class GMXEvents:
    def __init__(self, web3: Web3):
        self.web3 = web3
        self.event_contract = self.web3.eth.contract(
            address=EVENT_CONTRACT_ADDRESS,
            abi=load_contract_abi("IGmxEvents.json")
        )

    def listen_to_events(self, event_name: str, callback: callable):
        """Listen to specific GMX events."""
        # Implementation for event listening
        pass
```

---

## Testing Example

### `tests/test_gmx_trading.py`

```python
import pytest
from eth_defi.gmx.trading import GMXTrading
from web3 import Web3

@pytest.fixture
def mock_web3():
    return Web3.HTTPProvider("https://arbitrum-node-url.com")

def test_open_position(mock_web3):
    trading = GMXTrading(mock_web3)
    receipt = trading.open_leveraged_position(
        user_address="0x...",
        collateral_token="0x...",
        index_token="ETH",
        size_delta=100_000 * 1e30,
        is_long=True,
        leverage=3,
        slippage=0.02
    )
    assert receipt["status"] == 1
```

---

## Documentation

### Installation

```bash
pip install eth_defi[gmx]
```

### Usage

```python
from eth_defi.gmx.trading import GMXTrading
from web3 import Web3

web3 = Web3(Web3.HTTPProvider("https://arbitrum-rpc.com"))
trading = GMXTrading(web3)

# Open 3x long ETH position
receipt = trading.open_leveraged_position(
    user_address="0x...",
    collateral_token="0x...",
    index_token="ETH",
    size_delta=100_000 * 1e30,
    is_long=True,
    leverage=3,
    slippage=0.02
)
```

---

## CI/CD Pipeline

### `.github/workflows/test.yml`

```yaml
name: GMX Tests

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
      - run: pip install -r requirements.txt
      - run: pytest tests/ -v
```

---

## Security Considerations

- Private key management through environment variables.
- Slippage protection in trading functions.
- Input validation for all user-facing methods.
- Rate limiting for API calls.

---

This implementation provides:

1. Full integration with GMX smart contracts.
2. Real-time market data access.
3. Event monitoring system.
4. Comprehensive test coverage.
5. Production-ready error handling.
6. Multi-chain support (Arbitrum/Avalanche).

The architecture follows eth_defi's existing patterns while extending functionality for GMX-specific operations. Each component is modular and can be used independently or as part of larger DeFi strategies.



### Example
Example of collecting the latest candle data for the last `1` minute for `ETH` in terms of `USD`

```py

class GMXDataCollector:
    def __init__(self, chain: str):
        if chain.lower() == "arbitrum":
            self.base_url = "https://arbitrum-api.gmxinfra.io"
            self.alternative_url = "https://arbitrum-api.gmxinfra2.io"
        else:
            self.base_url = "https://avalanche-api.gmxinfra.io"
            self.alternative_url = "https://avalanche-api.gmxinfra2.io"

    def _make_request(self, endpoint: str, params: dict = None) -> dict:
        """Make a request to the API and handle exceptions."""
        try:
            url = f"{self.base_url}{endpoint}"
            response = requests.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"Error with primary URL: {e}")
            try:
                url = f"{self.alternative_url}{endpoint}"
                response = requests.get(url, params=params)
                response.raise_for_status()
                return response.json()
            except requests.RequestException as e:
                print(f"Error with alternative URL: {e}")
                return {}

    def ping(self) -> dict:
        """Check the endpoint status."""
        return self._make_request("/ping")

    def get_tickers(self) -> dict:
        """Get the latest price information for pricing display."""
        return self._make_request("/prices/tickers")

    def get_signed_prices(self) -> dict:
        """Get the latest signed price information for sending transactions."""
        return self._make_request("/signed_prices/latest")

    def get_candlesticks(self, token_symbol: str, period: str) -> dict:
        """Get candlestick data."""
        params = {
            "tokenSymbol": token_symbol,
            "period": period
        }
        return self._make_request("/prices/candles", params=params)

    def get_tokens(self) -> dict:
        """Get list of supported tokens."""
        return self._make_request("/tokens")

def plot_candle_stick(candlesticks: dict, token_symbol: str, unit_of_price: str):
    # Convert candlestick data to a DataFrame
    df = pd.DataFrame(candlesticks, columns=["period", "candles"])
    df[["timestamp", "open", "high", "low", "close"]] = pd.DataFrame(df["candles"].tolist(), index=df.index)

    # Create the candlestick chart
    fig = go.Figure(data=[go.Candlestick(x=df["timestamp"],
                                        open=df["open"],
                                        high=df["high"],
                                        low=df["low"],
                                        close=df["close"])])

    # Update layout
    fig.update_layout(title=f"Candlestick Chart for {token_symbol}",
                    xaxis_title="Time",
                    yaxis_title=f"Price ({unit_of_price})",
                    xaxis_rangeslider_visible=False)

    # Show the figure
    fig.show()


if __name__ == "__main__":
	arbitrum_collector = GMXDataCollector("arbitrum")
	token_symbol = "ETH"
    unit_of_price = "USD"

    # Example usage
    candlesticks = arbitrum_collector.get_candlesticks(token_symbol, "1m")
    plot_candle_stick(candlesticks, token_symbol, unit_of_price)
```


![image](https://github.com/user-attachments/assets/2255961d-a334-4d14-8448-658478f99287)
