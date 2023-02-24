"""Deploy Enzyme protcol v4.

Based on https://github.com/enzymefinance/protocol/blob/v4/packages/protocol/tests/release/e2e/FundManagementWalkthrough.test.ts
"""
from _decimal import Decimal

import pytest
from eth_abi import encode
from web3 import EthereumTesterProvider, Web3
from web3.contract import Contract

from eth_defi.enzyme.utils import convert_rate_to_scaled_per_second_rate
from eth_defi.token import create_token


@pytest.fixture
def tester_provider():
    # https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python
    return EthereumTesterProvider()


@pytest.fixture
def eth_tester(tester_provider):
    # https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python
    return tester_provider.ethereum_tester


@pytest.fixture
def web3(tester_provider):
    """Set up a local unit testing blockchain."""
    # https://web3py.readthedocs.io/en/stable/examples.html#contract-unit-tests-in-python
    return Web3(tester_provider)


@pytest.fixture()
def deployer(web3) -> str:
    return web3.eth.accounts[0]


@pytest.fixture()
def manager(web3) -> str:
    return web3.eth.accounts[1]


@pytest.fixture()
def usdc(web3, deployer) -> Contract:
    """Mock USDC token.

    Start with $10M pile of testnet dollars.
    """
    token = create_token(web3, deployer, "USD Coin", "USDC", 10_000_000 * 10**6, 6)
    return token


@pytest.fixture()
def seed_balance(web3) -> int:
    """How many tokens investors start with.

    100 USDC
    """
    return 100


@pytest.fixture()
def investor(
    web3,
    usdc: Contract,
    seed_balance
) -> str:

    address = web3.eth.accounts[2]
    # Fund accounts like in the source test case
    usdc.functions.transfer(address, 100 * 10**6).transact({"from": deployer})
    return address


@pytest.fixture()
def another_investor(
    web3,
    usdc: Contract,
    seed_balance
) -> str:

    address = web3.eth.accounts[3]
    # Fund accounts like in the source test case
    usdc.functions.transfer(address, 100 * 10**6).transact({"from": deployer})
    return address


def test_create_fund(
        deployer: str,
        manager: str,
        investor: str,
        another_investor: str,
        usdc: Contract,
):
    """Deploy Enzyme protocol on Ethereum Tester.

    See https://github.com/enzymefinance/protocol/blob/v4/packages/protocol/tests/release/e2e/FundManagementWalkthrough.test.ts
    """

   # // fees
   #    const scaledPerSecondRate = managementFeeConvertRateToScaledPerSecondRate(utils.parseEther('0.01')); // 1%
   #    const managementFeeSettings = managementFeeConfigArgs({ scaledPerSecondRate });
   #    const performanceFeeSettings = performanceFeeConfigArgs({
   #      rate: TEN_PERCENT,
   #    });
   #    const entranceRateBurnFeeSettings = entranceRateBurnFeeConfigArgs({ rate: FIVE_PERCENT });
   #
   #    const feeManagerConfig = feeManagerConfigArgs({
   #      fees: [fork.deployment.managementFee, fork.deployment.performanceFee, fork.deployment.entranceRateBurnFee],
   #      settings: [managementFeeSettings, performanceFeeSettings, entranceRateBurnFeeSettings],
   #    });
   #
   #    // TODO: add policies
   #
   #    const createFundTx = await createNewFund({
   #      denominationAsset,
   #      feeManagerConfig,
   #      fundDeployer: fork.deployment.fundDeployer,
   #      fundOwner: manager,
   #      signer: manager,
   #    });
   #
   #    comptrollerProxy = createFundTx.comptrollerProxy;
   #    vaultProxy = createFundTx.vaultProxy;
   #
   #    expect(createFundTx.receipt).toMatchGasSnapshot(denominationAssetId);
   #  });

    # export function managementFeeConfigArgs({
    #   scaledPerSecondRate,
    #   recipient = constants.AddressZero,
    # }: {
    #   scaledPerSecondRate: BigNumberish;
    #   recipient?: AddressLike;
    # }) {
    #   return encodeArgs(['uint256', 'address'], [scaledPerSecondRate, recipient]);
    # }

    # export function performanceFeeConfigArgs({
    #   rate,
    #   recipient = constants.AddressZero,
    # }: {
    #   rate: BigNumberish;
    #   recipient?: AddressLike;
    # }) {
    #   return encodeArgs(['uint256', 'address'], [rate, recipient]);
    # }

    # export function managementFeeConfigArgs({
    #   scaledPerSecondRate,
    #   recipient = constants.AddressZero,
    # }: {
    #   scaledPerSecondRate: BigNumberish;
    #   recipient?: AddressLike;
    # }) {
    #   return encodeArgs(['uint256', 'address'], [scaledPerSecondRate, recipient]);
    # }

    scaled_per_second_rate = convert_rate_to_scaled_per_second_rate(Decimal("0.01"))  # 1%
    management_fee_config_args = encode(['uint256', "address"], [scaled_per_second_rate, "0x0000000000000000000000000000000000000000"])
    performance_fee_settings = encode(['uint256', "address"], [int(0.10 * 10000), "0x0000000000000000000000000000000000000000"])
    entrance_rate_burn_fee_settings = encode(['uint256', "address"], [int(0.5 * 10000), "0x0000000000000000000000000000000000000000"])

    fee_manager_config = encode(
        ['address[]', "bytes[]"],
        [
            [],
            []
        ])
        # "0x0000000000000000000000000000000000000000"])

