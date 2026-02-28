"""GMX execution buffer

The execution buffer is a safety multiplier applied to the estimated execution fee
when creating GMX V2 orders. It ensures that keepers — the off-chain agents responsible
for executing orders — are adequately compensated even if network gas prices spike
between order creation and execution. Any excess fee is refunded to the user.

Background: GMX's two-step order execution
-------------------------------------------

GMX V2 uses a two-step process for all actions (deposits, withdrawals, swaps,
position opens/closes):

1. **User creates** an order and pays an upfront execution fee in native tokens
   (e.g. ETH on Arbitrum). This fee is meant to compensate the keeper who will
   execute the order on-chain.

2. **A keeper picks up** the order, executes it, and is compensated from the
   execution fee the user paid. Any excess is refunded back to the user's account.

The risk is that gas prices may increase between steps 1 and 2. If the user paid
too little, the keeper would lose money and refuse to execute the order, causing it
to sit indefinitely or fail with ``InsufficientExecutionFee``.

The execution buffer solves this by **overestimating** the fee. Since excess is
always refunded, the only cost to the user is a temporary lock-up of native tokens.

How it works at each layer
--------------------------

**On-chain (GMX smart contracts)**

The GMX protocol applies its own gas estimation adjustment via the ``DataStore``
contract. The key function in ``GasUtils.sol`` is ``adjustGasLimitForEstimate()``:

.. code-block:: solidity

    adjustedGas = baseGasLimit
                + (oracleCount * perOracleGas)
                + applyFactor(estimatedGasLimit, multiplierFactor)

The relevant ``DataStore`` keys (defined in ``Keys.sol``) are:

- ``ESTIMATED_GAS_FEE_BASE_AMOUNT_V2_1`` — base gas added to all estimates
- ``ESTIMATED_GAS_FEE_PER_ORACLE_PRICE`` — additional gas per oracle price used
- ``ESTIMATED_GAS_FEE_MULTIPLIER_FACTOR`` — multiplier applied to the order gas limit
- ``EXECUTION_GAS_FEE_MULTIPLIER_FACTOR`` — multiplier used for actual execution

See:
- `GasUtils.sol <https://github.com/gmx-io/gmx-synthetics/blob/main/contracts/gas/GasUtils.sol>`_
- `Keys.sol <https://github.com/gmx-io/gmx-synthetics/blob/main/contracts/data/Keys.sol>`_

**GMX interface (frontend)**

The official GMX interface adds an additional client-side buffer on top of the
on-chain estimation, configured as ``executionFeeBufferBps`` (basis points):

- Arbitrum default: 3000 bps (30%)
- Avalanche default: 1000 bps (10%)

The buffer is applied to the gas price: ``finalGasPrice = gasPrice + (gasPrice * bufferBps / 10000)``.
Users can adjust this in the Settings modal under "Max Network Fee Buffer".

See:
- `chains.ts <https://github.com/gmx-io/gmx-interface/blob/main/sdk/src/configs/chains.ts>`_
- `executionFee.ts <https://github.com/gmx-io/gmx-interface/blob/main/src/domain/synthetics/fees/utils/executionFee.ts>`_

**This library (eth_defi)**

This library uses a simpler floating-point multiplier applied directly to the
base execution fee:

.. code-block:: python

    execution_fee = apply_execution_buffer(base_fee, execution_buffer=2.2)
    # Equivalent to: int(base_fee * 2.2)

The default of 2.2x (120% buffer) is more conservative than the GMX interface's
30%, because programmatic/automated trading has a higher cost of failed execution
and cannot rely on a user manually retrying.

Safety thresholds
-----------------

+---------------+------------------------------------------------------------------+
| Buffer value  | Behaviour                                                        |
+===============+==================================================================+
| < 1.2x        | **Critical** — keepers will likely reject the order              |
+---------------+------------------------------------------------------------------+
| < 1.5x        | **Warning** — risk of failure during gas spikes                  |
+---------------+------------------------------------------------------------------+
| 1.8–2.2x      | **Recommended** range for standard orders                        |
+---------------+------------------------------------------------------------------+
| 2.5x          | Default for bundled SL/TP orders (multiple sub-orders)           |
+---------------+------------------------------------------------------------------+

For SL/TP bundled orders, an additional ``execution_fee_buffer`` multiplier
(default 3.0x) is applied on top of the execution buffer to account for the
extra gas consumed by multicall batching of multiple orders.

See also
--------

- :py:mod:`eth_defi.gmx.gas_utils` — low-level gas limit fetching and GMX fee formula
- :py:mod:`eth_defi.gmx.gas_monitor` — gas balance monitoring and thresholds
- `GMX Trading Documentation <https://docs.gmx.io/docs/trading/v2/>`_
- `GMX Synthetics Repository <https://github.com/gmx-io/gmx-synthetics>`_
"""

import logging

from eth_defi.gmx.constants import (
    DEFAULT_EXECUTION_BUFFER,
    DEFAULT_SLTP_EXECUTION_BUFFER,
    DEFAULT_SLTP_EXECUTION_FEE_BUFFER,
    EXECUTION_BUFFER_CRITICAL_THRESHOLD,
    EXECUTION_BUFFER_RECOMMENDED_MAX,
    EXECUTION_BUFFER_RECOMMENDED_MIN,
    EXECUTION_BUFFER_WARNING_THRESHOLD,
)

logger = logging.getLogger(__name__)


def validate_execution_buffer(execution_buffer: float) -> None:
    """Check the execution buffer value and log warnings if it is dangerously low.

    This does not raise an exception — it only emits log messages so that the
    caller can proceed with the order while being informed of the risk.

    :param execution_buffer:
        The multiplier to validate. Values below
        :data:`EXECUTION_BUFFER_CRITICAL_THRESHOLD` (1.2) will emit a critical
        error; values below :data:`EXECUTION_BUFFER_WARNING_THRESHOLD` (1.5) will
        emit a warning.
    """
    if execution_buffer < EXECUTION_BUFFER_CRITICAL_THRESHOLD:
        logger.error(
            "CRITICAL: executionBuffer=%.1fx is DANGEROUSLY LOW! GMX keepers will likely reject this order. Minimum safe value: %.1fx. Recommended: %.1f-%.1fx. Your order may fail with InsufficientExecutionFee error.",
            execution_buffer,
            EXECUTION_BUFFER_WARNING_THRESHOLD,
            EXECUTION_BUFFER_RECOMMENDED_MIN,
            EXECUTION_BUFFER_RECOMMENDED_MAX,
        )
    elif execution_buffer < EXECUTION_BUFFER_WARNING_THRESHOLD:
        logger.warning(
            "WARNING: executionBuffer=%.1fx is very low. Consider increasing to %.1f-%.1fx to avoid order failures during gas spikes.",
            execution_buffer,
            EXECUTION_BUFFER_RECOMMENDED_MIN,
            EXECUTION_BUFFER_RECOMMENDED_MAX,
        )


def apply_execution_buffer(
    base_fee: int,
    execution_buffer: float,
    validate: bool = True,
) -> int:
    """Apply the execution buffer multiplier to a base execution fee.

    Multiplies the base fee by the given buffer to produce a fee that is
    high enough for GMX keepers to execute profitably. Any excess is
    refunded by the GMX contracts.

    Example::

        base_fee = gas_limits["total"] * gas_price
        execution_fee = apply_execution_buffer(base_fee, execution_buffer=2.2)

    :param base_fee:
        Raw execution fee in wei, typically ``gas_limit * gas_price``.
    :param execution_buffer:
        Multiplier to apply. See :data:`DEFAULT_EXECUTION_BUFFER` for the
        recommended default.
    :param validate:
        If ``True`` (default), call :func:`validate_execution_buffer` before
        applying. Set to ``False`` to skip validation (e.g. when the buffer
        has already been validated earlier in the call chain).
    :return:
        The buffered execution fee in wei.
    """
    if validate:
        validate_execution_buffer(execution_buffer)
    return int(base_fee * execution_buffer)
