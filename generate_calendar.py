#!/usr/bin/env python3
"""Build an ICS calendar containing maintenance notices for selected Japanese game services.

Sources are the games' official Japanese news sites. The scraper discovers recent
news links, fetches article pages, keeps maintenance-related notices, and extracts
Japanese date/time ranges. GitHub Actions runs this periodically and publishes
calendar.ics through GitHub Pages.
"""
from __future__ import annotations
import hashlib, html, re, time
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

JST = timezone(timedelta(hours=9))
UTC8 = timezone(timedelta(hours=8))
PAST_DAYS = 14
FUTURE_DAYS = 240
UA = "Mozilla/5.0 (compatible; GameMaintenanceCalendar/2.0)"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA, "Accept-Language": "ja,en;q=0.8"})

# Official Japanese news/list pages. Multiple pages are supplied where a site
# uses pagination. The crawler also follows links found on these pages.
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
        "server_words": [],
    },
    "エンドフィールド": {
        "label": "エンドフィールド",
        "domains": ["endfield.gryphline.com"],
        "indexes": ["https://endfield.gryphline.com/ja-jp/news"],
        "tz": UTC8,
        "server_words": ["Asia", "アジア", "UTC+8"],
    },
    "原神": {
        "label": "原神",
        "domains": ["genshin.hoyoverse.com"],
        "indexes": ["https://genshin.hoyoverse.com/ja/news"],
        "tz": JST,
        "server_words": [],
    },
    "崩壊：スターレイル": {
        "label": "崩壊：スターレイル",
        "domains": ["hsr.hoyoverse.com"],
        "indexes": ["https://hsr.hoyoverse.com/ja-jp/news"],
        "tz": JST,
        "server_words": [],
    },
    "ステラソラ": {
        "label": "ステラソラ",
        "domains": ["stellasora.jp", "www.stellasora.jp"],
        "indexes": ["https://stellasora.jp/news/"],
        "tz": JST,
        "server_words": [],
    },
    "ドルフィンウェーブ": {
        "label": "ドルフィンウェーブ",
        "domains": ["news-dolphin-wave.marv.jp"],
        "indexes": ["https://news-dolphin-wave.marv.jp/category/1", "https://news-dolphin-wave.marv.jp/"],
        "tz": JST,
        "server_words": [],
    },
    "ブルーアーカイブ": {
        "label": "ブルーアーカイブ",
        "domains": ["bluearchive.jp"],
        "indexes": ["https://bluearchive.jp/news/"],
        "tz": JST,
        "server_words": [],
    },
    "NIKKE": {
        "label": "NIKKE",
        "domains": ["nikke-jp.com"],
        "indexes": ["https://nikke-jp.com/news.html", "https://nikke-jp.com/news_m.html"],
        "tz": JST,
        "server_words": [],
    },
    "鳴潮": {
        "label": "鳴潮",
        "domains": ["wutheringwaves.kurogames.com"],
        "indexes": ["https://wutheringwaves.kurogames.com/jp/announcement"],
        "tz": JST,
        "server_words": [],
    },
}

MAINT_WORDS = [
    "メンテナンス", "maintenance", "サーバーメンテ", "臨時メンテ",
    "アップデート", "サービス停止", "メンテ日時", "メンテナンス日時",
]

DATE_RE = re.compile(r"(?:(20\d{2})\s*[年./-])?\s*(\d{1,2})\s*[月./-]\s*(\d{1,2})\s*(?:日)?")
TIME_RE = re.compile(r"(\d{1,2})\s*[:：]\s*(\d{2})")
RANGE_SEP_RE = re.compile(r"\s*(?:～|〜|~|−|–|—|→|から|[-－])\s*")


def fetch(url: str) -> str:
    r = SESSION.get(url, timeout=30)
    r.raise_for_status()
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding or "utf-8"
    return r.text


def clean(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))


def page_title(soup: BeautifulSoup) -> str:
    for tag in (soup.find("h1"), soup.find("h2"), soup.find("title")):
        if tag:
            return re.sub(r"\s+", " ", tag.get_text(" ", strip=True))
    return ""


def discover(indexes: list[str], domains: list[str]) -> list[str]:
    out = set()
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


def year_from_text(text: str) -> int:
    m = re.search(r"(20\d{2})\s*[年./-]\s*\d{1,2}\s*[月./-]\s*\d{1,2}", text)
    return int(m.group(1)) if m else datetime.now(JST).year


def parse_dt(y: int, mo: int, d: int, h: int, mi: int, tz) -> datetime | None:
    try:
        return datetime(y, mo, d, h, mi, tzinfo=tz).astimezone(timezone.utc)
    except ValueError:
        return None


def extract_ranges(text: str, tz, require_server: bool = False) -> list[tuple[datetime, datetime]]:
    text = html.unescape(text).replace("（", "(").replace("）", ")")
    text = re.sub(r"\s+", " ", text)
    default_year = year_from_text(text)
    results = []

    # Find a date, then a nearby pair of times. This deliberately limits the
    # window to reduce accidental extraction from unrelated event schedules.
    for dm in DATE_RE.finditer(text):
        y = int(dm.group(1)) if dm.group(1) else default_year
        mo, d = int(dm.group(2)), int(dm.group(3))
        window = text[dm.end():dm.end()+220]
        if require_server and not re.search(r"Asia|アジア|UTC\+8", text[max(0, dm.start()-100):dm.end()+100], re.I):
            continue
        times = list(TIME_RE.finditer(window))
        if len(times) < 2:
            continue
        # Usually the first two times after the date are start/end.
        t1, t2 = times[0], times[1]
        sep = RANGE_SEP_RE.search(window, t1.end(), t2.start())
        if not sep:
            # Don't treat two unrelated times as a maintenance range.
            continue
        sh, sm = int(t1.group(1)), int(t1.group(2))
        eh, em = int(t2.group(1)), int(t2.group(2))
        start = parse_dt(y, mo, d, sh, sm, tz)
        end = parse_dt(y, mo, d, eh, em, tz)
        if not start or not end:
            continue
        if end <= start:
            end += timedelta(days=1)
        results.append((start, end))
    # Also handle date/time written on one line with a second date on the end.
    return list(dict.fromkeys(results))


def make_event(game: str, start: datetime, end: datetime, url: str, article_title: str) -> dict:
    uid = hashlib.sha1(f"{game}|{url}|{start.isoformat()}|{end.isoformat()}".encode()).hexdigest()[:24]
    return {
        "uid": uid + "@game-maintenance-calendar",
        "game": game,
        "title": f"{game} メンテナンス",
        "start": start,
        "end": end,
        "url": url,
        "description": f"公式告知: {article_title}\n{url}",
    }


def scrape(game: str, cfg: dict) -> list[dict]:
    links = discover(cfg["indexes"], cfg["domains"])
    print(f"[INFO] {game}: {len(links)} candidate links")
    events = []
    for url in links:
        try:
            soup = BeautifulSoup(fetch(url), "html.parser")
            title = page_title(soup)
            text = clean(soup)
            blob = title + " " + text
            if not any(w.lower() in blob.lower() for w in MAINT_WORDS):
                continue
            # For Endfield we only want the Asia schedule; its official notice
            # may list multiple regions.
            ranges = extract_ranges(text, cfg["tz"], require_server=(game == "エンドフィールド"))
            for start, end in ranges:
                events.append(make_event(game, start, end, url, title))
            if ranges:
                print(f"[INFO] {game}: {len(ranges)} range(s) from {url}")
        except Exception as e:
            print(f"[WARN] {game}: {url}: {e}")
        time.sleep(0.15)
    return events


def esc(v: str) -> str:
    return v.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n").replace("\r", "")


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
        "BEGIN:VCALENDAR", "VERSION:2.0",
        "PRODID:-//Game Maintenance Calendar//JP//",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        "X-WR-CALNAME:NTE + エンドフィールド + 原神 + スタレ + ステラソラ + ドルウェブ + ブルアカ + NIKKE + 鳴潮",
        "X-WR-TIMEZONE:Asia/Tokyo",
    ]
    for e in sorted(events, key=lambda x: x["start"]):
        lines += [
            "BEGIN:VEVENT", f"UID:{e['uid']}", f"DTSTAMP:{now:%Y%m%dT%H%M%SZ}",
            f"DTSTART:{e['start']:%Y%m%dT%H%M%SZ}", f"DTEND:{e['end']:%Y%m%dT%H%M%SZ}",
            f"SUMMARY:{esc(e['title'])}", f"DESCRIPTION:{esc(e['description'])}",
            f"URL:{e['url']}", "STATUS:CONFIRMED", "TRANSP:OPAQUE", "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(x) for x in lines) + "\r\n"


def main():
    all_events = []
    for game, cfg in GAMES.items():
        all_events.extend(scrape(game, cfg))
    now = datetime.now(timezone.utc)
    low, high = now - timedelta(days=PAST_DAYS), now + timedelta(days=FUTURE_DAYS)
    dedup = {e["uid"]: e for e in all_events if low <= e["start"] <= high}
    events = sorted(dedup.values(), key=lambda x: x["start"])
    if not events:
        raise RuntimeError("No maintenance events found. A site layout may have changed.")
    with open("calendar.ics", "w", encoding="utf-8", newline="") as f:
        f.write(build(events))
    print(f"[OK] calendar.ics: {len(events)} event(s)")

if __name__ == "__main__":
    main()
