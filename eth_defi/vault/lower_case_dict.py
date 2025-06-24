"""Ethereum address headache tools."""


class LowercaseDict(dict):
    """A dictionary subclass that automatically converts all string keys to lowercase.

    - Because of legacy, Ethrereum services mix loewrcased and checksum-case addresses

    - Ethereum checksum addresse where a f**king bad idea and everyone needs to suffer from
      this shitty idea for the eternity
    """

    def __init__(self, *args, **kwargs):
        super().__init__()
        # Handle initialization from dict or kwargs
        if args:
            if len(args) > 1:
                raise TypeError("expected at most 1 argument, got %d" % len(args))
            self.update(args[0])
        if kwargs:
            self.update(kwargs)

    def __setitem__(self, key, value):
        """Override setitem to convert string keys to lowercase."""
        key = key.lower()
        super().__setitem__(key, value)

    def __getitem__(self, key):
        """Override getitem to convert string keys to lowercase."""
        key = key.lower()
        return super().__getitem__(key)

    def get(self, key, default=None):
        """Override get method to convert string keys to lowercase."""
        key = key.lower()
        return super().get(key, default)

    def update(self, other=None, **kwargs):
        """Override update to convert string keys to lowercase."""
        if other is not None:
            for k, v in other.items() if isinstance(other, dict) else other:
                self[k] = v
        for k, v in kwargs.items():
            self[k] = v

    def setdefault(self, key, default=None):
        """Override setdefault to convert string keys to lowercase."""
        key = key.lower()
        return super().setdefault(key, default)
