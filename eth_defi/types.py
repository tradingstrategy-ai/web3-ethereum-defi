"""Common type aliases."""

from typing import TypeAlias

#: Percent number as 0...1
Percent: TypeAlias = float

#: ISO 8601 calendar date string, e.g. ``2026-06-26``.
ISODateString: TypeAlias = str

#: ISO 8601 datetime string in naive UTC, e.g. ``2026-06-26T12:00:00``.
ISODateTimeString: TypeAlias = str
