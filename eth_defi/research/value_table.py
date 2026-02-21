"""Format key value tables."""

import pandas as pd

import numpy as np


def format_series_as_multi_column_grid(
    series: pd.Series,
    n_cols=3,
) -> pd.DataFrame:
    """Display"""
    assert isinstance(series, pd.Series)
    df = series

    # Reshape into a grid (e.g., 2x4 or 3x3)
    n_rows = len(df) // n_cols + (len(df) % n_cols > 0)

    # Pad with empty values if needed
    padded_data = list(df.items()) + [("", "")] * (n_rows * n_cols - len(df))

    # Reshape into grid
    grid_data = np.array(padded_data).reshape(n_rows, n_cols, 2)

    # Create DataFrame
    columns = []
    for i in range(n_cols):
        columns.extend([f"Metric", f"Value"])

    grid_df = pd.DataFrame(grid_data.reshape(n_rows, n_cols * 2), columns=columns)

    # Apply styling with strong borders between pairs
    def add_pair_borders(styler):
        # Create CSS styles for strong right borders on value columns
        styles = []
        for i in range(n_cols - 1):  # Don't add border after last pair
            value_col_idx = i * 2 + 1  # Value columns are at indices 1, 3, 5, etc.
            styles.append({"selector": f"td:nth-child({value_col_idx + 1})", "props": [("border-right", "3px solid black")]})

        styler.set_table_styles(styles)
        return styler

    styled_df = grid_df.style.pipe(add_pair_borders).hide(axis="index")

    return styled_df


def format_grouped_series_as_multi_column_grid(
    groups: list[tuple[str, pd.Series]],
    n_cols: int = 2,
    multi_col_threshold: int = 5,
    metric_col_width: int = 160,
    value_col_width: int = 100,
) -> str:
    """Render grouped key-value data as multi-column tables with bold headings.

    Each group becomes its own ``Metric | Value`` grid section preceded
    by a bold heading row spanning all columns. Column headers are hidden,
    values are right-aligned, and vertical borders separate column pairs.

    All columns use fixed pixel widths so that single-column and
    multi-column sections align consistently.

    Sections with ``multi_col_threshold`` or fewer items are rendered
    as a single column (one Metric/Value pair per row). Larger sections
    use ``n_cols`` columns.

    :param groups:
        List of ``(heading, series)`` tuples. Each heading is rendered
        as a bold row above the corresponding grid section.
    :param n_cols:
        Number of metric/value pairs per row for large sections.
    :param multi_col_threshold:
        Sections with more items than this use ``n_cols`` columns;
        smaller sections use a single column.
    :param metric_col_width:
        Fixed width in pixels for metric name columns.
    :param value_col_width:
        Fixed width in pixels for value columns.
    :return:
        HTML string containing all sections.
    """
    html_parts = []
    total_width = n_cols * (metric_col_width + value_col_width)

    for group_idx, (heading, series) in enumerate(groups):
        if len(series) == 0:
            continue

        # Use single column for small sections, n_cols for large ones
        effective_cols = n_cols if len(series) > multi_col_threshold else 1

        # Build the grid for this group
        n_rows = len(series) // effective_cols + (len(series) % effective_cols > 0)
        padded_data = list(series.items()) + [("", "")] * (n_rows * effective_cols - len(series))
        grid_data = np.array(padded_data).reshape(n_rows, effective_cols, 2)

        columns = []
        for i in range(effective_cols):
            columns.extend(["Metric", "Value"])

        grid_df = pd.DataFrame(grid_data.reshape(n_rows, effective_cols * 2), columns=columns)

        # Hide column headers (already have section heading) and index
        styler = grid_df.style.hide(axis="index").hide(axis="columns")
        table_id = f"T_{styler.uuid}"

        # Build CSS scoped to this table's ID so rules don't leak
        # across sections. Use the same per-column widths regardless
        # of effective_cols so that columns align consistently.
        section_width = effective_cols * (metric_col_width + value_col_width)
        cell_css = []
        cell_css.append(f"#{table_id} {{ table-layout: fixed; width: {section_width}px; }}")
        for i in range(effective_cols):
            metric_col_idx = i * 2
            value_col_idx = i * 2 + 1
            cell_css.append(f"#{table_id} td:nth-child({metric_col_idx + 1}) {{ width: {metric_col_width}px; }}")
            cell_css.append(f"#{table_id} td:nth-child({value_col_idx + 1}) {{ width: {value_col_width}px; text-align: right; }}")
        if effective_cols == 1:
            cell_css.append(f"#{table_id} td:nth-child(2) {{ border-right: none !important; }}")
        else:
            for i in range(effective_cols - 1):
                value_col_idx = i * 2 + 1
                cell_css.append(f"#{table_id} td:nth-child({value_col_idx + 1}) {{ border-right: 2px solid #999; }}")

        table_html = styler.to_html()

        # Inject scoped CSS into the table's <style> block
        css_rules = "\n".join(cell_css)
        table_html = table_html.replace("</style>", f"{css_rules}\n</style>")

        # Heading row: bold, underlined, with top margin (except first)
        top_margin = "margin-top: 12px;" if group_idx > 0 else ""
        heading_html = (
            f'<div style="font-weight: bold; font-size: 1.1em; '
            f'width: {total_width}px; '
            f'padding: 4px 0 2px 0; border-bottom: 2px solid #333; {top_margin}">'
            f"{heading}</div>"
        )

        html_parts.append(heading_html)
        html_parts.append(table_html)

    return "\n".join(html_parts)
