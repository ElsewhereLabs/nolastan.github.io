#!/usr/bin/env python3
"""Sync Substack posts into the Writing list in index.html.

Substack serves no CORS headers, so the browser can't fetch the archive
directly. Instead this runs in CI (see .github/workflows/update-substack.yml)
and writes the posts straight into the HTML between the marker comments.

Exits non-zero without touching index.html if the fetch fails or returns
nothing, so a bad API response can never blank out the list.
"""

import html
import json
import re
import sys
import urllib.error
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
USER_AGENT = "nolastan.github.io-substack-sync"
INDENT = " " * 8


def fetch_posts():
    """Page through the archive API until it runs out of posts."""
    posts = []
    offset = 0
    while True:
        url = f"{PUBLICATION}/api/v1/archive?sort=new&limit={PAGE_SIZE}&offset={offset}"
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=30) as response:
            page = json.load(response)
        if not isinstance(page, list):
            raise ValueError(f"unexpected response at offset {offset}: {page!r}")
        posts.extend(page)
        if len(page) < PAGE_SIZE:
            return posts
        offset += PAGE_SIZE


def render(posts):
    posts = sorted(posts, key=lambda post: post["post_date"], reverse=True)
    items = []
    for post in posts:
        title = html.escape(post["title"] or "Untitled")
        url = html.escape(post.get("canonical_url") or f"{PUBLICATION}/p/{post['slug']}")
        year = post["post_date"][:4]
        items.append(
            f'{INDENT}<li>\n'
            f'{INDENT}  <span class="w-date">{year}</span>\n'
            f'{INDENT}  <a href="{url}" target="_blank">{title}</a>\n'
            f'{INDENT}</li>'
        )
    return "\n".join(items)


def main():
    try:
        posts = fetch_posts()
    except (urllib.error.URLError, ValueError, KeyError, json.JSONDecodeError) as error:
        sys.exit(f"Failed to fetch Substack archive: {error}")

    if not posts:
        sys.exit("Substack archive returned no posts; leaving index.html untouched.")

    published = [post for post in posts if post.get("slug") not in EXCLUDED_SLUGS]
    for slug in sorted(EXCLUDED_SLUGS - {post.get("slug") for post in posts}):
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
