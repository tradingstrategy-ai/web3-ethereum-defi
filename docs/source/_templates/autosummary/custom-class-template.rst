..
    We use short name as title, because it's part of TOC tree on the left hand nav.
    Then we fix the HTML title in the templating.
    CUSTOM TEMPLATE MARKER - DELETE ME

{{ name | escape | underline}}

.. currentmodule:: {{ module }}

.. meta::
    :description: {{ fullname|extract_object_docstring }}
    :title: {{ name }} {{ objtype }} in {{ fullname|obj_path }}




Documentation for `{{ fullname }}` Python class.

.. autoclass:: {{ objname }}
   :members:
   :noindex:

   {% block attributes %}
   {% if attributes %}
   .. rubric:: Attributes summary

   .. autosummary::
   {% for item in attributes %}
   {# Filter out inherited dataclass fields that cannot be imported as class attributes #}
   {% if item != 'block_map' %}
      ~{{ name }}.{{ item }}
   {%- endif %}
   {%- endfor %}
   {% endif %}
   {% endblock %}

   {% block methods %}
   {% if methods %}
   .. rubric:: Methods summary

   .. autosummary::
   {% for item in methods %}
      ~{{ name }}.{{ item }}
   {%- endfor %}
   {% endif %}
   {% endblock %}


