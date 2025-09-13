"""Jupyter notebook set up and formatting utilities."""

import enum
import logging
import sys

import matplotlib_inline
import pandas as pd
import plotly.io as pio


class OutputMode(enum.Enum):
    """What is the output mode for the notebook visualisations.

    Interactive visualisations work only on the HTML pages
    that are able to load Plotly.js JavaScripts.

    For examples see :py:func:`setup_charting_and_output`.
    """

    #: Output charts as static images
    static = "static"

    #: Output charts as interactive Plotly.js visualisations
    interactive = "interactive"


def setup_charting_and_output(
    mode: OutputMode = OutputMode.interactive,
    image_format="svg",
    max_rows=1000,
    width=1500,
    height=1500,
    increase_font_size=False,
):
    """Sets charting and other output options for Jupyter Notebooks.

    Interactive charts are better for local development, but are not compatible with most web-based notebook viewers.

    - `Set Quantstats chart to SVG output and for high-resolution screens <https://stackoverflow.com/questions/74721731/how-to-generate-svg-images-using-python-quantstat-library>`__

    - Mute common warnings like `Matplotlib font loading <https://stackoverflow.com/questions/42097053/matplotlib-cannot-find-basic-fonts/76136516#76136516>`__

    - `Plotly discussion <https://github.com/plotly/plotly.py/issues/931>`__

    Example how to set up default interactive output settings. Add early of your notebook do:

    .. code-block:: python

        # Set Jupyter Notebook output mode parameters.
        # For example, table max output rows is lifted from 20 to unlimited.
        from tradeexecutor.utils.notebook import setup_charting_and_output

        setup_charting_and_output()

    Example how to set up static image rendering:

        # Set charts to static image output, 1500 x 1000 pixels
        from tradeexecutor.utils.notebook import setup_charting_and_output, OutputMode
        setup_charting_and_output(OutputMode.static, image_format="png", width=1500, height=1000)

    :param mode:
        What kind of viewing context we have for this notebook output

    :param image_format:
        Do we do SVG or PNG.

        SVG is better, but Github inline viewer cannot display it in the notebooks.

    :param max_rows:
        Do we remove the ``max_rows`` limitation from Pandas tables.

        Default 20 is too low to display summary tables.

    :param increase_font_size:
        Make charts and tables more readable with larger fonts
    """

    import plotly.io as pio

    # Apply Plotly bug fixes
    import eth_defi.monkeypatch.plotly
    from plotly.offline import init_notebook_mode

    # Get rid of findfont: Font family 'Arial' not found.
    # when running a remote notebook on Jupyter Server on Ubuntu Linux server
    # https://stackoverflow.com/questions/42097053/matplotlib-cannot-find-basic-fonts/76136516#76136516
    logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)

    # Render charts from quantstats in high resolution
    # https://stackoverflow.com/questions/74721731/how-to-generate-svg-images-using-python-quantstat-library
    matplotlib_inline.backend_inline.set_matplotlib_formats(image_format)

    # Set Plotly to offline (static image mode)
    if mode == OutputMode.static:
        # https://stackoverflow.com/a/52956402/315168
        init_notebook_mode()

        # https://stackoverflow.com/a/74609837/315168
        pio.kaleido.scope.default_format = image_format

        # Kaleido 1.0

        # https://plotly.com/python/renderers/#overriding-the-default-renderer
        pio.renderers.default = image_format

        current_renderer = pio.renderers[image_format]
        # Have SVGs default pixel with
        current_renderer.width = width
        current_renderer.height = height
    elif mode == OutputMode.interactive:
        # https://plotly.com/python/renderers/#setting-the-default-renderer
        pio.renderers.default = "notebook_connected"
        init_notebook_mode(connected=False)
    else:
        raise NotImplementedError(f"Unknown rendering mode: {mode}")

    # TODO: Currently we do not reset interactive mode if the notebook has been run once
    # If you run setup_charting_and_output(offline) once you are stuck offline

    if max_rows:
        pd.set_option("display.max_rows", max_rows)


def set_large_plotly_chart_font(
    title_font_size=30,
    font_size=24,
    legend_font_size=24,
    line_width=3,
    axis_title_font_size=24,
    base_template="plotly",
):
    """Increase the default Plotly chart font sizes so that charts are readable on other mediums like mobile and PowerPoint.

    Usage:

    .. code-block:: python

        from tradeexecutor.utils.notebook import set_large_plotly_chart_font

        set_large_plotly_chart_font()

    """

    # Update the default template
    pio.templates["custom"] = pio.templates[base_template]
    pio.templates["custom"]["layout"]["font"]["size"] = font_size  # Set the default font size
    pio.templates["custom"]["layout"]["legend"]["font"]["size"] = legend_font_size  # Set the legend font size
    pio.templates["custom"]["layout"]["legend"]["font"]["size"] = legend_font_size  # Set the legend font size
    pio.templates["custom"]["layout"]["xaxis"]["title"]["font"]["size"] = font_size  # Set the x-axis title font size
    pio.templates["custom"]["layout"]["yaxis"]["title"]["font"]["size"] = font_size  # Set the y-axis title font size
    pio.templates["custom"]["layout"]["xaxis"]["tickfont"]["size"] = font_size  # Set the x-axis tick font size
    pio.templates["custom"]["layout"]["yaxis"]["tickfont"]["size"] = font_size  # Set the y-axis tick font size

    pio.templates["custom"]["layout"]["xaxis"]["title"]["font"]["size"] = axis_title_font_size  # Set the x-axis title font size
    pio.templates["custom"]["layout"]["yaxis"]["title"]["font"]["size"] = axis_title_font_size  # Set the y-axis title font size

    # Set the default title font size
    pio.templates["custom"]["layout"]["title"] = {"font": {"size": title_font_size}}

    # Set the default line width
    pio.templates["custom"]["data"]["scatter"] = [
        {
            "type": "scatter",
            "mode": "lines",
            "line": {"width": line_width},  # Set the default line width for scatter plots
        }
    ]

    # Set the default template to the custom template
    pio.templates.default = "custom"


def set_notebook_logging(log_level: int | str = logging.INFO):
    """Enable logging in notebooks.

    - Only needed to diagnose Client library bugs when running in notebook
    """

    if type(log_level) == str:
        log_level = getattr(logging, log_level.upper())

    format = "[%(asctime)s] %(levelname)s %(module)s: %(message)s"
    logging.basicConfig(
        level=log_level,
        format=format,
        datefmt="%H:%M:%S",
        stream=sys.stdout,
        force=True,  # Force to override any previous logging configuration
    )
