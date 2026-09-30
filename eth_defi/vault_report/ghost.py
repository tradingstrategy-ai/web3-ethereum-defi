"""Minimal Ghost blog API client for the vault report.

- The `Content API <https://ghost.org/docs/content-api/>`__ is read-only and
  uses a content API key. We use it to find the previous report post.
- The `Admin API <https://ghost.org/docs/admin-api/>`__ is needed to upload
  images and create draft posts. It uses an ``{id}:{secret}`` admin API key,
  created in Ghost Admin under *Settings → Integrations → Add custom integration*,
  and short-lived JWT tokens signed with the secret, see
  `token authentication <https://ghost.org/docs/admin-api/#token-authentication>`__.
"""

import base64
import datetime
import hashlib
import hmac
import json
import logging
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path

import requests

from eth_defi.compat import native_datetime_utc_now
from eth_defi.utils import to_unix_timestamp

logger = logging.getLogger(__name__)

#: Ghost API version header value
GHOST_ACCEPT_VERSION = "v5.0"

#: Public blog base URL; posts live at ``{BLOG_URL}/{slug}``. The Ghost API returns ``ghost.io`` URLs; readers use this domain.
BLOG_URL = "https://tradingstrategy.ai/blog"

#: Ghost adds this tracking parameter to links in its HTML output
GHOST_REF_PARAMETER = re.compile(r"([?&])ref=[a-z0-9.-]+\.ghost\.io(&?)")


def strip_ghost_ref(url: str) -> str:
    """Remove the ``ref`` tracking parameter Ghost adds to links.

    :param url:
        Link from Ghost post HTML, e.g. ``https://youtu.be/abc?ref=example.ghost.io``.

    :return:
        The link without the parameter, e.g. ``https://youtu.be/abc``.
    """
    # Keep the separator when other query parameters follow: ?ref=x&a=1 -> ?a=1
    return GHOST_REF_PARAMETER.sub(lambda m: m.group(1) if m.group(2) else "", url)


#: Admin API JWT lifetime; Ghost allows at most five minutes
ADMIN_TOKEN_LIFETIME = datetime.timedelta(minutes=5)


class GhostAPIError(Exception):
    """Ghost API returned an error response."""


def _raise_for_ghost_error(resp: requests.Response, action: str) -> None:
    """Raise :py:class:`GhostAPIError` with Ghost's error message for a failed response.

    The message does not include the request URL, which may contain the Content API key.

    :param resp:
        Ghost API response.

    :param action:
        Human-readable description of the request, for the error message.
    """
    if resp.status_code >= 400:
        try:
            errors = resp.json().get("errors", [])
            message = "; ".join(f"{e.get('message')} {e.get('context') or ''}".strip() for e in errors)
        except ValueError:
            message = resp.text[:500]
        raise GhostAPIError(f"Ghost API {action} failed: HTTP {resp.status_code}: {message}")


#: Admin API key or staff access token: a 24-character hex id and a 64-character hex secret
ADMIN_API_KEY_FORMAT = re.compile(r"^[0-9a-f]{24}:[0-9a-f]{64}$")

#: Content API key: 26 hex characters, read-only
CONTENT_API_KEY_FORMAT = re.compile(r"^[0-9a-f]{26}$")

#: How to get a key that can create draft posts
ADMIN_API_KEY_HELP = "Creating a draft post needs the Ghost Admin API. In Ghost Admin, open Settings -> Integrations -> Add custom integration and copy its Admin API key, or copy the Staff access token from your staff user profile. Put it in your secrets file, outside the repository, as GHOST_ADMIN_API_KEY."


def check_ghost_admin_api_key(admin_api_key: str) -> None:
    """Check that a key can authenticate to the Ghost Admin API.

    The `Content API <https://ghost.org/docs/content-api/>`__ is read-only,
    so its key cannot create posts, drafts included. The
    `Admin API <https://ghost.org/docs/admin-api/#authentication>`__ takes an
    integration's Admin API key or a staff access token, both ``{id}:{secret}``.
    The key itself is never included in the error.

    :param admin_api_key:
        Key to check.

    :raise GhostAPIError:
        The key is a Content API key or not in ``{id}:{secret}`` format.
    """
    key = admin_api_key.strip()
    if CONTENT_API_KEY_FORMAT.match(key):
        raise GhostAPIError(f"GHOST_ADMIN_API_KEY is a Content API key, which is read-only and cannot create posts. {ADMIN_API_KEY_HELP}")
    if not ADMIN_API_KEY_FORMAT.match(key):
        raise GhostAPIError(f"GHOST_ADMIN_API_KEY is not an Admin API key in {{id}}:{{secret}} format. {ADMIN_API_KEY_HELP}")


def _base64url(data: bytes) -> str:
    """Encode bytes as unpadded base64url, as used in JWTs."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def create_ghost_admin_token(admin_api_key: str, now: datetime.datetime | None = None) -> str:
    """Create a short-lived Admin API JWT token.

    Implements `Ghost token authentication <https://ghost.org/docs/admin-api/#token-authentication>`__:
    HS256 signature with the hex-decoded secret, the key id in the ``kid``
    header and ``/admin/`` audience.

    :param admin_api_key:
        Admin API key in ``{id}:{secret}`` format.

    :param now:
        Token issue time, naive UTC. Defaults to the current time.

    :return:
        Encoded JWT.
    """
    assert ":" in admin_api_key, "Ghost Admin API key must be in {id}:{secret} format. A Content API key cannot create posts."
    key_id, secret = admin_api_key.split(":", 1)
    if now is None:
        now = native_datetime_utc_now()
    iat = int(to_unix_timestamp(now))
    header = {"alg": "HS256", "typ": "JWT", "kid": key_id}
    payload = {"iat": iat, "exp": iat + int(ADMIN_TOKEN_LIFETIME.total_seconds()), "aud": "/admin/"}
    signing_input = _base64url(json.dumps(header, separators=(",", ":")).encode()) + "." + _base64url(json.dumps(payload, separators=(",", ":")).encode())
    signature = hmac.new(bytes.fromhex(secret), signing_input.encode("ascii"), hashlib.sha256).digest()
    return signing_input + "." + _base64url(signature)


@dataclass(slots=True)
class GhostPost:
    """Subset of Ghost post fields we use."""

    #: Ghost object id
    id: str

    #: Post title
    title: str

    #: URL slug
    slug: str

    #: ``draft``, ``scheduled`` or ``published``
    status: str

    #: Post body, when requested with ``formats=html``
    html: str | None

    #: Publication time, naive UTC
    published_at: datetime.datetime | None

    #: Last update timestamp as returned by Ghost, needed for updates
    updated_at: str | None

    @staticmethod
    def from_api(data: dict) -> "GhostPost":
        """Parse a post from a Ghost API response.

        :param data:
            One entry of the ``posts`` list.

        :return:
            Parsed post.
        """
        published_at = data.get("published_at")
        if published_at:
            published_at = datetime.datetime.fromisoformat(published_at.replace("Z", "+00:00")).astimezone(datetime.UTC).replace(tzinfo=None)
        return GhostPost(
            id=data["id"],
            title=data.get("title", ""),
            slug=data.get("slug", ""),
            status=data.get("status", "published"),
            html=data.get("html"),
            published_at=published_at,
            updated_at=data.get("updated_at"),
        )


class GhostContentClient:
    """Read published posts with the Ghost Content API."""

    def __init__(self, api_url: str, content_api_key: str, timeout: float = 30.0) -> None:
        """
        :param api_url:
            Ghost site API URL, e.g. ``https://example.ghost.io``.

        :param content_api_key:
            Content API key.

        :param timeout:
            HTTP timeout in seconds.
        """
        self.api_url = api_url.rstrip("/")
        self.content_api_key = content_api_key
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["Accept-Version"] = GHOST_ACCEPT_VERSION

    def fetch_latest_post_by_slug_prefix(self, slug_prefix: str) -> GhostPost | None:
        """Find the most recently published post whose slug starts with a prefix.

        Uses a `NQL filter <https://ghost.org/docs/content-api/#filtering>`__
        ``slug:~^'prefix'``.

        :param slug_prefix:
            E.g. ``the-best-performing-stablecoin-vaults``.

        :return:
            The latest post including its HTML body, or ``None``.
        """
        posts = self.fetch_latest_posts(f"slug:~^'{slug_prefix}'", limit=1)
        return posts[0] if posts else None

    def fetch_latest_posts(self, nql_filter: str, limit: int) -> list[GhostPost]:
        """Fetch the most recently published posts that match a filter.

        See the `Content API filtering documentation <https://ghost.org/docs/content-api/#filtering>`__
        for the NQL syntax.

        :param nql_filter:
            NQL filter, e.g. ``title:~'episode'`` for titles containing a word.

        :param limit:
            Maximum number of posts.

        :return:
            Posts including their HTML bodies, newest first.
        """
        params = {"key": self.content_api_key, "filter": nql_filter, "limit": str(limit), "order": "published_at desc", "formats": "html"}
        try:
            resp = self.session.get(f"{self.api_url}/ghost/api/content/posts/", params=params, timeout=self.timeout)
        except requests.RequestException as e:
            # Connection error messages contain the full URL with the key query parameter
            raise GhostAPIError(f"Ghost API fetch posts failed: {type(e).__name__}") from None
        _raise_for_ghost_error(resp, "fetch posts")
        return [GhostPost.from_api(post) for post in resp.json()["posts"]]


class GhostAdminClient:
    """Upload images and manage draft posts with the Ghost Admin API."""

    def __init__(self, api_url: str, admin_api_key: str, timeout: float = 60.0) -> None:
        """Create an Admin API client after checking the key format.

        The key is validated before any request, so a Content API key or a
        malformed key fails early without being sent or printed.

        :param api_url:
            Ghost site API URL, e.g. ``https://example.ghost.io``.

        :param admin_api_key:
            Admin API key or staff access token in ``{id}:{secret}`` format,
            see :py:func:`check_ghost_admin_api_key`.

        :param timeout:
            HTTP timeout in seconds.

        :raise GhostAPIError:
            The key cannot authenticate to the Admin API.
        """
        check_ghost_admin_api_key(admin_api_key)
        self.api_url = api_url.rstrip("/")
        self.admin_api_key = admin_api_key.strip()
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["Accept-Version"] = GHOST_ACCEPT_VERSION

    def _headers(self) -> dict:
        """Create request headers with a fresh Admin API token."""
        return {"Authorization": f"Ghost {create_ghost_admin_token(self.admin_api_key)}"}

    def _url(self, path: str) -> str:
        """Create an Admin API endpoint URL."""
        return f"{self.api_url}/ghost/api/admin/{path}"

    def upload_image(self, path: Path) -> str:
        """Upload an image to the Ghost media library.

        :param path:
            Local image file.

        :return:
            Public URL of the uploaded image.
        """
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        with open(path, "rb") as inp:
            resp = self.session.post(
                self._url("images/upload/"),
                headers=self._headers(),
                files={"file": (path.name, inp, content_type)},
                data={"purpose": "image", "ref": path.name},
                timeout=self.timeout,
            )
        _raise_for_ghost_error(resp, f"upload image {path.name}")
        url = resp.json()["images"][0]["url"]
        logger.info("Uploaded %s to %s", path.name, url)
        return url

    def fetch_post_by_slug(self, slug: str) -> GhostPost | None:
        """Fetch a post in any status by its slug.

        :param slug:
            Post slug.

        :return:
            The post, or ``None`` if it does not exist.
        """
        resp = self.session.get(self._url(f"posts/slug/{slug}/"), headers=self._headers(), params={"formats": "html"}, timeout=self.timeout)
        if resp.status_code == 404:
            return None
        _raise_for_ghost_error(resp, f"fetch post {slug}")
        return GhostPost.from_api(resp.json()["posts"][0])

    def delete_post(self, post_id: str) -> None:
        """Delete a post.

        :param post_id:
            Ghost post id.
        """
        resp = self.session.delete(self._url(f"posts/{post_id}/"), headers=self._headers(), timeout=self.timeout)
        _raise_for_ghost_error(resp, f"delete post {post_id}")

    def fetch_writable_draft(self, slug: str, *, overwrite_draft: bool = False) -> GhostPost | None:
        """Fetch the existing post for a slug and check a draft can be written there without losing work.

        :param slug:
            Post slug.

        :param overwrite_draft:
            Allow replacing an existing draft.

        :return:
            The existing draft to replace, or ``None`` if the slug is free.

        :raise GhostAPIError:
            A post with the slug has been published or scheduled, or a draft
            exists and ``overwrite_draft`` is not set.
        """
        existing = self.fetch_post_by_slug(slug)
        if existing is None:
            return None
        if existing.status != "draft":
            raise GhostAPIError(f"Post {slug} already exists with status {existing.status}; refusing to overwrite it")
        if not overwrite_draft:
            raise GhostAPIError(f"Draft {slug} already exists; refusing to overwrite possible manual edits. Delete the draft or enable overwriting.")
        return existing

    def create_or_update_draft(
        self,
        title: str,
        slug: str,
        html: str,
        custom_excerpt: str | None = None,
        tags: list[str] | None = None,
        *,
        overwrite_draft: bool = False,
    ) -> GhostPost:
        """Create a draft post, or replace the body of an existing draft with the same slug.

        See :py:meth:`fetch_writable_draft` for when an existing post is replaced.

        The body is sent with ``?source=html`` and Ghost converts it to its
        editor format. Content wrapped in ``<!--kg-card-begin: html-->`` /
        ``<!--kg-card-end: html-->`` comments becomes a raw HTML card; the
        previous report posts store their tables the same way.

        :param title:
            Post title.

        :param slug:
            Post URL slug.

        :param html:
            Post body.

        :param custom_excerpt:
            Post excerpt.

        :param tags:
            Tag names.

        :param overwrite_draft:
            Replace the body of an existing draft with the same slug.

        :return:
            The created or updated draft.
        """
        post_data = {"title": title, "slug": slug, "html": html, "status": "draft"}
        if custom_excerpt:
            post_data["custom_excerpt"] = custom_excerpt
        if tags:
            post_data["tags"] = [{"name": t} for t in tags]
        # The feature image is the editor's choice: it is never sent, so replacing a draft keeps it

        existing = self.fetch_writable_draft(slug, overwrite_draft=overwrite_draft)
        if existing is None:
            resp = self.session.post(self._url("posts/"), headers=self._headers(), params={"source": "html"}, json={"posts": [post_data]}, timeout=self.timeout)
            _raise_for_ghost_error(resp, f"create draft {slug}")
            logger.info("Created draft post %s", slug)
        else:
            post_data["updated_at"] = existing.updated_at
            resp = self.session.put(self._url(f"posts/{existing.id}/"), headers=self._headers(), params={"source": "html"}, json={"posts": [post_data]}, timeout=self.timeout)
            _raise_for_ghost_error(resp, f"update draft {slug}")
            logger.info("Updated existing draft post %s", slug)
        return GhostPost.from_api(resp.json()["posts"][0])

    def get_editor_url(self, post: GhostPost) -> str:
        """Get the Ghost Admin editor link for a post.

        :param post:
            Post.

        :return:
            Editor URL.
        """
        return f"{self.api_url}/ghost/#/editor/post/{post.id}"
