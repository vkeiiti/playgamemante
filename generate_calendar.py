#!/usr/bin/env python3
"""Generate an ICS feed containing official maintenance windows for selected games.

The important design choice is that we DO NOT scan arbitrary dates/times from an
article. We first identify a maintenance notice, then extract a date/time range
only from the maintenance section. This prevents event periods, banner periods,
and other unrelated times from becoming fake maintenance events.
"""
from __future__ import annotations

import hashlib
import html
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

JST = timezone(timedelta(hours=9))
UTC8 = timezone(timedelta(hours=8))
PAST_DAYS = 14
FUTURE_DAYS = 240
UA = "Mozilla/5.0 (compatible; GameMaintenanceCalendar/3.0)"

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA, "Accept-Language": "ja,en;q=0.8"})

GAMES = {
    "NTE": {
        "label": "NTE",
        "domains": ["nte.perfectworld.com"],
        "indexes": [
            "https://nte.perfectworld.com/jp/article/news/gamenews/index.html",
            "https://nte.perfectworld.com/jp/article/news/gamenews/index1.html",
            "https://nte.perfectworld.com/jp/article/news/gamenews/index2.html",
        ],
        "tz": JST,
        "server": None,
    },
    "エンドフィールド": {
        "label": "エンドフィールド",
        "domains": ["endfield.gryphline.com"],
        "indexes": ["https://endfield.gryphline.com/ja-jp/news"],
        "tz": UTC8,
        "server": "Asia",
    },
    "原神": {
        "label": "原神",
        "domains": ["genshin.hoyoverse.com"],
        "indexes": ["https://genshin.hoyoverse.com/ja/news"],
        "tz": JST,
        "server": None,
    },
    "崩壊：スターレイル": {
        "label": "崩壊：スターレイル",
        "domains": ["hsr.hoyoverse.com"],
        "indexes": ["https://hsr.hoyoverse.com/ja-jp/news"],
        "tz": JST,
        "server": None,
    },
    "ステラソラ": {
        "label": "ステラソラ",
        "domains": ["stellasora.jp", "www.stellasora.jp"],
        "indexes": ["https://www.stellasora.jp/news/"],
        "tz": JST,
        "server": None,
    },
    "ドルフィンウェーブ": {
        "label": "ドルフィンウェーブ",
        "domains": ["news-dolphin-wave.marv.jp"],
        "indexes": [
            "https://news-dolphin-wave.marv.jp/category/1",
            "https://news-dolphin-wave.marv.jp/",
        ],
        "tz": JST,
        "server": None,
    },
    "ブルーアーカイブ": {
        "label": "ブルーアーカイブ",
        "domains": ["bluearchive.jp"],
        "indexes": ["https://bluearchive.jp/news/"],
        "tz": JST,
        "server": None,
    },
    "NIKKE": {
        "label": "NIKKE",
        "domains": ["nikke-jp.com"],
        "indexes": ["https://nikke-jp.com/news.html", "https://nikke-jp.com/news_m.html"],
        "tz": JST,
        "server": None,
    },
    "鳴潮": {
        "label": "鳴潮",
        "domains": ["wutheringwaves.kurogames.com"],
        "indexes": ["https://wutheringwaves.kurogames.com/jp/announcement"],
        "tz": JST,
        "server": None,
    },
}

# These are strong maintenance indicators. "アップデート" alone is intentionally
# excluded because update articles contain many unrelated event dates/times.
MAINTENANCE_TERMS = [
    "メンテナンス",
    "maintenance",
    "サーバーメンテ",
    "臨時メンテ",
    "メンテ日時",
    "メンテナンス日時",
    "メンテナンス時間",
    "メンテナンス期間",
    "メンテナンス実施",
    "実施時間",
]

SECTION_MARKERS = [
    "メンテナンス時間＆補填について",
    "メンテナンス時間",
    "メンテナンス期間",
    "メンテナンス日時",
    "メンテナンス実施日時",
    "メンテナンス実施時間",
    "メンテナンス実施",
    "バージョンアップメンテナンス",
    "アップデートメンテナンス",
    "実施時間",
]

DATE = r"(?:(20\d{2})\s*[年./-]\s*)?(\d{1,2})\s*[月./-]\s*(\d{1,2})\s*(?:日)?"
TIME = r"(\d{1,2})\s*[:：]\s*(\d{2})"
RANGE = re.compile(
    rf"(?P<d1>{DATE})\s*(?P<t1>{TIME})\s*(?:～|〜|~|−|–|—|-|－|から)\s*"
    rf"(?:(?P<d2>{DATE})\s*)?(?P<t2>{TIME})",
    re.I,
)


def fetch(url: str) -> str:
    r = SESSION.get(url, timeout=30)
    r.raise_for_status()
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding or "utf-8"
    return r.text


def clean(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    text = html.unescape(text)
    text = text.replace("（", "(").replace("）", ")")
    text = re.sub(r"\s+", " ", text)
    return text


def page_title(soup: BeautifulSoup) -> str:
    for tag in (soup.find("h1"), soup.find("h2"), soup.find("title")):
        if tag:
            return re.sub(r"\s+", " ", tag.get_text(" ", strip=True))
    return ""


def discover(indexes: list[str], domains: list[str]) -> list[str]:
    out: set[str] = set()
    for index in indexes:
        try:
            soup = BeautifulSoup(fetch(index), "html.parser")
        except Exception as e:
            print(f"[WARN] index {index}: {e}")
            continue
        for a in soup.find_all("a", href=True):
            u = urljoin(index, a["href"]).split("#")[0]
            host = urlparse(u).netloc.lower()
            if any(host == d or host.endswith("." + d) for d in domains):
                if u.startswith(("http://", "https://")):
                    out.add(u)
    return sorted(out)


def default_year(text: str) -> int:
    m = re.search(r"(20\d{2})\s*[年./-]\s*\d{1,2}\s*[月./-]\s*\d{1,2}", text)
    if m:
        return int(m.group(1))
    return datetime.now(JST).year


def make_dt(y: int, mo: int, d: int, h: int, mi: int, tz) -> datetime | None:
    try:
        return datetime(y, mo, d, h, mi, tzinfo=tz)
    except ValueError:
        return None


def parse_range(match: re.Match, text: str, tz) -> tuple[datetime, datetime] | None:
    g = match.groupdict()
    d1 = re.search(DATE, g["d1"])
    t1 = re.search(TIME, g["t1"])
    d2 = re.search(DATE, g["d2"] or "") if g["d2"] else None
    t2 = re.search(TIME, g["t2"])
    if not d1 or not t1 or not t2:
        return None

    y0 = default_year(text[: match.start() + 100])
    y1 = int(d1.group(1)) if d1.group(1) else y0
    mo1, day1 = int(d1.group(2)), int(d1.group(3))
    y2 = int(d2.group(1)) if d2 and d2.group(1) else y1
    mo2 = int(d2.group(2)) if d2 else mo1
    day2 = int(d2.group(3)) if d2 else day1

    start = make_dt(y1, mo1, day1, int(t1.group(1)), int(t1.group(2)), tz)
    end = make_dt(y2, mo2, day2, int(t2.group(1)), int(t2.group(2)), tz)
    if not start or not end:
        return None
    if end <= start:
        end += timedelta(days=1)
    return start, end


def extract_maintenance_ranges(text: str, tz, server: str | None) -> list[tuple[datetime, datetime]]:
    """Extract only ranges inside maintenance-labelled sections."""
    text = re.sub(r"\s+", " ", text)
    snippets: list[str] = []

    # A section marker gets a generous window, but only the first matching range
    # is accepted. This avoids later event periods in the same article.
    for marker in SECTION_MARKERS:
        for m in re.finditer(re.escape(marker), text, flags=re.I):
            snippets.append(text[m.start() : m.start() + 500])

    # Endfield articles contain multiple regions. Restrict the snippet to the
    # Asia server line before parsing, otherwise Americas/Europe times leak in.
    if server:
        asia_snippets = []
        for snip in snippets:
            for m in re.finditer(r"Asia\s*サーバー", snip, flags=re.I):
                asia_snippets.append(snip[m.start() : m.start() + 260])
        snippets = asia_snippets

    results: list[tuple[datetime, datetime]] = []
    for snip in snippets:
        m = RANGE.search(snip)
        if not m:
            continue
        parsed = parse_range(m, snip, tz)
        if parsed:
            results.append(parsed)

    # Deduplicate while preserving order.
    return list(dict.fromkeys(results))


def is_maintenance_article(title: str, text: str) -> bool:
    # Strong title signal.
    if any(term.lower() in title.lower() for term in MAINTENANCE_TERMS):
        return True
    # Strong body signal. Do NOT use "アップデート" by itself.
    return any(term.lower() in text.lower() for term in MAINTENANCE_TERMS)


def make_event(game: str, start: datetime, end: datetime, url: str, title: str) -> dict:
    uid = hashlib.sha1(
        f"{game}|{url}|{start.isoformat()}|{end.isoformat()}".encode()
    ).hexdigest()[:24]
    return {
        "uid": uid + "@game-maintenance-calendar",
        "game": game,
        "title": f"{game} メンテナンス",
        "start": start,
        "end": end,
        "url": url,
        "description": f"公式告知: {title}\n{url}",
    }


def scrape(game: str, cfg: dict) -> list[dict]:
    links = discover(cfg["indexes"], cfg["domains"])
    print(f"[INFO] {game}: {len(links)} candidate links")
    events: list[dict] = []
    for url in links:
        try:
            soup = BeautifulSoup(fetch(url), "html.parser")
            title = page_title(soup)
            text = clean(soup)
            if not is_maintenance_article(title, text):
                continue
            ranges = extract_maintenance_ranges(text, cfg["tz"], cfg["server"])
            for start, end in ranges:
                events.append(make_event(game, start, end, url, title))
            if ranges:
                for start, end in ranges:
                    print(f"[INFO] {game}: {start.astimezone(JST)} -> {end.astimezone(JST)} {url}")
        except Exception as e:
            print(f"[WARN] {game}: {url}: {e}")
        time.sleep(0.10)
    return events


def esc(v: str) -> str:
    return (
        v.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
        .replace("\r", "")
    )


def fold(line: str) -> str:
    out, cur, n = [], "", 0
    for ch in line:
        b = len(ch.encode("utf-8"))
        if n + b > 75:
            out.append(cur)
            cur, n = " " + ch, 1 + b
        else:
            cur += ch
            n += b
    out.append(cur)
    return "\r\n".join(out)


def build(events: list[dict]) -> str:
    now = datetime.now(timezone.utc)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Game Maintenance Calendar//JP//",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:NTE + エンドフィールド + 原神 + スタレ + ステラソラ + ドルウェブ + ブルアカ + NIKKE + 鳴潮",
        "X-WR-TIMEZONE:Asia/Tokyo",
    ]
    for e in sorted(events, key=lambda x: x["start"]):
        lines += [
            "BEGIN:VEVENT",
            f"UID:{e['uid']}",
            f"DTSTAMP:{now:%Y%m%dT%H%M%SZ}",
            f"DTSTART:{e['start']:%Y%m%dT%H%M%SZ}",
            f"DTEND:{e['end']:%Y%m%dT%H%M%SZ}",
            f"SUMMARY:{esc(e['title'])}",
            f"DESCRIPTION:{esc(e['description'])}",
            f"URL:{e['url']}",
            "STATUS:CONFIRMED",
            "TRANSP:OPAQUE",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(x) for x in lines) + "\r\n"


def main() -> None:
    all_events: list[dict] = []
    for game, cfg in GAMES.items():
        all_events.extend(scrape(game, cfg))

    now = datetime.now(timezone.utc)
    low = now - timedelta(days=PAST_DAYS)
    high = now + timedelta(days=FUTURE_DAYS)
    dedup = {e["uid"]: e for e in all_events if low <= e["start"] <= high}
    events = sorted(dedup.values(), key=lambda x: x["start"])

    if not events:
        raise RuntimeError(
            "No maintenance events found. A source page or its layout may have changed."
        )

    with open("calendar.ics", "w", encoding="utf-8", newline="") as f:
        f.write(build(events))
    print(f"[OK] calendar.ics: {len(events)} event(s)")


if __name__ == "__main__":
    main()
