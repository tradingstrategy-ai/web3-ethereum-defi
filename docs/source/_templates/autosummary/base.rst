{{ name | escape | underline}}

.. currentmodule:: {{ module }}

{% if objtype == "class" %}

Documentation for `{{ fullname }}` Python class.

.. autoclass:: {{ objname }}
   :members:

   {% block attributes %}
   {% if attributes %}
   .. rubric:: {{ _('Attributes summary') }}

   .. autosummary::
   {% for item in attributes %}
      ~{{ name }}.{{ item }}
   {%- endfor %}
   {% endif %}
   {% endblock %}

   {% block methods %}
   {% if methods %}
   .. rubric:: {{ _('Methods summary') }}

   .. autosummary::
   {% for item in methods %}
      ~{{ name }}.{{ item }}
   {%- endfor %}
   {% endif %}
   {% endblock %}

{% else %}

Documentation for `{{ fullname }}` {{ objtype }}.

.. auto{{ objtype }}:: {{ objname }}
{% endif %}


