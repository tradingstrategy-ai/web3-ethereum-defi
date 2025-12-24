"""A simple key-value persistent disk cache based on SQLite.

See :py:class:`PersistentKeyValueStore`
"""

import sqlite3
from pathlib import Path
from threading import get_ident
from typing import Any


class PersistentKeyValueStore(dict):
    """A simple key-value cache for sqlite3, honouring Python dictionary interface.

    Designed to cache
    - JSON blobs from integrated API services like TokenSniffer
    - Token metadata with :py:func:`~eth_defi.token.fetch_erc20_details`

    Based on https://stackoverflow.com/questions/47237807/use-sqlite-as-a-keyvalue-store

    - Disk cache can grow over time (supports append)
    - Cache keys must be strings
    - Cache values must be string-encodeable via :py:meth:`encode_value` and :py:meth:`decode_value` hooks
    - Can be used across threads
    """

    def __init__(self, filename: Path, autocommit=True):
        """
        :param filename: Path to the sqlite database

        :param autocommit: Whether to autocommit every time new entry is added to the database
        """
        super().__init__()
        self.autocommit = autocommit
        assert isinstance(filename, Path)
        self.filename = filename
        self.thread_connection_map = {}

    @property
    def conn(self) -> sqlite3.Connection:
        """One connection per thread"""
        thread_id = get_ident()
        if thread_id not in self.thread_connection_map:
            self.thread_connection_map[thread_id] = sqlite3.connect(self.filename)
            self.thread_connection_map[thread_id].execute("CREATE TABLE IF NOT EXISTS kv (key text unique, value text)")
        return self.thread_connection_map[thread_id]

    def encode_value(self, value: Any) -> str:
        """Hook to convert Python objects to cache format"""
        return value

    def decode_value(self, value: str) -> Any:
        """Hook to convert SQLite values to Python objects"""
        return value

    def close(self):
        self.conn.commit()
        self.conn.close()
        thread_id = get_ident()
        del self.thread_connection_map[thread_id]

    def commit(self):
        self.conn.commit()

    def iterkeys(self):
        c = self.conn.cursor()
        for row in c.execute("SELECT key FROM kv"):
            yield row[0]

    def itervalues(self):
        c = self.conn.cursor()
        for row in c.execute("SELECT value FROM kv"):
            yield row[0]

    def iteritems(self):
        c = self.conn.cursor()
        for row in c.execute("SELECT key, value FROM kv"):
            yield row[0], row[1]

    def keys(self):
        return list(self.iterkeys())

    def values(self):
        return list(self.itervalues())

    def items(self):
        return list(self.iteritems())

    def __contains__(self, key):
        return self.conn.execute("SELECT 1 FROM kv WHERE key = ?", (key,)).fetchone() is not None

    def __getitem__(self, key):
        assert type(key) == str, f"Only string keys allowed, got {key}"
        item = self.conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        if item is None:
            raise KeyError(key)
        return self.decode_value(item[0])

    def __setitem__(self, key, value):
        assert type(key) == str, f"Only string keys allowed, got {key}"
        value = self.encode_value(value)
        assert type(value) == str, f"Only string values allowed, got {value}"
        self.conn.execute("REPLACE INTO kv (key, value) VALUES (?,?)", (key, value))
        if self.autocommit:
            self.conn.commit()

    def __delitem__(self, key):
        if key not in self:
            raise KeyError(key)
        self.conn.execute("DELETE FROM kv WHERE key = ?", (key,))

    def __iter__(self):
        return self.iterkeys()

    def __len__(self):
        rows = self.conn.execute("SELECT COUNT(*) FROM kv").fetchone()[0]
        return rows if rows is not None else 0

    def get(self, key, default=None):
        if key in self:
            value = self[key]
            return value
        return default

    def purge(self):
        """Delete all keys and save."""
        keys = list(self.keys())
        for key in keys:
            del self[key]
        self.commit()

    def get_file_size(self) -> int:
        return self.filename.stat().st_size
