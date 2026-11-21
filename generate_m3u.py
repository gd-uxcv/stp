import html
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://m.livetv.sx"
LIST_URL = f"{BASE_URL}/enx/"
OUTPUT_M3U = Path(__file__).resolve().parent / "live_events.m3u"
GROUP_NAME = "✨✦ʟɪᴠᴇ ᴇᴠᴇɴᴛꜱ✦✨"

USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 13; Mobile) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": f"{BASE_URL}/",
}


def normalize_url(raw_url: str | None, base_url: str = BASE_URL) -> str | None:
    if not raw_url:
        return None
    cleaned = raw_url.strip()
    if not cleaned:
        return None
    if cleaned.startswith("//"):
        return "https:" + cleaned
    if cleaned.startswith(("http://", "https://")):
        return cleaned
    return urljoin(base_url.rstrip("/") + "/", cleaned.lstrip("/"))


def is_youtube_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    return any(
        domain in host
        for domain in ("youtube.com", "youtu.be", "youtube-nocookie.com")
    )


def fetch_html(url: str, referer: str | None = None, timeout: int = 20) -> str | None:
    headers = dict(DEFAULT_HEADERS)
    if referer:
        headers["Referer"] = referer
    try:
        resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        if resp.status_code >= 400:
            return None
        resp.encoding = resp.apparent_encoding or "utf-8"
        return resp.text
    except requests.RequestException:
        return None


def get_live_events(list_html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(list_html, "html.parser")
    events: list[dict[str, str]] = []
    seen_hrefs: set[str] = set()

    for li in soup.select('ul#broadcasts li[data-search-item]'):
        live_span = li.select_one("span.live")
        if not live_span:
            continue

        title_div = li.select_one("div.title")
        link_tag = title_div.select_one("a[href]") if title_div else None
        img_tag = li.select_one("div.logo img[src]")

        raw_title = title_div.get_text(" ", strip=True) if title_div else ""
        title = re.sub(r"\s+", " ", html.unescape(raw_title)).strip()
        href = normalize_url(link_tag.get("href") if link_tag else None)
        img = normalize_url(img_tag.get("src") if img_tag else None) or ""

        if not title or not href:
            continue
        if href in seen_hrefs:
            continue
        seen_hrefs.add(href)

        events.append({
            "title": title,
            "href": href,
            "img": img,
        })

    return events


def get_event_embeds(event: dict[str, str]) -> list[str]:
    event_html = fetch_html(event["href"], referer=LIST_URL, timeout=15)
    if not event_html:
        return []

    soup = BeautifulSoup(event_html, "html.parser")
    anchors = soup.select("ul.sp-sources li.sp-source a.sp-source__link[href]")

    valid_embeds: list[str] = []
    for a in anchors:
        embed_url = normalize_url(a.get("href"))
        if not embed_url:
            continue
        if is_youtube_url(embed_url):
            continue
        if embed_url not in valid_embeds:
            valid_embeds.append(embed_url)

    return valid_embeds


def build_m3u() -> str:
    list_html = fetch_html(LIST_URL, referer=f"{BASE_URL}/", timeout=25)
    if not list_html:
        raise RuntimeError("Failed to fetch main broadcast list page.")

    live_events = get_live_events(list_html)
    results_by_index: dict[int, list[str]] = {}

    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_idx = {
            executor.submit(get_event_embeds, event): idx
            for idx, event in enumerate(live_events)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results_by_index[idx] = future.result()
            except Exception:
                results_by_index[idx] = []

    m3u_lines = ["#EXTM3U"]

    for idx, event in enumerate(live_events):
        embeds = results_by_index.get(idx, [])
        if not embeds:
            continue

        safe_logo = event["img"].replace('"', "")
        logo_attr = f' tvg-logo="{safe_logo}"' if safe_logo else ""
        group_attr = f' group-title="{GROUP_NAME}"'

        for stream_num, embed_url in enumerate(embeds, start=1):
            channel_title = f"{event['title']} s-{stream_num}"
            m3u_lines.append(f"#EXTINF:-1{logo_attr}{group_attr},{channel_title}")
            m3u_lines.append(embed_url)

    return "\n".join(m3u_lines) + "\n"


def main() -> None:
    content = build_m3u()
    OUTPUT_M3U.write_text(content, encoding="utf-8")
    print(f"Saved M3U playlist to {OUTPUT_M3U}")


if __name__ == "__main__":
    main()
