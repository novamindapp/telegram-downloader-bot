"""Offline tests for apkpure.py -- no network, no token.

The fixture HTML mimics the real APKPure search page structure
(verified live 2026-11-19: search results link as /slug/package,
e.g. /facebook-lite/com.facebook.lite, first result "Facebook Lite").
Run:  python test_apkpure.py
"""

from apkpure import (
    parse_detail_html,
    parse_play_search_html,
    parse_search_html,
    search_apkpure,
    slugify,
)
import apkpure

SEARCH_FIXTURE = """
<html><body>
<div class="search-result">
  <a href="/facebook-lite/com.facebook.lite" title="Facebook Lite">Facebook Lite</a>
  <a href="/messenger-lite/com.facebook.mlite" title="Messenger Lite">Messenger Lite</a>
  <a href="/facebook/com.facebook.katana" title="Facebook">Facebook</a>
  <a href="/search?q=facebook+lite">Search again</a>
</div>
</body></html>
"""

DETAIL_FIXTURE = """
<html><head><title>Facebook Lite APK for Android Download</title></head>
<body>
<div>Latest Version 539.0.0.7.106</div>
<div>File Size 3.6 MB</div>
</body></html>
"""

# Play Store search page structure (verified live 2026-11-19):
# hero result uses aria-label, list results use <span class="DdYX5">.
PLAY_SEARCH_FIXTURE = """
<html><body>
<a href="/store/apps/details?id=com.dts.freefireth" aria-label="Free Fire: Storm Arena" class="Qfxief"></a>
<a class="Si6A0c Gy4nib" href="/store/apps/details?id=com.dts.freefiremax"><span class="DdYX5">Free Fire MAX: Storm Arena</span></a>
<a class="Si6A0c Gy4nib" href="/store/apps/details?id=com.dts.freefiremax"><span class="DdYX5">Free Fire MAX: Storm Arena</span></a>
<a class="Si6A0c Gy4nib" href="/store/apps/details?id=com.tencent.ig"><span class="DdYX5">PUBG MOBILE</span></a>
<a class="Si6A0c Gy4nib" href="/store/apps/details?id=com.gameloft.android."><span class="DdYX5">Bad Package</span></a>
</body></html>
"""


def test_search():
    results = parse_search_html(SEARCH_FIXTURE, limit=5)
    assert len(results) == 3, results
    assert results[0]["name"] == "Facebook Lite", results[0]
    assert results[0]["package"] == "com.facebook.lite", results[0]
    assert results[0]["slug"] == "facebook-lite", results[0]
    assert results[0]["detail_url"] == (
        "https://apkpure.com/facebook-lite/com.facebook.lite"
    )
    assert results[1]["package"] == "com.facebook.mlite"
    assert results[2]["package"] == "com.facebook.katana"
    print("test_search OK:", [(r["name"], r["package"]) for r in results])


def test_search_empty():
    assert parse_search_html("<html><body>No results</body></html>") == []
    print("test_search_empty OK")


def test_detail():
    d = parse_detail_html(DETAIL_FIXTURE, "facebook-lite", "com.facebook.lite")
    assert d["name"] == "Facebook Lite", d
    assert d["version"] == "539.0.0.7.106", d
    assert d["size"] == "3.6 MB", d
    assert d["download_url"] == (
        "https://d.apkpure.net/b/APK/com.facebook.lite?version=latest"
    )
    assert d["detail_url"] == (
        "https://apkpure.com/facebook-lite/com.facebook.lite"
    )
    print("test_detail OK:", d)


def test_play_search():
    results = parse_play_search_html(PLAY_SEARCH_FIXTURE, limit=5)
    # Duplicate freefiremax deduped, bad trailing-dot package dropped.
    assert len(results) == 3, results
    assert results[0]["name"] == "Free Fire: Storm Arena", results[0]
    assert results[0]["package"] == "com.dts.freefireth", results[0]
    assert results[0]["slug"] == "free-fire-storm-arena", results[0]
    assert results[0]["detail_url"] == (
        "https://apkpure.com/free-fire-storm-arena/com.dts.freefireth"
    )
    assert results[1]["package"] == "com.dts.freefiremax"
    assert results[1]["slug"] == "free-fire-max-storm-arena"
    assert results[2]["package"] == "com.tencent.ig"
    print("test_play_search OK:", [(r["name"], r["package"]) for r in results])


def test_slugify():
    assert slugify("Free Fire: Storm Arena") == "free-fire-storm-arena"
    assert slugify("Facebook Lite") == "facebook-lite"
    assert slugify("WhatsApp Messenger") == "whatsapp-messenger"
    print("test_slugify OK")


def test_search_play_fallback():
    """search_apkpure() falls back to Play when APKPure fails (offline).

    Monkeypatches apkpure.requests.get: APKPure raises, Play returns
    the fixture page. No network is used.
    """

    class FakeResp:
        def __init__(self, text, status=200):
            self.text = text
            self.status_code = status

        def raise_for_status(self):
            if self.status_code >= 400:
                raise Exception(f"HTTP {self.status_code}")

    real_get = apkpure.requests.get

    def fake_get(url, **kwargs):
        if "apkpure.com" in url:
            raise Exception("403 Cloudflare")
        return FakeResp(PLAY_SEARCH_FIXTURE)

    apkpure.requests.get = fake_get
    try:
        results = search_apkpure("free fire", limit=5)
    finally:
        apkpure.requests.get = real_get
    assert len(results) == 3, results
    assert results[0]["package"] == "com.dts.freefireth", results[0]
    print("test_search_play_fallback OK:", [(r["name"], r["package"]) for r in results])


if __name__ == "__main__":
    test_search()
    test_search_empty()
    test_detail()
    test_play_search()
    test_slugify()
    test_search_play_fallback()
    print("All offline tests passed.")
