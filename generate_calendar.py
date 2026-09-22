#!/usr/bin/env python3
"""
Game Maintenance Calendar
Official-site scraper -> calendar.ics

Supported:
NTE, アークナイツ：エンドフィールド, 原神, 崩壊：スターレイル,
ステラソラ, ドルフィンウェーブ, ブルーアーカイブ, NIKKE, 鳴潮

Important:
- Only explicitly labelled maintenance times are accepted.
- Event/banner/reward periods are NOT accepted.
- Endfield uses the Asia server and converts UTC+8 -> JST.
- If a page cannot be parsed safely, it is skipped instead of creating a fake event.
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
NOW = datetime.now(JST)

PAST_DAYS = 30
FUTURE_DAYS = 365

UA = "Mozilla/5.0 (compatible; GameMaintenanceCalendar/4.0)"
S = requests.Session()
S.headers.update({
    "User-Agent": UA,
    "Accept-Language": "ja,en;q=0.8",
})

GAMES = {
    "NTE": {
        "indexes": [
            "https://nte.perfectworld.com/jp/article/news/gamenews/index.html",
            "https://nte.perfectworld.com/jp/article/news/gamenews/index1.html",
            "https://nte.perfectworld.com/jp/article/news/gamenews/index2.html",
        ],
        "domains": ["nte.perfectworld.com"],
        "parser": "nte",
    },
    "エンドフィールド": {
        "indexes": ["https://endfield.gryphline.com/ja-jp/news"],
        "domains": ["endfield.gryphline.com"],
        "parser": "endfield",
    },
    "原神": {
        "indexes": [
            "https://genshin.hoyoverse.com/ja/news",
            "https://genshin.hoyoverse.com/ja-jp/news",
        ],
        "domains": ["genshin.hoyoverse.com"],
        "parser": "generic",
    },
    "崩壊：スターレイル": {
        "indexes": [
            "https://hsr.hoyoverse.com/ja-jp/news",
            "https://hsr.hoyoverse.com/ja/news",
        ],
        "domains": ["hsr.hoyoverse.com"],
        "parser": "generic",
    },
    "ステラソラ": {
        "indexes": ["https://www.stellasora.jp/news/"],
        "domains": ["stellasora.jp", "www.stellasora.jp"],
        "parser": "generic",
    },
    "ドルフィンウェーブ": {
        "indexes": [
            "https://news-dolphin-wave.marv.jp/category/1",
            "https://news-dolphin-wave.marv.jp/",
        ],
        "domains": ["news-dolphin-wave.marv.jp"],
        "parser": "generic",
    },
    "ブルーアーカイブ": {
        "indexes": ["https://bluearchive.jp/news/"],
        "domains": ["bluearchive.jp"],
        "parser": "generic",
    },
    "NIKKE": {
        "indexes": [
            "https://nikke-jp.com/news.html",
            "https://nikke-jp.com/news_m.html",
        ],
        "domains": ["nikke-jp.com"],
        "parser": "generic",
    },
    "鳴潮": {
        "indexes": ["https://wutheringwaves.kurogames.com/jp/announcement"],
        "domains": ["wutheringwaves.kurogames.com"],
        "parser": "generic",
    },
}

MAINT_WORDS = (
    "メンテナンス",
    "maintenance",
    "サーバーメンテナンス",
    "臨時メンテ",
    "メンテ実施",
)

DATE = r"(20\d{2})?[./年\-](\d{1,2})[./月\-](\d{1,2})日?"
JDATE = r"(20\d{2})?\s*年?\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?"
TIME = r"(\d{1,2})\s*[:：]\s*(\d{2})"

# Full explicit range. The end date is optional, but if omitted it means same date.
FULL_RANGE = re.compile(
    rf"(?P<d1>{JDATE})\s*(?P<t1>{TIME})\s*"
    rf"(?:～|〜|~|−|–|—|-|－|から)\s*"
    rf"(?:(?P<d2>{JDATE})\s*)?(?P<t2>{TIME})",
    re.I,
)

# ISO-ish dates often used by Endfield.
ISO_RANGE = re.compile(
    rf"(?P<d1>{DATE})\s*(?P<t1>{TIME})\s*"
    rf"(?:～|〜|~|−|–|—|-|－|から)\s*"
    rf"(?:(?P<d2>{DATE})\s*)?(?P<t2>{TIME})",
    re.I,
)

def fetch(url: str) -> str:
    r = S.get(url, timeout=30)
    r.raise_for_status()
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding or "utf-8"
    return r.text

def clean(soup: BeautifulSoup) -> str:
    for x in soup(["script", "style", "noscript", "svg"]):
        x.decompose()
    t = soup.get_text("\n", strip=True)
    t = html.unescape(t)
    t = t.replace("\u3000", " ")
    return re.sub(r"[ \t]+", " ", t)

def title_of(soup: BeautifulSoup) -> str:
    for x in soup.find_all(["h1", "h2"], limit=3):
        t = x.get_text(" ", strip=True)
        if t:
            return re.sub(r"\s+", " ", t)
    if soup.title:
        return re.sub(r"\s+", " ", soup.title.get_text(" ", strip=True))
    return ""

def discover(indexes, domains):
    out = set()
    for idx in indexes:
        try:
            soup = BeautifulSoup(fetch(idx), "html.parser")
        except Exception as e:
            print(f"[WARN] index failed: {idx}: {e}")
            continue
        for a in soup.find_all("a", href=True):
            u = urljoin(idx, a["href"]).split("#")[0]
            host = urlparse(u).netloc.lower()
            if u.startswith(("http://", "https://")) and any(
                host == d or host.endswith("." + d) for d in domains
            ):
                out.add(u)
    return sorted(out)

def year_from(text: str) -> int:
    m = re.search(r"(20\d{2})\s*年", text)
    return int(m.group(1)) if m else NOW.year

def build_dt(year, month, day, hour, minute, tz):
    try:
        return datetime(year, month, day, hour, minute, tzinfo=tz)
    except ValueError:
        return None

def parse_match(m, source_text, tz):
    gd = m.groupdict()
    d1 = re.search(JDATE, gd["d1"]) or re.search(DATE, gd["d1"])
    t1 = re.search(TIME, gd["t1"])
    d2 = None
    if gd.get("d2"):
        d2 = re.search(JDATE, gd["d2"]) or re.search(DATE, gd["d2"])
    t2 = re.search(TIME, gd["t2"])
    if not d1 or not t1 or not t2:
        return None

    # Both JDATE and DATE expose year, month, day in groups 1,2,3.
    y = int(d1.group(1)) if d1.group(1) else year_from(source_text)
    mo = int(d1.group(2))
    day = int(d1.group(3))
    if d2:
        y2 = int(d2.group(1)) if d2.group(1) else y
        mo2 = int(d2.group(2))
        day2 = int(d2.group(3))
    else:
        y2, mo2, day2 = y, mo, day

    start = build_dt(y, mo, day, int(t1.group(1)), int(t1.group(2)), tz)
    end = build_dt(y2, mo2, day2, int(t2.group(1)), int(t2.group(2)), tz)
    if not start or not end:
        return None
    if end <= start:
        # Only roll to next day when the end date was omitted.
        if not d2:
            end += timedelta(days=1)
        else:
            return None
    # Refuse absurd durations caused by accidental parsing.
    if end - start > timedelta(hours=18):
        return None
    return start, end

def parse_labeled_range(text: str, tz):
    """
    Look only immediately after a maintenance-time label.
    This is the key fix for the previous false positives.
    """
    patterns = [
        r"メンテナンス時間\s*[:：]\s*",
        r"メンテナンス実施日時\s*[:：]?\s*",
        r"メンテナンス実施時間\s*[:：]?\s*",
        r"メンテナンス日時\s*[:：]?\s*",
        r"実施時間\s*[:：]\s*",
        r"maintenance\s*(?:time|period)\s*[:：]?\s*",
    ]
    results = []
    for pat in patterns:
        for m in re.finditer(pat, text, re.I):
            # Do NOT search hundreds of characters. 120 chars is enough for a
            # real time field and prevents event periods later in the article.
            snippet = text[m.end():m.end() + 160]
            r = FULL_RANGE.search(snippet) or ISO_RANGE.search(snippet)
            if r:
                parsed = parse_match(r, snippet, tz)
                if parsed:
                    results.append(parsed)
    return list(dict.fromkeys(results))

def parse_endfield(text: str):
    # Only the Asia server row in the maintenance section.
    anchors = [
        "メンテナンス実施日時",
        "メンテナンス実施時間",
        "アップデートメンテナンス",
        "実施時間",
    ]
    sections = []
    for a in anchors:
        for m in re.finditer(re.escape(a), text, re.I):
            sections.append(text[m.start():m.start() + 1800])

    results = []
    for sec in sections:
        # Prefer the Asia row; never parse Americas/Europe.
        for m in re.finditer(r"Asia\s*サーバー\s*[:：]?\s*", sec, re.I):
            snippet = sec[m.end():m.end() + 180]
            r = ISO_RANGE.search(snippet) or FULL_RANGE.search(snippet)
            if r:
                parsed = parse_match(r, snippet, UTC8)
                if parsed:
                    results.append(parsed)
    return list(dict.fromkeys(results))

def parse_nte(text: str):
    # NTE has a very stable explicit field.
    return parse_labeled_range(text, JST)

def is_maintenance_article(title, text):
    low_title = title.lower()
    if any(w.lower() in low_title for w in MAINT_WORDS):
        return True
    # Body must contain both a maintenance term and an explicit time label.
    return (
        any(w.lower() in text.lower() for w in MAINT_WORDS)
        and bool(re.search(
            r"メンテナンス(?:時間|実施日時|実施時間|日時)|実施時間|maintenance\s*(?:time|period)",
            text, re.I
        ))
    )

def event(game, start, end, url, article_title):
    uid = hashlib.sha1(
        f"{game}|{url}|{start.isoformat()}|{end.isoformat()}".encode()
    ).hexdigest() + "@game-maintenance-calendar"
    return {
        "uid": uid,
        "title": f"{game} メンテナンス",
        "start": start,
        "end": end,
        "url": url,
        "desc": f"公式告知: {article_title}\n{url}",
    }

def scrape(game, cfg):
    links = discover(cfg["indexes"], cfg["domains"])
    print(f"[INFO] {game}: {len(links)} links")
    out = []
    for url in links:
        try:
            soup = BeautifulSoup(fetch(url), "html.parser")
            title = title_of(soup)
            text = clean(soup)

            if not is_maintenance_article(title, text):
                continue

            if cfg["parser"] == "nte":
                ranges = parse_nte(text)
            elif cfg["parser"] == "endfield":
                ranges = parse_endfield(text)
            else:
                ranges = parse_labeled_range(text, JST)

            for start, end in ranges:
                # Future/past filtering happens later; print in JST for debugging.
                print(
                    f"[FOUND] {game}: "
                    f"{start.astimezone(JST):%Y-%m-%d %H:%M} - "
                    f"{end.astimezone(JST):%Y-%m-%d %H:%M}"
                )
                out.append(event(game, start, end, url, title))
        except Exception as e:
            print(f"[WARN] {game}: {url}: {e}")
        time.sleep(0.05)
    return out

def esc(s):
    return (
        s.replace("\\", "\\\\")
         .replace(";", "\\;")
         .replace(",", "\\,")
         .replace("\n", "\\n")
         .replace("\r", "")
    )

def fold(line):
    # RFC5545-ish 75-byte folding.
    parts, cur, n = [], "", 0
    for ch in line:
        b = len(ch.encode("utf-8"))
        if n + b > 75:
            parts.append(cur)
            cur = " " + ch
            n = 1 + b
        else:
            cur += ch
            n += b
    parts.append(cur)
    return "\r\n".join(parts)

def make_ics(events):
    now = datetime.now(timezone.utc)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Game Maintenance Calendar//JP//",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:ゲーム メンテナンス",
        "X-WR-TIMEZONE:Asia/Tokyo",
    ]

    for e in sorted(events, key=lambda x: x["start"]):
        lines += [
            "BEGIN:VEVENT",
            f"UID:{e['uid']}",
            f"DTSTAMP:{now:%Y%m%dT%H%M%SZ}",
            # UTC storage makes Google Calendar conversion unambiguous.
            f"DTSTART:{e['start'].astimezone(timezone.utc):%Y%m%dT%H%M%SZ}",
            f"DTEND:{e['end'].astimezone(timezone.utc):%Y%m%dT%H%M%SZ}",
            f"SUMMARY:{esc(e['title'])}",
            f"DESCRIPTION:{esc(e['desc'])}",
            f"URL:{e['url']}",
            "STATUS:CONFIRMED",
            "TRANSP:OPAQUE",
            "END:VEVENT",
        ]

    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(x) for x in lines) + "\r\n"

def main():
    all_events = []
    for game, cfg in GAMES.items():
        all_events.extend(scrape(game, cfg))

    low = NOW - timedelta(days=PAST_DAYS)
    high = NOW + timedelta(days=FUTURE_DAYS)

    # Deduplicate by UID and reject anything outside our safe time window.
    events = {
        e["uid"]: e
        for e in all_events
        if low <= e["start"].astimezone(JST) <= high
    }

    if not events:
        raise RuntimeError(
            "No safely parseable maintenance events were found. "
            "The official page layout may have changed; no fake events were created."
        )

    with open("calendar.ics", "w", encoding="utf-8", newline="") as f:
        f.write(make_ics(list(events.values())))

    print(f"[OK] calendar.ics generated: {len(events)} event(s)")

if __name__ == "__main__":
    main()
