#!/usr/bin/env python3
"""
Game Maintenance Calendar v6

Design:
- Each game has its own article discovery rules and parser.
- Never guesses a maintenance time from arbitrary dates.
- "Event period / banner period / campaign period" are not parsed.
- Each game reports diagnostics to the GitHub Actions log.
- One game's failure does not stop the other games.
- If no event is safely parsed, calendar.ics is left untouched.

Python 3.12+
Dependencies: requests, beautifulsoup4
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

JST = timezone(timedelta(hours=9))
UTC8 = timezone(timedelta(hours=8))
NOW = datetime.now(JST)

LOOKBACK_DAYS = 14
LOOKAHEAD_DAYS = 400

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; GameMaintenanceCalendar/6.0; +https://github.com/)",
    "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.7",
})

@dataclass(frozen=True)
class Game:
    key: str
    name: str
    indexes: tuple[str, ...]
    domains: tuple[str, ...]
    parser: str

GAMES = (
    Game("nte", "NTE",
         ("https://nte.perfectworld.com/jp/article/news/gamenews/index.html",),
         ("nte.perfectworld.com",), "nte"),

    Game("endfield", "エンドフィールド",
         ("https://endfield.gryphline.com/ja-jp/news",
          "https://endfield.gryphline.com/news"),
         ("endfield.gryphline.com",), "endfield"),

    Game("genshin", "原神",
         ("https://genshin.hoyoverse.com/ja-jp/news",
          "https://genshin.hoyoverse.com/ja/news"),
         ("genshin.hoyoverse.com",), "hoyoverse"),

    Game("hsr", "崩壊：スターレイル",
         ("https://hsr.hoyoverse.com/ja-jp/news",
          "https://hsr.hoyoverse.com/ja/news"),
         ("hsr.hoyoverse.com",), "hoyoverse"),

    Game("stellasora", "ステラソラ",
         ("https://www.stellasora.jp/news/",
          "https://stellasora.jp/news/"),
         ("stellasora.jp", "www.stellasora.jp"), "stellasora"),

    Game("dolphinwave", "ドルフィンウェーブ",
         ("https://news-dolphin-wave.marv.jp/",
          "https://news-dolphin-wave.marv.jp/category/1"),
         ("news-dolphin-wave.marv.jp",), "dolphin"),

    Game("bluearchive", "ブルーアーカイブ",
         ("https://bluearchive.jp/news/",),
         ("bluearchive.jp",), "bluearchive"),

    Game("nikke", "NIKKE",
         ("https://nikke-jp.com/news.html",
          "https://nikke-jp.com/news_m.html"),
         ("nikke-jp.com",), "nikke"),

    Game("wutheringwaves", "鳴潮",
         ("https://wutheringwaves.kurogames.com/jp/announcement",
          "https://wutheringwaves.kurogames.com/jp/news"),
         ("wutheringwaves.kurogames.com",), "wutheringwaves"),
)

MAINT_TITLE = re.compile(
    r"(メンテナンス|メンテ|maintenance|update\s*&\s*maintenance|"
    r"update\s+and\s+maintenance|maintenance\s+notice)",
    re.I
)

# Explicit labels. These are deliberately narrow.
LABELS = (
    r"メンテナンス実施日時",
    r"メンテナンス実施時間",
    r"メンテナンス時間",
    r"メンテナンス日時",
    r"メンテナンス\s*期間",
    r"maintenance\s+(?:date|time|period)",
    r"maintenance\s+schedule",
)

DATE = r"(?:(20\d{2})\s*[年/.\-])?\s*(\d{1,2})\s*[月/.\-]\s*(\d{1,2})\s*日?"
ISO = r"(20\d{2})[/-](\d{1,2})[/-](\d{1,2})"
TIME = r"(\d{1,2})\s*[:：]\s*(\d{2})"
SEP = r"(?:～|〜|~|–|—|−|－|-|から|to)"
FULL_RANGE = re.compile(
    rf"(?P<d1>{DATE})\s*(?P<t1>{TIME})\s*{SEP}\s*"
    rf"(?:(?P<d2>{DATE})\s*)?(?P<t2>{TIME})", re.I)
ISO_RANGE = re.compile(
    rf"(?P<d1>{ISO})\s*(?P<t1>{TIME})\s*{SEP}\s*"
    rf"(?:(?P<d2>{ISO})\s*)?(?P<t2>{TIME})", re.I)

def get(url: str) -> str:
    r = SESSION.get(url, timeout=30)
    r.raise_for_status()
    r.encoding = r.encoding or r.apparent_encoding or "utf-8"
    return r.text

def clean_html(html: str):
    soup = BeautifulSoup(html, "html.parser")
    for x in soup(["script", "style", "noscript", "svg", "template"]):
        x.decompose()
    text = soup.get_text("\n", strip=True).replace("\u3000", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return text, soup

def page_title(soup: BeautifulSoup) -> str:
    for tag in soup.find_all(["h1", "h2"], limit=8):
        s = re.sub(r"\s+", " ", tag.get_text(" ", strip=True))
        if s:
            return s
    return soup.title.get_text(" ", strip=True) if soup.title else ""

def same_domain(url: str, domains: tuple[str, ...]) -> bool:
    host = urlparse(url).netloc.lower()
    return any(host == d or host.endswith("." + d) for d in domains)

def discover(game: Game):
    urls = set(game.indexes)
    fetched = 0
    for index in game.indexes:
        try:
            html = get(index)
            fetched += 1
        except Exception as e:
            print(f"[{game.name}] INDEX_FAIL {index} :: {e}")
            continue

        _, soup = clean_html(html)
        for a in soup.find_all("a", href=True):
            u = urljoin(index, a["href"]).split("#")[0]
            if u.startswith(("http://", "https://")) and same_domain(u, game.domains):
                urls.add(u)

        # Discover sitemap links if the index exposes them.
        for tag in soup.find_all(["link", "meta"]):
            u = tag.get("href") or tag.get("content")
            if u and same_domain(u, game.domains) and (
                "sitemap" in u.lower() or "news" in u.lower()
            ):
                urls.add(urljoin(index, u))

    # Try robots.txt -> sitemap without depending on it.
    base = f"{urlparse(game.indexes[0]).scheme}://{urlparse(game.indexes[0]).netloc}"
    try:
        robots = get(base + "/robots.txt")
        for line in robots.splitlines():
            if line.lower().startswith("sitemap:"):
                u = line.split(":", 1)[1].strip()
                if same_domain(u, game.domains):
                    urls.add(u)
    except Exception:
        pass

    return sorted(urls), fetched

def parse_date(s: str):
    m = re.search(ISO, s)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    m = re.search(DATE, s)
    if not m:
        return None
    return (
        int(m.group(1)) if m.group(1) else None,
        int(m.group(2)),
        int(m.group(3)),
    )

def parse_time(s: str):
    m = re.search(TIME, s)
    return (int(m.group(1)), int(m.group(2))) if m else None

def year_from(text: str) -> int:
    m = re.search(r"(20\d{2})\s*年", text)
    return int(m.group(1)) if m else NOW.year

def parse_range(m, context: str, tz):
    d1 = parse_date(m.group("d1"))
    d2 = parse_date(m.group("d2")) if m.group("d2") else None
    t1 = parse_time(m.group("t1"))
    t2 = parse_time(m.group("t2"))
    if not d1 or not t1 or not t2:
        return None

    y1 = d1[0] or year_from(context)
    y2 = (d2[0] if d2 and d2[0] else y1)

    try:
        start = datetime(y1, d1[1], d1[2], t1[0], t1[1], tzinfo=tz)
        if d2:
            end = datetime(y2, d2[1], d2[2], t2[0], t2[1], tzinfo=tz)
        else:
            end = datetime(y1, d1[1], d1[2], t2[0], t2[1], tzinfo=tz)
    except ValueError:
        return None

    if end <= start:
        if not d2:
            end += timedelta(days=1)
        else:
            return None

    # A normal game maintenance should not become a multi-day event.
    if end - start > timedelta(hours=18):
        return None
    return start, end

def ranges(text: str, tz):
    found = []
    for pat in (ISO_RANGE, FULL_RANGE):
        for m in pat.finditer(text):
            r = parse_range(m, text, tz)
            if r:
                found.append(r)
    return list(dict.fromkeys(found))

def after_labels(text: str, labels=LABELS, window=360):
    out = []
    for label in labels:
        for m in re.finditer(label, text, re.I):
            out.append(text[m.end():m.end() + window])
    return out

def explicit_parser(text: str, tz):
    out = []
    for w in after_labels(text):
        out.extend(ranges(w, tz))
    return list(dict.fromkeys(out))

# ---- Individual parsers ----

def parse_nte(text):
    # NTE: only explicit maintenance-time fields.
    return explicit_parser(text, JST)

def parse_endfield(text):
    # Endfield: ONLY Asia server. The published server schedule is UTC+8.
    out = []
    anchors = (
        r"メンテナンス実施日時",
        r"maintenance\s+(?:date|time|schedule)",
    )
    for a in anchors:
        for m in re.finditer(a, text, re.I):
            section = text[m.end():m.end()+1200]
            asia = re.search(
                r"Asia\s*(?:サーバー|Server)\s*[:：]?\s*(.*?)(?="
                r"(?:Americas|Europe|日本|※|$))",
                section, re.I | re.S
            )
            if asia:
                out.extend(ranges(asia.group(1), UTC8))
    return list(dict.fromkeys(out))

def parse_hoyoverse(text):
    # Genshin / HSR: maintenance-specific fields only.
    return explicit_parser(text, JST)

def parse_stellasora(text):
    # Do not parse generic "開催期間". Look for maintenance sentence/field.
    out = explicit_parser(text, JST)
    for m in re.finditer(
        r"メンテナンス[^。\n]{0,260}(?:。|$)", text, re.I | re.S
    ):
        snippet = m.group(0)
        # Require a time in the same short maintenance sentence.
        if TIME.search(snippet):
            out.extend(ranges(snippet, JST))
    return list(dict.fromkeys(out))

def parse_dolphin(text):
    return explicit_parser(text, JST)

def parse_bluearchive(text):
    return explicit_parser(text, JST)

def parse_nikke(text):
    # NIKKE Japanese/English maintenance labels.
    return explicit_parser(text, JST)

def parse_wutheringwaves(text):
    # Kuro's global announcements commonly use UTC+8.
    return explicit_parser(text, UTC8)

PARSERS = {
    "nte": parse_nte,
    "endfield": parse_endfield,
    "hoyoverse": parse_hoyoverse,
    "stellasora": parse_stellasora,
    "dolphin": parse_dolphin,
    "bluearchive": parse_bluearchive,
    "nikke": parse_nikke,
    "wutheringwaves": parse_wutheringwaves,
}

def is_article_candidate(title: str, text: str) -> bool:
    # Title is weighted heavily. This prevents event/news pages containing
    # the word "maintenance" in a footer from being parsed.
    if MAINT_TITLE.search(title):
        return True
    head = text[:2500]
    return bool(MAINT_TITLE.search(head))

def make_event(game: Game, url: str, start, end, title: str):
    uid = hashlib.sha256(
        f"{game.key}|{url}|{start.isoformat()}|{end.isoformat()}".encode()
    ).hexdigest() + "@game-maintenance-calendar"
    return {
        "uid": uid,
        "summary": f"{game.name} メンテナンス",
        "start": start,
        "end": end,
        "url": url,
        "title": title,
    }

def scrape(game: Game):
    urls, index_count = discover(game)
    parser = PARSERS[game.parser]
    events = []
    candidate_count = 0
    parse_fail_count = 0

    print(f"\n===== {game.name} =====")
    print(f"index_ok={index_count} discovered_urls={len(urls)}")

    # We don't need to scrape every arbitrary page forever. Maintenance
    # announcements normally appear among the newest pages; however the
    # look-ahead period is large enough to catch announced schedules.
    for url in urls:
        try:
            html = get(url)
            text, soup = clean_html(html)
            title = page_title(soup)

            if not is_article_candidate(title, text):
                continue

            candidate_count += 1
            found = parser(text)

            if not found:
                parse_fail_count += 1
                print(f"[{game.name}] PARSE_SKIP :: {title[:100]} :: {url}")
                continue

            for start, end in found:
                ls = start.astimezone(JST)
                le = end.astimezone(JST)
                if ls < NOW - timedelta(days=LOOKBACK_DAYS):
                    continue
                if ls > NOW + timedelta(days=LOOKAHEAD_DAYS):
                    continue
                events.append(make_event(game, url, start, end, title))
                print(
                    f"[{game.name}] FOUND "
                    f"{ls:%Y-%m-%d %H:%M} - {le:%Y-%m-%d %H:%M} :: {url}"
                )

        except Exception as e:
            print(f"[{game.name}] ARTICLE_FAIL {url} :: {e}")
        time.sleep(0.03)

    print(
        f"[{game.name}] RESULT events={len(events)} "
        f"maintenance_candidates={candidate_count} "
        f"parse_skips={parse_fail_count}"
    )
    return events

def esc(s: str) -> str:
    return (s.replace("\\", "\\\\").replace(";", "\\;")
             .replace(",", "\\,").replace("\r", "").replace("\n", "\\n"))

def fold(line: str) -> str:
    chunks, cur, size = [], "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        if cur and size + n > 75:
            chunks.append(cur)
            cur = " " + ch
            size = 1 + n
        else:
            cur += ch
            size += n
    chunks.append(cur)
    return "\r\n".join(chunks)

def make_ics(events):
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Game Maintenance Calendar v6//JP//",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:ゲーム メンテナンス",
        "X-WR-TIMEZONE:Asia/Tokyo",
    ]
    stamp = datetime.now(timezone.utc)

    for e in sorted(events, key=lambda x: x["start"]):
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{e['uid']}",
            f"DTSTAMP:{stamp:%Y%m%dT%H%M%SZ}",
            f"DTSTART:{e['start'].astimezone(timezone.utc):%Y%m%dT%H%M%SZ}",
            f"DTEND:{e['end'].astimezone(timezone.utc):%Y%m%dT%H%M%SZ}",
            f"SUMMARY:{esc(e['summary'])}",
            f"DESCRIPTION:{esc('公式告知: ' + e['title'] + chr(10) + e['url'])}",
            f"URL:{e['url']}",
            "STATUS:CONFIRMED",
            "TRANSP:OPAQUE",
            "END:VEVENT",
        ])

    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(x) for x in lines) + "\r\n"

def main():
    all_events = []

    for game in GAMES:
        try:
            all_events.extend(scrape(game))
        except Exception as e:
            print(f"[{game.name}] FATAL_GAME_ERROR :: {e}")

    # Deduplicate.
    unique = {e["uid"]: e for e in all_events}

    print("\n===== FINAL DIAGNOSTIC =====")
    for game in GAMES:
        count = sum(1 for e in unique.values() if e["summary"] == f"{game.name} メンテナンス")
        print(f"{game.name}: {count}")

    if not unique:
        print("NO_SAFE_EVENTS: calendar.ics was NOT modified.")
        return

    with open("calendar.ics", "w", encoding="utf-8", newline="") as f:
        f.write(make_ics(list(unique.values())))

    print(f"SUCCESS: wrote {len(unique)} events to calendar.ics")

if __name__ == "__main__":
    main()
