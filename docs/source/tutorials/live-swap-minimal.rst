Uniswap v2 reading swaps real-time (minimal)
--------------------------------------------

This is a minimal example code for showing live swaps happening
on Uniswap v2 compatible examples.

- This example runs on a free Polygon JSON-RPC node.

- It will print out live trade events for *all* Uniswap v2 compatible exchanges.
  This includes QuickSwap, Sushi and couple of others.
  `See the full DEX list here <https://tradingstrategy.ai/trading-view/polygon>`__.

- It demonstrates the chain reorganisation detection and event reader API.

- See :doc:`the more complete example <./live-swap>`

To run:

.. code-block:: shell

    python scripts/uniswap-v2-swaps-live-minimal.py

Example output:

.. code-block:: text

    Swap at block:42,549,528 tx:0x2011d03b4f3d80992339eb6303b0b7b86ec77f629ce7f2508344e739c4536cc7
    Swap at block:42,549,528 tx:0x67af6d9d28634747d83f14d48bdc3d56421df7b686055c4519850b97e863291d
    .
    Block 42,549,527 is 0x83fd3f8dfd6065fcc3406fed9e81b069a45cf0e823fe4863c89a5e9cef49bdc6
    Block 42,549,528 is 0xb29ce58ad7267b5c9906eea32aeacf043965a7223a35e0aa3c495dcdf3815eac
    .
    .
    Swap at block:42,549,529 tx:0xa9ba7e61c1bdedf53b657419874d528b4164b9e286c3baf162f20f8d1c428b80
    .
    Block 42,549,529 is 0x686ebaa6ac7fa5f32aedbdc05e1352f681072acb46c0c158b779afd8e0fce21f
    .
    .


.. literalinclude:: ../../../scripts/uniswap-v2-swaps-live-minimal.py
   :language: python
