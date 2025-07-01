Developer guide
===============

Here are instructions for new developers how to get started with the development of `web3-ethereum-defi <https://github.com/tradingstrategy-ai/web3-ethereum-defi>`_ package.
This concerns you only if you indent to contribute to the package code itself - if you use the package as a dependency then this might not be relevant for oyu.

Prerequisites
-------------

* You can program in Python
* You now how Git and Github work
* Linux or Mac recommended (We might not be able to support Windows users)

Checkout
--------

Check out the project locally:

.. code-block:: shell

    git clone git@github.com:tradingstrategy-ai/web3-ethereum-defi.git

We assume you use SSH keys with Github, but HTTPS checkout works as well.

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
    poetry shell  # Enter to Poetry/virtualenv enabled shell
    poetry install -E docs -E data

Note that `-E docs` is important, otherwise Sphinx package won't be installed and you cannot build docs.

`Check instructions here if you need to change Python version from your default to Python 3.8+ <https://stackoverflow.com/questions/70064449/how-to-rebuild-poetry-environment-from-scratch-and-force-reinstall-everything/70064450#70064450>`_.

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

Check that the tests of unmodified master branch pass:

.. code-block:: shell

     pytest

For fast parallel test execution run with ``pytest-xdist`` across all of your CPUs:

.. code-block::

    pytest -n auto --dist loadscope

You should get all green.

Some tests will be skipped, because they require full EVM nodes. JSON-RPC needs to be configured through environment variables.

You can also run tests with logging enabled to get more information:

.. code-block:: shell

    pytest --tb=native --log-cli-level=info -x

This will

- Use native tracebacks

- Set console logging level to `INFO`

- Stop on the first failure

Formatting code
---------------

The code uses Ruff formatting with unlimited line length.

- All pull requests will be validated for valid ``ruff`` formatting.

To format any of your code:

.. code-block:: shell

    # ruff comes in dev dependencies
     poetry run ruff format .

Pull requests
-------------

For new feature requests, make sure your pull request satisfies the checklist below and enjoy merge party.

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
