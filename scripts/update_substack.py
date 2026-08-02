#!/usr/bin/env python3
"""Sync Substack posts into the Writing list in index.html.

Substack serves no CORS headers, so the browser can't fetch the archive
directly. Instead this runs in CI (see .github/workflows/update-substack.yml)
and writes the posts straight into the HTML between the marker comments.

Posts come from Substack's archive API when it's reachable. Cloudflare
returns 403 to GitHub's runner IPs, so there's a fallback through rss2json,
which fetches the RSS feed from its own servers and hands back JSON.

Exits non-zero without touching index.html if every source fails, so a
blocked request can never blank out the list.
"""

import html
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PUBLICATION = "https://nolastan.substack.com"
INDEX = Path(__file__).resolve().parent.parent / "index.html"

# Posts to keep out of the Writing list, by slug — the trailing part of the
# post URL (.../p/<slug>). Slugs are stable even when a title is edited.
EXCLUDED_SLUGS = {
    "easter-420-earth-day-and-climate",  # Sunset Dunes is Open!
}

START = "<!-- substack:start -->"
END = "<!-- substack:end -->"

PAGE_SIZE = 50
# Substack's RSS feed carries only the most recent posts, so the fallback
# can't see the whole archive. Warn if a run comes back at the cap.
FEED_LIMIT = 20
USER_AGENT = "nolastan.github.io-substack-sync"
INDENT = " " * 8


def get(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def slug_from(url):
    return urllib.parse.urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]


def normalize(title, url, date):
    """Flatten a post from either source into one shape.

    Dates arrive as '2026-08-02T00:00:20.369Z' from the API and
    '2026-08-02 00:00:20' from rss2json; both normalize to a string that
    sorts correctly and exposes the year as the first four characters.
    """
    return {
        "title": title or "Untitled",
        "url": url,
        "slug": slug_from(url),
        "date": date.replace("T", " ").rstrip("Z"),
    }


def fetch_from_api():
    """Page through the archive API until it runs out of posts."""
    posts = []
    offset = 0
    while True:
        page = get(f"{PUBLICATION}/api/v1/archive?sort=new&limit={PAGE_SIZE}&offset={offset}")
        if not isinstance(page, list):
            raise ValueError(f"unexpected response at offset {offset}: {page!r}")
        posts.extend(
            normalize(
                post["title"],
                post.get("canonical_url") or f"{PUBLICATION}/p/{post['slug']}",
                post["post_date"],
            )
            for post in page
        )
        if len(page) < PAGE_SIZE:
            return posts
        offset += PAGE_SIZE


def fetch_from_rss2json():
    """Read the RSS feed via rss2json, which fetches it server-side."""
    feed = urllib.parse.quote(f"{PUBLICATION}/feed", safe="")
    data = get(f"https://api.rss2json.com/v1/api.json?rss_url={feed}")
    if data.get("status") != "ok":
        raise ValueError(f"rss2json returned {data.get('status')!r}: {data.get('message')!r}")
    items = data.get("items") or []
    if len(items) >= FEED_LIMIT:
        print(
            f"Warning: RSS feed returned {len(items)} posts, at or above its "
            f"cap of {FEED_LIMIT} — older posts may be missing from the list."
        )
    return [normalize(item["title"], item["link"], item["pubDate"]) for item in items]


def fetch_posts():
    """Try each source in turn; return the first that yields posts."""
    failures = []
    for name, fetch in (("archive API", fetch_from_api), ("rss2json", fetch_from_rss2json)):
        try:
            posts = fetch()
        except (urllib.error.URLError, ValueError, KeyError, json.JSONDecodeError) as error:
            print(f"Source {name} failed: {error}")
            failures.append(f"{name}: {error}")
            continue
        if posts:
            print(f"Fetched {len(posts)} posts from {name}.")
            return posts
        failures.append(f"{name}: returned no posts")
    sys.exit(
        "Could not fetch any Substack posts; leaving index.html untouched.\n  "
        + "\n  ".join(failures)
    )


def render(posts):
    posts = sorted(posts, key=lambda post: post["date"], reverse=True)
    items = []
    for post in posts:
        items.append(
            f'{INDENT}<li>\n'
            f'{INDENT}  <span class="w-date">{post["date"][:4]}</span>\n'
            f'{INDENT}  <a href="{html.escape(post["url"])}" target="_blank">'
            f'{html.escape(post["title"])}</a>\n'
            f'{INDENT}</li>'
        )
    return "\n".join(items)


def main():
    posts = fetch_posts()

    published = [post for post in posts if post["slug"] not in EXCLUDED_SLUGS]
    for slug in sorted(EXCLUDED_SLUGS - {post["slug"] for post in posts}):
        print(f"Warning: excluded slug {slug!r} matches no post in the archive.")

    source = INDEX.read_text(encoding="utf-8")
    block = f"{START}\n{render(published)}\n{INDENT}{END}"
    updated, count = re.subn(
        re.escape(START) + r".*?" + re.escape(END),
        lambda _: block,
        source,
        flags=re.DOTALL,
    )
    if count != 1:
        sys.exit(f"Expected exactly one {START}...{END} block, found {count}.")

    excluded = len(posts) - len(published)
    summary = f"{len(published)} posts, {excluded} excluded"
    if updated == source:
        print(f"No change ({summary}).")
        return

    INDEX.write_text(updated, encoding="utf-8")
    print(f"Updated Writing list with {summary}.")


if __name__ == "__main__":
    main()
