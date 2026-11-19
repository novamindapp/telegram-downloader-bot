"""APKPure search + app details, with a Play Store search fallback.

Root cause (verified 2026-11-19 from a server, same requests code as
the bot): https://apkpure.com/search?q=free+fire returns 403
Cloudflare "Just a moment..." (5763 bytes) from a server IP, and
sometimes a ChunkedEncodingError. The APKPure search page works in a
browser, not from a server. So search_apkpure() tries APKPure first
and, on ANY failure (403, timeout, empty results), falls back to a
Play Store search (https://play.google.com/store/search returns 200
from the same server) and parses package + app name from that page.
The download itself still comes from APKPure's direct file host:
https://d.apkpure.net/b/APK/<package>?version=latest returns 200
(verified: 14 MB for com.dts.freefireth, 3.6 MB for
com.facebook.lite), so the bot never needs the Play Store to hand
out an APK file -- the Play Store is only used to find the package
name.

Search results link to the app page as
    /<slug>/<package>        e.g. /facebook-lite/com.facebook.lite
That link pattern is what the APKPure parser looks for. APKPure has
no public search API, so this parses public pages. If a layout
changes, search returns [] and the bot tells the user to try again
later instead of crashing.
"""

import logging
import re
import html as html_lib

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("apkpure")

BASE_URL = "https://apkpure.com"
SEARCH_URL = BASE_URL + "/search"
PLAY_SEARCH_URL = "https://play.google.com/store/search"
# Direct APKPure file host -- verified 200 from a server 2026-11-19.
DOWNLOAD_URL = "https://d.apkpure.net/b/APK/{package}?version=latest"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# /slug/package  -- slug is the URL name, package is like com.facebook.lite
APP_LINK_RE = re.compile(
    r"^/([a-z0-9][a-z0-9\-]*)/([a-z][a-z0-9_]*(\.[a-z0-9_]+)+)/?$",
    re.IGNORECASE,
)

# Play Store app link: /store/apps/details?id=com.example.app
PLAY_LINK_RE = re.compile(
    r"^/store/apps/details\?id=([a-z][a-z0-9_]*(\.[a-z0-9_]+)+)$",
    re.IGNORECASE,
)

VERSION_RE = re.compile(r"([0-9]+(?:\.[0-9A-Za-z]+)+)")
SIZE_RE = re.compile(r"(\d+(?:\.\d+)?\s*(?:MB|GB|KB))", re.IGNORECASE)

# Words that are never an app name (nav / article links that can match
# the /slug/package shape by accident).
_SKIP_NAMES = {
    "search", "download", "apk", "apps", "games", "home",
    "how-to", "how to",
}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(text or "")).strip()


def slugify(name: str) -> str:
    """Turn an app name into an APKPure-style slug.

    e.g. "Free Fire: Storm Arena" -> "free-fire-storm-arena".
    """
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "app"


def parse_search_html(html: str, limit: int = 5) -> list[dict]:
    """Parse an APKPure search page (raw HTML) into app results.

    Returns a list of {"name", "slug", "package", "detail_url"}.
    Pure function -- easy to test offline with a saved fixture.
    """
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        m = APP_LINK_RE.match(a["href"].strip())
        if not m:
            continue
        slug, package = m.group(1), m.group(2)
        if package.lower() in seen:
            continue
        name = _clean(a.get("title") or a.get_text(" ", strip=True))
        # The link text is usually just the app name; if it carries extra
        # junk (developer, size), keep only the first line.
        name = name.split("\n")[0].strip()
        if not name or name.lower() in _SKIP_NAMES:
            continue
        if len(name) > 80:
            continue
        seen.add(package.lower())
        results.append(
            {
                "name": name,
                "slug": slug,
                "package": package,
                "detail_url": f"{BASE_URL}/{slug}/{package}",
            }
        )
        if len(results) >= limit:
            break

    return results


def parse_play_search_html(html: str, limit: int = 5) -> list[dict]:
    """Parse a Play Store search page (raw HTML) into app results.

    Play result links look like:
        <a href="/store/apps/details?id=com.example.app"
           aria-label="App Name" ...>           (hero result)
        <a href="/store/apps/details?id=com.example.app" ...>
            <span class="DdYX5">App Name</span>  (list results)
    Returns the same shape as parse_search_html, with detail_url
    pointing at the APKPure app page (slug built from the name).
    Pure function -- easy to test offline with a saved fixture.
    """
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        m = PLAY_LINK_RE.match(a["href"].strip())
        if not m:
            continue
        package = m.group(1)
        if package.lower() in seen:
            continue
        name = _clean(a.get("aria-label") or "")
        if not name:
            span = a.find("span", class_="DdYX5")
            if span:
                name = _clean(span.get_text(" ", strip=True))
        if not name:
            name = _clean(a.get_text(" ", strip=True)).split("\n")[0].strip()
        if not name or len(name) > 80:
            continue
        seen.add(package.lower())
        slug = slugify(name)
        results.append(
            {
                "name": name,
                "slug": slug,
                "package": package,
                "detail_url": f"{BASE_URL}/{slug}/{package}",
            }
        )
        if len(results) >= limit:
            break

    return results


def search_play_store(query: str, limit: int = 5) -> list[dict]:
    """Search the Play Store for an app name. Returns up to `limit` results."""
    query = query.strip()
    if not query:
        return []
    resp = requests.get(
        PLAY_SEARCH_URL,
        params={"q": query, "c": "apps"},
        headers=HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    return parse_play_search_html(resp.text, limit=limit)


def search_apkpure(query: str, limit: int = 5) -> list[dict]:
    """Search for an app name. Returns up to `limit` results.

    Tries APKPure first; on ANY failure (Cloudflare 403 from a
    server, timeout, empty results) falls back to a Play Store
    search, which works from a server. Results keep the same shape
    {"name", "slug", "package", "detail_url"} either way.
    """
    query = query.strip()
    if not query:
        return []
    try:
        resp = requests.get(
            SEARCH_URL, params={"q": query}, headers=HEADERS, timeout=30
        )
        resp.raise_for_status()
        results = parse_search_html(resp.text, limit=limit)
        if results:
            return results
        logger.warning("APKPure search for %r returned no results", query)
    except Exception as e:
        logger.warning("APKPure search failed for %r: %s", query, e)

    # Fallback: Play Store search works from a server.
    try:
        results = search_play_store(query, limit=limit)
        if results:
            logger.info(
                "Play Store fallback found %d result(s) for %r",
                len(results),
                query,
            )
            return results
        logger.warning("Play Store fallback found nothing for %r", query)
    except Exception as e:
        logger.warning("Play Store fallback failed for %r: %s", query, e)
    return []


def parse_detail_html(html: str, slug: str, package: str) -> dict:
    """Parse an APKPure app page (raw HTML) into name/version/size."""
    soup = BeautifulSoup(html, "html.parser")
    title = ""
    if soup.title and soup.title.string:
        title = _clean(soup.title.string)
    name = title
    for suffix in (" APK for Android Download", " APK Download", " APK"):
        if name.endswith(suffix):
            name = name[: -len(suffix)].strip()
            break
    if not name:
        name = slug.replace("-", " ").title()

    text = soup.get_text(" ", strip=True)

    version = ""
    m = re.search(r"Latest Version\s+" + VERSION_RE.pattern, text)
    if not m:
        m = re.search(r"\bVersion\s+" + VERSION_RE.pattern, text)
    if m:
        version = m.group(1)

    size = ""
    m = re.search(r"File Size\s*" + SIZE_RE.pattern, text)
    if not m:
        m = SIZE_RE.search(text)
    if m:
        size = m.group(1).upper()

    return {
        "name": name,
        "slug": slug,
        "package": package,
        "version": version,
        "size": size,
        "detail_url": f"{BASE_URL}/{slug}/{package}",
        # Direct APKPure file host -- verified 200 from a server
        # 2026-11-19. The old /download pattern was never live-tested.
        "download_url": DOWNLOAD_URL.format(package=package),
    }


def get_app_details(slug: str, package: str) -> dict:
    """Fetch an app page from APKPure and return its details.

    If the APKPure app page itself is blocked from a server
    (Cloudflare 403, same as search), return a minimal details dict
    with the direct download URL, so the bot can still give the
    user a working download link instead of failing.
    """
    try:
        resp = requests.get(
            f"{BASE_URL}/{slug}/{package}", headers=HEADERS, timeout=30
        )
        resp.raise_for_status()
        return parse_detail_html(resp.text, slug, package)
    except Exception as e:
        logger.warning(
            "APKPure app page failed for %s: %s -- using direct link",
            package,
            e,
        )
        return {
            "name": slug.replace("-", " ").title(),
            "slug": slug,
            "package": package,
            "version": "",
            "size": "",
            "detail_url": f"{BASE_URL}/{slug}/{package}",
            "download_url": DOWNLOAD_URL.format(package=package),
        }
