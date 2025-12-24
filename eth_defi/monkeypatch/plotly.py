"""Monkey-patch Plotly 6.x bug: FigureWidget showing nanoseconds instead of dates.

- Workaround bug https://github.com/plotly/plotly.py/issues/5210
"""

import warnings
from importlib.metadata import version, PackageNotFoundError

from packaging.version import Version


try:
    pkg_version = version("plotly")
except PackageNotFoundError:
    pkg_version = None


if (pkg_version is not None) and Version(pkg_version) <= Version("7.0.0"):
    import numpy
    import pandas
    from plotly.graph_objs import Figure

    def fix_trace_x_axis_dates(self: Figure):
        for trace in self.data:
            if not (hasattr(trace, "x") and len(trace.x) > 0 and isinstance(trace.x, numpy.ndarray)):
                continue

            # Detect datetime64 and convert to native Python datetime so it's formatted correctly
            if isinstance(trace.x[0], numpy.datetime64):
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", FutureWarning)
                    trace.x = pandas.Series(trace.x).dt.to_pydatetime().tolist()

    # Apply the monkey patch to to_dict() method to fix traces during serialization
    # This ensures the fix works for both show() (notebooks) and to_image() (web renderer)
    _old_to_dict = Figure.to_dict

    def _new_to_dict(self: Figure, *args, **kwargs):
        fix_trace_x_axis_dates(self)
        return _old_to_dict(self, *args, **kwargs)

    Figure.to_dict = _new_to_dict
