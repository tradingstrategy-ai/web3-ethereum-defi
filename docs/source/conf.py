# Configuration file for the Sphinx documentation builder.
#
# This file only contains a selection of the most common options. For a full
# list see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Path setup --------------------------------------------------------------

# If extensions (or modules to document with autodoc) are in another directory,
# add these directories to sys.path here. If the directory is relative to the
# documentation root, use os.path.abspath to make it absolute, like shown here.
#
# import os
# import sys
# sys.path.insert(0, os.path.abspath('.'))


# -- Project information -----------------------------------------------------

project = "Web3 Ethereum Defi"
copyright = "2022, Mikko Ohtamaa"
author = "Mikko Ohtamaa"


# -- General configuration ---------------------------------------------------

# Add any Sphinx extension module names here, as strings. They can be
# extensions coming with Sphinx (named 'sphinx.ext.*') or your custom
# ones.
extensions = [
    "sphinx_rtd_theme",
    "sphinx_sitemap",
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    # https://github.com/tox-dev/sphinx-autodoc-typehints/issues/216
    # sphinx_autodoc_typehints'
]

# Add any paths that contain templates here, relative to this directory.
templates_path = ["_templates"]

# List of patterns, relative to source directory, that match files and
# directories to ignore when looking for source files.
# This pattern also affects html_static_path and html_extra_path.
exclude_patterns = []


# -- Options for HTML output -------------------------------------------------

# The theme to use for HTML and HTML Help pages.  See the documentation for
# a list of builtin themes.
#
html_theme = "sphinx_rtd_theme"

# Add any paths that contain custom static files (such as style sheets) here,
# relative to this directory. They are copied after the builtin static files,
# so a file named "default.css" will overwrite the builtin "default.css".
html_static_path = ["_static"]


autodoc_class_signature = "separated"

autodoc_typehints = "description"

autosummary_generate = True

add_module_names = False

html_context = {
    # https://stackoverflow.com/questions/62904172/how-do-i-replace-view-page-source-with-edit-on-github-links-in-sphinx-rtd-th
    # https://github.com/readthedocs/sphinx_rtd_theme/issues/529
    "display_github": True,
    "github_user": "tradingstrategy-ai",
    "github_repo": "web3-ethereum-defi",
    "github_version": "tree/master/docs/source/",
}

# Don't conflict with RTD supplied sitemap
sitemap_filename = "sitemap-generated.xml"
