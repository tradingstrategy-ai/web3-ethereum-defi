pragma solidity 0.6.12;

/**
 * See enzyme/deployment.py
 */
contract EnzymeTestEnvironment{

    address mln;
    address weth;

    constructor(
        address _mln,
        address _weth
    ) public {
        mln = _mln;
        weth = _weth;
    }

    function getMlnToken() external view returns (address) {
        return mln;
    }

    function getWethToken() external view returns (address) {
        return weth;
    }

    function getWrappedNativeToken() external view returns (address) {
        return weth;
    }
}