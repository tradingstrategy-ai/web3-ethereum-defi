"""Latest Trading Strategy podcast episodes for the monthly vault report.

The `Trading Strategy podcast <https://tradingstrategy.ai/podcast>`__ publishes
each episode as a blog post on Ghost. The website lists every post whose title
contains "episode" (``src/routes/podcast/+page.ts`` in the frontend), and this
module uses the same rule through the
`Ghost Content API <https://ghost.org/docs/content-api/>`__.

Each episode post follows the same template, which this module reads:

- the first paragraph is the promotion text, e.g. "Listen to our latest
  episode featuring Yearn and learn why ...";
- it links to the episode on Spotify (``open.spotify.com/episode/...``) and
  YouTube (``youtu.be/...`` or ``youtube.com/watch?v=...``);
- it links to the guest's page on the website, e.g.
  ``tradingstrategy.ai/vaults/curators/yearn`` or
  ``tradingstrategy.ai/vaults/protocols/frankencoin``, whose slug names the
  guest's logo in ``eth_defi/data/vaults/formatted_logos``.
"""

import datetime
import html
import logging
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from eth_defi.vault_report.ghost import BLOG_URL, GhostContentClient, GhostPost

logger = logging.getLogger(__name__)

#: The podcast index page on the website
PODCAST_PAGE_URL = "https://tradingstrategy.ai/podcast"


#: Ghost NQL filter matching the website's podcast rule: the title contains "episode", case-insensitively
PODCAST_FILTER = "title:~'episode'"

#: Number of episodes in the report
LATEST_PODCAST_COUNT = 4

#: Link label of each listening service, by its icon name in ``assets/podcast``
PODCAST_SERVICES = {"youtube": "Watch on YouTube", "spotify": "Listen on Spotify"}


def logo_image_key(slug: str) -> str:
    """Key of a guest logo in the podcast image mapping, see :py:func:`render_podcast_episodes`."""
    return f"logo:{slug}"


def icon_image_key(service: str) -> str:
    """Key of a listening service icon in the podcast image mapping, e.g. ``icon:youtube``."""
    return f"icon:{service}"


#: Spotify episode link
SPOTIFY_LINK = re.compile(r'href="(https://open\.spotify\.com/episode/[^"]+)"')

#: YouTube watch link; embeds are skipped because they only play inside a page
YOUTUBE_LINK = re.compile(r'href="(https://(?:youtu\.be/[^"]+|(?:www\.)?youtube\.com/watch\?[^"]+))"')

#: Link to the guest's curator or protocol page on the website
GUEST_PAGE_LINK = re.compile(r'href="https://tradingstrategy\.ai/(?:trading-view/)?vaults/(?:curators|protocols)/([a-z0-9-]+)')


@dataclass(slots=True, frozen=True)
class PodcastEpisode:
    """One podcast episode, read from its blog post."""

    #: Post title, e.g. ``Episode #13: Yearn``
    title: str

    #: Blog post URL
    url: str

    #: Publication time, naive UTC
    published_at: datetime.datetime | None

    #: First paragraph of the post, plain text
    promotion: str

    #: Episode on Spotify, or ``None`` if the post has no link
    spotify_url: str | None

    #: Episode on YouTube, or ``None`` if the post has no link
    youtube_url: str | None

    #: Curator or protocol slug of the guest, for its logo, or ``None``
    logo_slug: str | None

    @property
    def guest(self) -> str:
        """Guest name from the title, e.g. ``Yearn`` from ``Episode #13: Yearn``."""
        return self.title.split(":", 1)[-1].strip()


def clean_episode_link(url: str) -> str:
    """Remove share-tracking parameters from a Spotify or YouTube link.

    Spotify links need no query string. YouTube links keep only the ``v``
    video id of ``youtube.com/watch`` links.

    :param url:
        Link from the post, e.g. ``https://youtu.be/abc?si=xyz&ref=example.ghost.io``.

    :return:
        E.g. ``https://youtu.be/abc``.
    """
    parts = urlsplit(url)
    video = parse_qs(parts.query).get("v", [])[:1]
    query = urlencode({"v": video[0]}) if video and parts.path == "/watch" else ""
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def parse_podcast_episode(post: GhostPost) -> PodcastEpisode:
    """Read an episode's promotion text, links and guest from its blog post.

    :param post:
        Episode post with its HTML body.

    :return:
        Parsed episode. Links that are missing from the post are ``None``.
    """
    body = post.html or ""
    first_paragraph = re.search(r"<p>(.*?)</p>", body, flags=re.DOTALL)
    promotion = html.unescape(re.sub(r"<[^>]+>", "", first_paragraph.group(1))).strip() if first_paragraph else ""
    spotify = SPOTIFY_LINK.search(body)
    youtube = YOUTUBE_LINK.search(body)
    guest_page = GUEST_PAGE_LINK.search(body)
    episode = PodcastEpisode(
        title=post.title,
        url=f"{BLOG_URL}/{post.slug}",
        published_at=post.published_at,
        promotion=" ".join(promotion.split()),
        spotify_url=clean_episode_link(html.unescape(spotify.group(1))) if spotify else None,
        youtube_url=clean_episode_link(html.unescape(youtube.group(1))) if youtube else None,
        logo_slug=guest_page.group(1) if guest_page else None,
    )
    missing = [name for name, value in (("Spotify link", episode.spotify_url), ("YouTube link", episode.youtube_url), ("guest page link", episode.logo_slug)) if value is None]
    if missing:
        logger.warning("Podcast episode %s has no %s", post.slug, ", ".join(missing))
    return episode


def fetch_latest_podcast_episodes(client: GhostContentClient, count: int = LATEST_PODCAST_COUNT) -> list[PodcastEpisode]:
    """Fetch the latest podcast episodes from the blog.

    :param client:
        Ghost Content API client for the blog.

    :param count:
        Number of episodes.

    :return:
        Episodes, newest first.
    """
    episodes = [parse_podcast_episode(post) for post in client.fetch_latest_posts(PODCAST_FILTER, limit=count)]
    logger.info("Latest podcast episodes: %s", ", ".join(episode.title for episode in episodes))
    return episodes


def render_podcast_episodes(episodes: list[PodcastEpisode], images: dict[str, str]) -> str:
    """Render the podcast episode list for the post.

    Each episode is a table row with the guest's logo, the episode title linked
    to its blog post, the promotion text and the YouTube and Spotify links,
    each with the service's icon. A table keeps the logo beside the text in
    Ghost and in newsletter email clients.

    :param episodes:
        Episodes, newest first.

    :param images:
        Image URLs or relative paths, keyed by :py:func:`logo_image_key` for
        guest logos and :py:func:`icon_image_key` for service icons. Missing
        logos and icons are left out.

    :return:
        HTML table.
    """
    rows = []
    for episode in episodes:
        logo_src = images.get(logo_image_key(episode.logo_slug)) if episode.logo_slug else None
        logo = f'<img src="{html.escape(logo_src)}" alt="{html.escape(episode.guest)} logo" width="48" height="48" style="width:48px;height:48px;">' if logo_src else ""
        links = []
        for service, url in (("youtube", episode.youtube_url), ("spotify", episode.spotify_url)):
            if not url:
                continue
            icon_src = images.get(icon_image_key(service))
            # The icon is decorative: the link text names the service
            icon = f'<img src="{html.escape(icon_src)}" alt="" width="16" height="16" style="width:16px;height:16px;vertical-align:-3px;margin-right:4px;">' if icon_src else ""
            links.append(f'<a href="{html.escape(url)}">{icon}{PODCAST_SERVICES[service]}</a>')
        text = [
            f'<strong><a href="{html.escape(episode.url)}">{html.escape(episode.title)}</a></strong>',
            html.escape(episode.promotion),
            " · ".join(links),
        ]
        rows.append(f'<tr><td style="width:64px;vertical-align:top;">{logo}</td><td>{"<br>".join(part for part in text if part)}</td></tr>')
    return '<table class="podcast-episodes">\n<tbody>\n' + "\n".join(rows) + "\n</tbody>\n</table>"
