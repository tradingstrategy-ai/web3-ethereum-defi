"""Trying to make Sphinx more useful."""

# Monkey-patch autosummary template context
from sphinx.ext.autosummary.generate import AutosummaryRenderer


def smart_fullname(fullname):
    parts = fullname.split(".")
    return ".".join(parts[1:])


def get_first_line(docstring: str | None) -> str:
    if not docstring:
        # __doc__ can be None
        return ""
    lines = docstring.split("\n")
    return lines[0]


def extract_module_docstring(mod_name) -> str:
    """See _templates/autosummary/base.rst"""
    import sys

    mod = sys.modules[mod_name]
    return get_first_line(getattr(mod, "__doc__", ""))


def extract_object_docstring(dotted_path: str) -> str:
    """See _templates/autosummary/base.rst"""
    from zope.dottedname.resolve import resolve

    obj = resolve(dotted_path)
    return get_first_line(getattr(obj, "__doc__", ""))


def partial_name(fullname):
    parts = fullname.split(".")
    return parts[-1]


def obj_path(fullname):
    parts = fullname.split(".")
    return ".".join(parts[0:-1])


# Patch autosummary internals to allow our tuned templates to access
# necessary Python functions
def fixed_init(self, app, template_dir=None):
    AutosummaryRenderer.__old_init__(self, app, template_dir)
    self.env.filters["smart_fullname"] = smart_fullname
    self.env.filters["extract_module_docstring"] = extract_module_docstring
    self.env.filters["extract_object_docstring"] = extract_object_docstring
    self.env.filters["partial_name"] = partial_name
    self.env.filters["obj_path"] = obj_path


AutosummaryRenderer.__old_init__ = AutosummaryRenderer.__init__
AutosummaryRenderer.__init__ = fixed_init


#
# Monkey patch meta generation.
# See _templates/autosummary.base.rst
#

from sphinx.addnodes import meta
from docutils import nodes
from docutils.io import StringOutput
from sphinx.util.osutil import relative_uri


def write_doc(self, docname: str, doctree: nodes.document) -> None:
    destination = StringOutput(encoding="utf-8")
    doctree.settings = self.docsettings

    self.secnumbers = self.env.toc_secnumbers.get(docname, {})
    self.fignumbers = self.env.toc_fignumbers.get(docname, {})
    self.imgpath = relative_uri(self.get_target_uri(docname), "_images")
    self.dlpath = relative_uri(self.get_target_uri(docname), "_downloads")
    self.current_docname = docname
    self.docwriter.write(doctree, destination)
    self.docwriter.assemble_parts()
    body = self.docwriter.parts["fragment"]
    metatags = self.docwriter.clean_meta

    ctx = self.get_doc_context(docname, body, metatags)

    # Pass the custom meta attributes in raw objects instead
    # of contatenad HTML soup
    class ExtractMeta(nodes.GenericNodeVisitor):
        def __init__(self, document):
            super().__init__(document)
            self.metas = {}

        def default_visit(self, node):
            if isinstance(node, meta):
                self.metas[node.attributes.get("name")] = node.rawcontent

        def default_departure(self, node):
            pass

    meta_extractor = ExtractMeta(doctree)
    doctree.walkabout(meta_extractor)

    ctx["metas"] = meta_extractor.metas

    self.handle_page(docname, ctx, event_arg=doctree)


from sphinx.builders.html import StandaloneHTMLBuilder

StandaloneHTMLBuilder.write_doc = write_doc
