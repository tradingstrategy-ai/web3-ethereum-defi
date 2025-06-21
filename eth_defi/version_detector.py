import sys
from packaging import version
import web3


class Web3VersionInfo:
    """Version detection and compatibility flags"""

    _instance = None
    _initialized = False

    def __new__(cls):
        """Singleton pattern to ensure consistent version detection"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not self._initialized:
            self._detect_version()
            Web3VersionInfo._initialized = True

    def _detect_version(self):
        """Detect web3.py version and set compatibility flags"""
        self.raw_version = web3.__version__
        self.parsed_version = version.parse(self.raw_version)

        # Version flags
        self.is_v6 = version.parse("6.0.0") <= self.parsed_version < version.parse("7.0.0")
        self.is_v7_or_higher = self.parsed_version >= version.parse("7.0.0")
        self.is_v7 = version.parse("7.0.0") <= self.parsed_version < version.parse("8.0.0")
        self.is_v8_or_higher = self.parsed_version >= version.parse("8.0.0")

        # Detailed version info
        self.major = self.parsed_version.major
        self.minor = self.parsed_version.minor
        self.patch = self.parsed_version.micro

    @property
    def version_string(self):
        """Human readable version string"""
        if self.is_v6:
            return f"v6.x ({self.raw_version})"
        elif self.is_v7:
            return f"v7.x ({self.raw_version})"
        elif self.is_v8_or_higher:
            return f"v8+ ({self.raw_version})"
        else:
            return f"Unknown ({self.raw_version})"

    def requires_legacy_import(self, module_name):
        """Check if a specific module requires legacy import"""
        legacy_modules = {
            'eth_utils.abi': self.is_v6,
            'web3.contract': self.is_v6,
            # Add more modules as needed
        }
        return legacy_modules.get(module_name, False)

    def __str__(self):
        return f"Web3.py {self.version_string}"

    def __repr__(self):
        return f"Web3VersionInfo(version='{self.raw_version}', is_v7_or_higher={self.is_v7_or_higher})"


# Global instance
version_info = Web3VersionInfo()