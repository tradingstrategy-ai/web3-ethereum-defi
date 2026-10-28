Developer guide
===============

This guide explains how to contribute to the
`web3-ethereum-defi <https://github.com/tradingstrategy-ai/web3-ethereum-defi>`_
package. Applications using the package as a dependency can follow the API
documentation and tutorials instead.

Prerequisites
-------------

* You can program in Python 3.14
* You know how Git and GitHub work
* Linux or Mac recommended (We might not be able to support Windows users)

Checkout
--------

Check out the project locally:

.. code-block:: shell

    git clone git@github.com:tradingstrategy-ai/web3-ethereum-defi.git

We assume you use SSH keys with GitHub, but HTTPS checkout works as well.

Make sure you checkout the submodules, as we include Uniswap, Sushiswap and others as Git submodules:

.. code-block:: shell

   cd web3-ethereum-defi
   git submodule update --init --recursive

Prerequisites
-------------

* Pandoc (to build docs):

.. code-block:: shell

    brew install pandoc


Install with Poetry
-------------------

`Install Poetry <https://python-poetry.org/docs/#installation>`_.

Install the project with Poetry in development mode:

.. code-block:: shell

    cd web3-ethereum-defi
    poetry install -E data -E test -E docs -E hypersync -E ccxt -E cloudflare_r2 -E duckdb

Use Python 3.14 for the Poetry environment and run project commands through
``poetry run``. Include the listed extras so integrations and focused tests
have their required dependencies. Documentation sources are checked during
development; Sphinx builds run separately in CI.

Ganache
-------

.. warning ::

    Ganache is legacy and any Ganache-based code is no longer maintained. Use Foundry and Anvil instead.

Some features require ganache, to install it.

First install Node / NPM:

.. code-block:: shell

     brew install node  # For macOS

Then install Ganache itself:

.. code-block:: shell

     npm install -g ganache

Make sure your Ganache is version 7+:

.. code-block:: shell

    ganache --version

::

    ganache v7.0.3 (@ganache/cli: 0.1.4, @ganache/core: 0.1.4)

Smoke test
----------

Run the focused test files relevant to your change. The repository-root
``.local-test.env`` must already exist and source the test secrets from outside
the repository. Never edit that file. In a worktree, copy it from the main
checkout if it is missing.

.. code-block:: shell

    source .local-test.env && timeout 180s poetry run pytest tests/vault/test_fee.py

For worktrees sharing the parent checkout's Poetry environment, set
``PYTHONPATH`` so tests import the changed source:

.. code-block:: shell

    source .local-test.env && PYTHONPATH="$PWD" timeout 180s poetry run pytest tests/vault/test_fee.py

Avoid running the whole suite during development. Some integration tests need
JSON-RPC environment variables and skip when those variables are absent.

You can also run tests with logging enabled to get more information:

.. code-block:: shell

    source .local-test.env && timeout 180s poetry run pytest tests/vault/test_fee.py --tb=native --log-cli-level=info -x

This will

- Use native tracebacks

- Set console logging level to `INFO`

- Stop on the first failure

Formatting code
---------------

The code uses Ruff formatting with unlimited line length.

- All pull requests will be validated for valid ``ruff`` formatting.

Format the Python files changed for the task:

.. code-block:: shell

    poetry run ruff format path/to/changed.py path/to/new.py

Pull requests
-------------

Prepare the completed change for review using the workflow below.

Preparing a change for review
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

After completing a feature, bug fix or maintained content change, use the
repository's `clean-up-for-pr skill <https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/.claude/skills/clean-up-for-pr/SKILL.md>`__.
It covers code simplification, factual comments and public descriptions,
documentation updates, repository conventions and an independent review.

Update relevant agent guidance under ``.claude/docs`` and Sphinx source pages
and index references when their behaviour or usage changes. Check those sources
without building Sphinx locally, and leave generated ``_autosummary*`` files
to the documentation pipeline.

The skill specifies the opposite agent and requested model. Follow the
`agent CLI guide <https://github.com/tradingstrategy-ai/web3-ethereum-defi/blob/master/.claude/docs/agent-tricks-and-troubleshooting.md>`__
for tool access, streaming output and completed-review checks. Run focused
tests and format changed Python files before handing off the patch; commits,
pushes and publication follow the user's existing authorisation.

Dependency audits
-----------------

Dependency changes are checked in two ways in GitHub Actions:

- Pull requests that change ``pyproject.toml`` or ``poetry.lock`` run a dependency review check. This blocks pull requests that introduce known vulnerable dependencies without re-running a full audit for every change.
- A separate ``Dependency audit`` workflow runs weekly on ``master`` and can also be started manually from the Actions tab. It installs the same Poetry environment used in CI and runs ``pip-audit`` against the local environment.
- The weekly/manual audit writes a Markdown summary to the workflow summary page and uploads machine-readable artefacts named ``pip-audit.json`` and ``pip-audit.cyclonedx.json`` for follow-up work.

Documentation dependencies
--------------------------

This repository uses `poetry` to manage dependencies, but Read The Docs,
where docs are continuously build, only supports `pip`.
You need to update Read the Docs dependencies manually
if you update `pyproject.toml`.

To update dependencies for Read the Docs run:

.. code-block:: shell

    poetry update
    poetry export \
        --with=dev \
        --extras=data \
        --extras=docs \
        --without-hashes \
        --format=requirements.txt > docs/requirements.txt

    # Include self
    echo "-e ." >> docs/requirements.txt

    # Check we generated a good file
    head docs/requirements.txt


- See `.readthedocs.yml` for further details.

- See `Generating requirements.txt with Poetry <https://testdriven.io/tips/eb1fb0f9-3547-4ca2-b2a8-1c037ba856d8/>`__.

- See `including your own package in pip requirements.txt list <https://stackoverflow.com/questions/51010251/what-does-e-in-requirements-txt-do>`__

Pull request quality checklist
------------------------------

- ✅ The Python code passes `ruff formatting conventions <https://flake8.pycqa.org/en/latest/>`_.
  Run `poetry run flake8` and you should get a clean output. Note that Github Action will complain on these
  when you open a pull request.

- ✅ Every Python module has a sensible docstring in the format single line description + long description.
  See existing modules for examples.

- ✅ Every Python function has a sensible docstring in the format single line description + long description.
  See existing modules for examples.

- ✅ Every Python function that library users call have their parameters documented.

- ✅ Every Python function that library users call has a code example in the docstring.

- ✅ Every Python function has a unit test and unit test comes with a proper docstring.

- ✅ Any new functions are added to the documentation. Run `cd docs && make clean html` and then open `docs/build/html/index.html`
  to view documentation locally. See existing Sphinx documentation for examples how to include your module in the autogenerated
  documentation.

- ✅ `CHANGELOG.md` contains a line for the change if it is a library user facing feature.


Rebuilding smart contract compilation artifacts
-----------------------------------------------

All smart contracts should be precompiled in the Github repository. If you need to recompile them, you need to have Gnu make.

You will need `yarn` in the additional to `npm`:

.. code-block:: shell

    npm install -g yarn

Get make:

.. code-block:: shell

    brew install make

Then you can run the command to recompile all the smart contracts:

.. code-block:: shell

    make all
