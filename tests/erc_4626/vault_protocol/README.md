This folder contains vault protocol detection tests and focused adapter unit
tests.

- Detection checks use mainnet forks to verify that a new protocol can
  initialise its vault wrapper and read back metadata.
- Strategy-tag tests are no-RPC unit tests. They verify address-level
  classifications, the ``None`` missing-information result, and automatic
  protocol defaults. Add or update focused coverage for every newly added or
  newly covered vault when extending a protocol.

The tests here do not cover complex vault protocol integrations, only stub reads.
