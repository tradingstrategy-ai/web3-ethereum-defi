"""Key value cache based on SQLite

"""

import sqlite3
from pathlib import Path
from typing import Any


class PersistentKeyValueStore(dict):
    """A simple key-value cache for sqlite3.

    Designed to cache JSON blobs from integrated API services like TokenSniffer.

    Based on https://stackoverflow.com/questions/47237807/use-sqlite-as-a-keyvalue-store

    - Disk cache can grow over time (supports append)
    - Cache keys must be strings
    - Cache values must be string-encodeable via :py:meth:`encode_value` and :py:meth:`decode_value` hooks
    """

    def __init__(self, filename: Path, autocommit=True):
        super().__init__()
        self.autocommit = autocommit
        assert isinstance(filename, Path)
        self.filename = filename
        try:
            self.conn = sqlite3.connect(filename)
        except Exception as e:
            raise RuntimeError(f"Sqlite3 connect failed: {filename}") from e
        self.conn.execute("CREATE TABLE IF NOT EXISTS kv (key text unique, value text)")

    def encode_value(self, value: Any) -> str:
        """Hook to convert Python objects to cache format"""
        return value

    def decode_value(self, value: str) -> Any:
        """Hook to convert SQLite values to Python objects"""
        return value

    def close(self):
        self.conn.commit()
        self.conn.close()

    def commit(self):
        self.conn.commit()

    def __len__(self):
        rows = self.conn.execute('SELECT COUNT(*) FROM kv').fetchone()[0]
        return rows if rows is not None else 0

    def iterkeys(self):
        c = self.conn.cursor()
        for row in c.execute('SELECT key FROM kv'):
            yield row[0]

    def itervalues(self):
        c = self.conn.cursor()
        for row in c.execute('SELECT value FROM kv'):
            yield row[0]

    def iteritems(self):
        c = self.conn.cursor()
        for row in c.execute('SELECT key, value FROM kv'):
            yield row[0], row[1]

    def keys(self):
        return list(self.iterkeys())

    def values(self):
        return list(self.itervalues())

    def items(self):
        return list(self.iteritems())

    def __contains__(self, key):
        return self.conn.execute('SELECT 1 FROM kv WHERE key = ?', (key,)).fetchone() is not None

    def __getitem__(self, key):
        assert type(key) == str, f"Only string keys allowed, got {key}"
        item = self.conn.execute('SELECT value FROM kv WHERE key = ?', (key,)).fetchone()
        if item is None:
            raise KeyError(key)
        return self.decode_value(item[0])

    def __setitem__(self, key, value):
        assert type(key) == str, f"Only string keys allowed, got {key}"
        value = self.encode_value(value)
        assert type(value) == str, f"Only string values allowed, got {value}"
        self.conn.execute('REPLACE INTO kv (key, value) VALUES (?,?)', (key, value))
        if self.autocommit:
            self.conn.commit()

    def __delitem__(self, key):
        if key not in self:
            raise KeyError(key)
        self.conn.execute('DELETE FROM kv WHERE key = ?', (key,))

    def __iter__(self):
        return self.iterkeys()

    def get(self, key, default=None):
        if key in self:
            value = self[key]
            return value
        return default
