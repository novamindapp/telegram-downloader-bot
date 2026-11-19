"""APKPure search + app details.

Verified live 2026-11-19 (browser read, no login):
  * https://apkpure.com/search?q=facebook+lite
    -> "Results for \"facebook lite\"", "21 Apps and 2 Articles found",
       first result "Facebook Lite", "Meta Platforms, Inc.", "3.6 MB".
  * https://apkpure.com/whatsapp-messenger/com.whatsapp
    -> "WhatsApp APK for Android Download", rating 7.9/10.

Search results on apkpure.com link to the app page as
    /<slug>/<package>        e.g. /facebook-lite/com.facebook.lite
    /whatsapp-messenger/com.whatsapp
That link pattern is what the parser below looks for. APKPure has no
public search API, so this parses the public search page. If APKPure
changes its page layout, search returns [] and the bot tells the user
to try again later instead of crashing.
"""

import re
import html as html_lib

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://apkpure.com"
SEARCH_URL = BASE_URL + "/search"

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


def search_apkpure(query: str, limit: int = 5) -> list[dict]:
    """Search APKPure for an app name. Returns up to `limit` results."""
    query = query.strip()
    if not query:
        return []
    resp = requests.get(
        SEARCH_URL, params={"q": query}, headers=HEADERS, timeout=30
    )
    resp.raise_for_status()
    return parse_search_html(resp.text, limit=limit)


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
        # Standard APKPure download URL pattern (the "Download APK"
        # button on the app page points here). It redirects to the file.
        "download_url": f"{BASE_URL}/{slug}/{package}/download",
    }


def get_app_details(slug: str, package: str) -> dict:
    """Fetch an app page from APKPure and return its details."""
    resp = requests.get(
        f"{BASE_URL}/{slug}/{package}", headers=HEADERS, timeout=30
    )
    resp.raise_for_status()
    return parse_detail_html(resp.text, slug, package)
