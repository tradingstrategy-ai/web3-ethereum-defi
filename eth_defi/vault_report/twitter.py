"""User-authenticated X writes for reviewed chart tweets.

Uses OAuth 1.0a user credentials, never an application bearer token. See
https://docs.x.com/x-api/posts/manage-tweets/introduction and
https://docs.x.com/x-api/media/upload-media. No ambiguous post is retried.
"""

import base64
import hashlib
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

import requests
from requests_oauthlib import OAuth1

logger = logging.getLogger(__name__)


class TwitterError(RuntimeError):
    """An X operation failed with a redacted error."""


class AmbiguousPostError(TwitterError):
    """A post may have succeeded; operator reconciliation is required."""


class TwitterRateLimit(TwitterError):
    """A known rejected request may wait until the provider reset."""

    def __init__(self, reset_at: float) -> None:
        """Retain only the provider's retry time.

        Request URLs, bodies and credentials never enter error messages.

        :param reset_at: UNIX reset timestamp.
        """
        self.reset_at = reset_at
        super().__init__("X rate limit: defer until the recorded reset time")


@dataclass(slots=True)
class UploadedMedia:
    """An expiring, ready-to-attach chart image."""

    #: X media identifier.
    id: str
    #: UNIX expiry time.
    expires_at: float


class TwitterWriter:
    """A small synchronous OAuth user API client with no automatic write retries."""

    def __init__(self, consumer_key: str, secret_key: str, access_token: str, access_token_secret: str) -> None:
        """Create a user-scoped signed session.

        All four supplied values are required; secrets remain in memory.

        :param consumer_key: OAuth application key.
        :param secret_key: OAuth application secret.
        :param access_token: User token.
        :param access_token_secret: User token secret.
        """
        self.session = requests.Session()
        self.session.auth = OAuth1(consumer_key, secret_key, access_token, access_token_secret)

    @classmethod
    def from_env(cls) -> "TwitterWriter":
        """Load the existing repository credential names.

        A missing key produces its name only, never its value.

        :return: User-authenticated writer.
        """
        names = ("TWITTER_CONSUMER_KEY", "TWITTER_SECRET_KEY", "TWITTER_ACCESS_TOKEN", "TWITTER_ACCESS_TOKEN_SECRET")
        missing = [name for name in names if not os.environ.get(name)]
        if missing:
            raise TwitterError("Missing user credentials: " + ", ".join(missing))
        return cls(*(os.environ[name] for name in names))

    def _request(self, method: str, endpoint: str, payload: dict | None = None, params: dict | None = None, creating_post: bool = False) -> dict:
        """Issue one signed request and classify failures without exposing secrets.

        Connection loss and server failures after a create request are ambiguous.
        The caller alone controls retries and durable posting state.

        :param method: HTTP method.
        :param endpoint: Fixed X API path.
        :param payload: Optional JSON body.
        :param params: Optional read query.
        :param creating_post: Treat unknown outcomes as potentially sent.
        :return: Parsed successful JSON.
        """
        try:
            response = self.session.request(method, "https://api.x.com" + endpoint, json=payload, params=params, timeout=(10, 45), allow_redirects=False)
        except requests.RequestException as exc:
            error = AmbiguousPostError if creating_post else TwitterError
            raise error(f"X {method} failed: {type(exc).__name__}") from None
        if response.status_code == 429:
            resets = []
            for name in ("x-rate-limit-reset", "x-user-limit-24hour-reset"):
                try:
                    if response.headers.get(name):
                        resets.append(float(response.headers[name]))
                except ValueError:
                    continue
            reset = max(resets) if resets else time.time() + 900
            raise TwitterRateLimit(max(reset, time.time() + 1))
        if response.status_code >= 500 and creating_post:
            raise AmbiguousPostError(f"X post returned HTTP {response.status_code}; reconcile before retrying")
        if not 200 <= response.status_code < 300:
            if creating_post and ("duplicate" in response.text.lower() or '"code":187' in response.text.replace(" ", "")):
                raise AmbiguousPostError("X reports duplicate content; reconcile the existing post")
            raise TwitterError(f"X {method} {endpoint} rejected: HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError:
            error = AmbiguousPostError if creating_post else TwitterError
            raise error("X returned invalid JSON") from None

    def fetch_identity(self) -> dict:
        """Fetch and enforce the configured publishing account.

        User identity is checked with OAuth user context before any media write.

        :return: Numeric id and username of TradingProtocol.
        """
        result = self._request("GET", "/2/users/me")["data"]
        if result["username"].lower() != "tradingprotocol":
            raise TwitterError("Credentials do not belong to tradingprotocol")
        return result

    def fetch_handles(self, handles: list[str]) -> dict[str, dict]:
        """Resolve a bounded batch of winner usernames.

        The caller compares stored numeric ids to detect reassignment.

        :param handles: Usernames without at signs.
        :return: Case-folded handle to returned user object.
        """
        if not handles:
            return {}
        result = self._request("GET", "/2/users/by", params={"usernames": ",".join(handles)})
        return {user["username"].lower(): user for user in result.get("data", [])}

    def upload_chart(self, path: Path, alt: str, expected_sha256: str | None = None) -> UploadedMedia:
        """Upload one chart and set its reviewed alt text without creating a post.

        A failed upload or metadata write never falls back to text-only posting.

        :param path: Frozen reviewed PNG or JPEG.
        :param alt: Reviewed alt text, at most 1,000 characters.
        :param expected_sha256: Approved hash checked against the actual upload bytes.
        :return: Ready media id with expiry.
        """
        data = path.read_bytes()
        if expected_sha256 and hashlib.sha256(data).hexdigest() != expected_sha256:
            raise TwitterError("Upload image differs from the approved attachment")
        if not 0 < len(alt) <= 1000 or len(data) > 5_000_000:
            raise TwitterError("Invalid alt text or image larger than 5 MB; prepare and review a variant")
        result = self._request("POST", "/2/media/upload", {"media": base64.b64encode(data).decode(), "media_category": "tweet_image"})["data"]
        if result.get("processing_info", {}).get("state") not in (None, "succeeded"):
            raise TwitterError("Chart media processing is not complete")
        media = UploadedMedia(str(result["id"]), time.time() + float(result["expires_after_secs"]))
        self._request("POST", "/2/media/metadata", {"id": media.id, "metadata": {"alt_text": {"text": alt}}})
        return media

    def create_post(self, body: str, media: UploadedMedia) -> str:
        """Create exactly one standalone reviewed image tweet.

        This method sends once. Its caller must record intent before dispatch.

        :param body: Exact manually reviewed body.
        :param media: Unexpired ready chart attachment.
        :return: Confirmed tweet id.
        """
        if media.expires_at <= time.time():
            raise TwitterError("Media expired before dispatch")
        result = self._request("POST", "/2/tweets", {"text": body, "media": {"media_ids": [media.id]}}, creating_post=True)
        if not result.get("data", {}).get("id"):
            raise AmbiguousPostError("No confirmed tweet id in successful response")
        return str(result["data"]["id"])

    def fetch_recent_posts(self, user_id: str) -> list[dict]:
        """Fetch a small timeline for manual duplicate reconciliation.

        A missing result is never interpreted as proof that a write failed.

        :param user_id: Publishing account id.
        :return: At most 100 recent posts with time and media references.
        """
        return self._request("GET", f"/2/users/{user_id}/tweets", params={"max_results": 100, "tweet.fields": "created_at,attachments"}).get("data", [])
