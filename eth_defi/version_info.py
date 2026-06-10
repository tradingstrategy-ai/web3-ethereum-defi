"""Read Docker image git version stamp.

During the Docker image build, the git revision of the source tree is
written into ``GIT_VERSION_TAG.txt``, ``GIT_COMMIT_MESSAGE.txt`` and
``GIT_VERSION_HASH.txt`` files at the install root — see
``Dockerfile.vault-scanner``.  This module reads those files back at
runtime so long-running services can report which code revision they
are running, e.g. the vault scanner embedding its version in the
top-vaults JSON export.

Modelled on the trade-executor ``VersionInfo`` pattern, based on
https://stackoverflow.com/a/74694676/315168

Example::

    from eth_defi.version_info import VersionInfo

    version = VersionInfo.read_docker_version()
    print(version.commit_hash)  # None outside Docker
"""

import logging
from dataclasses import dataclass
from pathlib import Path


logger = logging.getLogger(__name__)


#: Install root where the Docker build writes the version stamp files.
#:
#: Resolves to the directory containing the ``eth_defi`` package,
#: e.g. ``/usr/src/web3-ethereum-defi`` inside the vault scanner image
#: or the repository checkout when running from source.
VERSION_FILE_ROOT: Path = Path(__file__).parent.parent

#: Sentinel written by the Docker build when a version ARG was not passed.
#:
#: ``Dockerfile.vault-scanner`` defaults each ``GIT_*`` build ARG to
#: ``unspecified``, so an image built without the args (e.g. a plain
#: ``docker compose build``) stamps this literal string into the files.
#: :py:meth:`VersionInfo.read_version_file` normalises it to ``None``.
UNSPECIFIED_SENTINEL = "unspecified"


@dataclass(slots=True, frozen=True)
class VersionInfo:
    """Reflect the git version information embedded in the Docker image during build.

    All fields are ``None`` when running outside a stamped Docker image,
    e.g. from a source checkout.  Individual fields can also be ``None``
    inside a stamped image when the corresponding build ARG was not
    passed — in particular :py:attr:`tag` is ``None`` for images built
    from an untagged commit.  See ``Dockerfile.vault-scanner`` for how
    the stamp files are written.
    """

    #: Git tag at build time, e.g. ``v0.30``.
    #:
    #: Often ``None``: only set when the image was built with the
    #: ``GIT_VERSION_TAG`` build ARG, which requires a tagged commit.
    #: Use :py:attr:`commit_hash` as the primary build identifier.
    tag: str | None = None

    #: The latest git commit message at build time.
    commit_message: str | None = None

    #: Git commit SHA hash at build time.
    commit_hash: str | None = None

    @staticmethod
    def read_version_file(name: str, root: Path = VERSION_FILE_ROOT) -> str | None:
        """Read one version stamp file written by the Docker build.

        :param name:
            Stamp file name, e.g. ``GIT_VERSION_HASH.txt``.

        :param root:
            Directory holding the stamp files.

        :return:
            Stripped file content, or ``None`` if the file does not
            exist, is empty, or contains the
            :py:data:`UNSPECIFIED_SENTINEL` placeholder written when the
            build ARG was not passed.
        """
        path = root / name
        if path.exists():
            value = path.read_text().strip()
            if not value or value == UNSPECIFIED_SENTINEL:
                return None
            return value
        return None

    @classmethod
    def read_docker_version(cls, root: Path = VERSION_FILE_ROOT) -> "VersionInfo":
        """Read version information burnt within the Docker file system during image build.

        :param root:
            Directory holding the stamp files.  Defaults to the
            ``eth_defi`` install root.

        :return:
            Populated version info, or ``None`` for every field when the
            stamp files are absent (e.g. running from a source checkout).
        """
        return cls(
            tag=cls.read_version_file("GIT_VERSION_TAG.txt", root),
            commit_message=cls.read_version_file("GIT_COMMIT_MESSAGE.txt", root),
            commit_hash=cls.read_version_file("GIT_VERSION_HASH.txt", root),
        )

    def as_dict(self) -> dict[str, str | None]:
        """Return a JSON-serialisable dict for embedding in data exports.

        :return:
            Dict with ``tag``, ``commit_message`` and ``commit_hash`` keys.
        """
        return {
            "tag": self.tag,
            "commit_message": self.commit_message,
            "commit_hash": self.commit_hash,
        }
