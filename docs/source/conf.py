# Configuration file for the Sphinx documentation builder.
#
# This file only contains a selection of the most common options. For a full
# list see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------


project = "Web3 Ethereum Defi"
copyright = "2022, Market Software Ltd"
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
    "sphinx.ext.viewcode",
    # https://github.com/tox-dev/sphinx-autodoc-typehints/issues/216
    # sphinx_autodoc_typehints'
    "nbsphinx",
    "sphinx.ext.intersphinx",
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
html_theme = "furo"

# Add any paths that contain custom static files (such as style sheets) here,
# relative to this directory. They are copied after the builtin static files,
# so a file named "default.css" will overwrite the builtin "default.css".
html_static_path = ["_static"]

autodoc_class_signature = "separated"

autodoc_member_order = "bysource"

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

#
# All notebooks in documentation needs an API key and must be pre-executed
# https://nbsphinx.readthedocs.io/en/0.8.6/never-execute.html
#
nbsphinx_execute = "never"

#     <script src='https://cdnjs.cloudflare.com/ajax/libs/require.js/2.1.10/require.min.js'></script>
#     <script>require=requirejs;</script>
#     <script src='https://cdnjs.cloudflare.com/ajax/libs/plotly.js/1.33.1/plotly.min.js'></script>


nbsphinx_prolog = """


.. raw:: html

    <a style="display: block; margin-top: 1.5rem" href="https://mybinder.org/v2/gh/tradingstrategy-ai/web3-ethereum-defi/master?labpath=docs/source/{{ env.doc2path(env.docname, base=None) }}">
        <img src="https://mybinder.org/badge_logo.svg">
    </a>    

"""

# Grabbed from https://github.com/pandas-dev/pandas/blob/master/doc/source/conf.py
intersphinx_mapping = {
    "pandas": ("http://pandas.pydata.org/pandas-docs/dev", None),
    "matplotlib": ("https://matplotlib.org/stable/", None),
    "numpy": ("https://numpy.org/doc/stable/", None),
    "py": ("https://pylib.readthedocs.io/en/latest/", None),
    "python": ("https://docs.python.org/3/", None),
}

html_js_files = [
    "require.js",
    # 'plotly-2.12.1.min.js',
    "custom.js",
]

# nbsphinx_requirejs_options = {
# 	"src": "https://cdnjs.cloudflare.com/ajax/libs/require.js/2.1.10/require.min.js",
# 	"integrity": "sha512-VCK7oF67GXNc+J7zsu5o57jtxhLA75nSMHGaq8Q8TCOxDj4nMDw5dhQZvm9Cd9RN+3zgcodqbKcRc9gyPP8a2w==",
# 	"crossorigin": "anonymous"
# }

# Monkey-patch autosummary template context
from sphinx.ext.autosummary.generate import AutosummaryRenderer


def smart_fullname(fullname):
    parts = fullname.split(".")
    return ".".join(parts[1:])


def fixed_init(self, app, template_dir=None):
    AutosummaryRenderer.__old_init__(self, app, template_dir)
    self.env.filters["smart_fullname"] = smart_fullname


AutosummaryRenderer.__old_init__ = AutosummaryRenderer.__init__
AutosummaryRenderer.__init__ = fixed_init
