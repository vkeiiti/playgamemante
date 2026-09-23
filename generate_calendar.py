import hashlib, re, time
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

JST=timezone(timedelta(hours=9)); UTC8=timezone(timedelta(hours=8))
NOW=datetime.now(JST); PAST=30; FUTURE=370
S=requests.Session()
S.headers.update({"User-Agent":"Mozilla/5.0 (compatible; GameMaintenanceCalendar/5.0)","Accept-Language":"ja,en;q=0.8"})

SOURCES={
"nte":("NTE",["https://nte.perfectworld.com/jp/article/news/gamenews/index.html"],["nte.perfectworld.com"],"nte"),
"endfield":("エンドフィールド",["https://endfield.gryphline.com/ja-jp/news"],["endfield.gryphline.com"],"endfield"),
"genshin":("原神",["https://genshin.hoyoverse.com/ja-jp/news","https://genshin.hoyoverse.com/ja/news"],["genshin.hoyoverse.com"],"hoyoverse"),
"hsr":("崩壊：スターレイル",["https://hsr.hoyoverse.com/ja-jp/news","https://hsr.hoyoverse.com/ja/news"],["hsr.hoyoverse.com"],"hoyoverse"),
"stellasora":("ステラソラ",["https://www.stellasora.jp/news/"],["stellasora.jp"],"stellasora"),
"dolphinwave":("ドルフィンウェーブ",["https://news-dolphin-wave.marv.jp/","https://news-dolphin-wave.marv.jp/category/1"],["news-dolphin-wave.marv.jp"],"dolphin"),
"bluearchive":("ブルーアーカイブ",["https://bluearchive.jp/news/"],["bluearchive.jp"],"bluearchive"),
"nikke":("NIKKE",["https://nikke-jp.com/news.html","https://nikke-jp.com/news_m.html"],["nikke-jp.com"],"nikke"),
"wutheringwaves":("鳴潮",["https://wutheringwaves.kurogames.com/jp/announcement"],["wutheringwaves.kurogames.com"],"ww"),
}

DATE=r"(?:(20\d{2})\s*[年/.\-])?\s*(\d{1,2})\s*[月/.\-]\s*(\d{1,2})\s*日?"
ISO=r"(20\d{2})[/-](\d{1,2})[/-](\d{1,2})"
TM=r"(\d{1,2})\s*[:：]\s*(\d{2})"
SEP=r"(?:～|〜|~|–|—|−|－|-|から)"
FULL=re.compile(rf"(?P<a>{DATE})\s*(?P<b>{TM})\s*{SEP}\s*(?:(?P<c>{DATE})\s*)?(?P<d>{TM})",re.I)
ISOR=re.compile(rf"(?P<a>{ISO})\s*(?P<b>{TM})\s*{SEP}\s*(?:(?P<c>{ISO})\s*)?(?P<d>{TM})",re.I)

def get(u):
    r=S.get(u,timeout=30); r.raise_for_status()
    r.encoding=r.encoding or r.apparent_encoding or "utf-8"; return r.text

def text(html):
    s=BeautifulSoup(html,"html.parser")
    for x in s(["script","style","noscript","svg"]): x.decompose()
    return re.sub(r"[ \t]+"," ",s.get_text("\n",strip=True).replace("\u3000"," ")), s

def title(s):
    for x in s.find_all(["h1","h2"],limit=5):
        q=re.sub(r"\s+"," ",x.get_text(" ",strip=True))
        if q:return q
    return re.sub(r"\s+"," ",s.title.get_text(" ",strip=True)) if s.title else ""

def host_ok(u,domains):
    h=urlparse(u).netloc.lower()
    return any(h==d or h.endswith("."+d) for d in domains)

def discover(indexes,domains):
    out=set(indexes)
    for idx in indexes:
        try: html=get(idx)
        except Exception as e:
            print("[WARN] index",idx,e); continue
        _,s=text(html)
        for a in s.find_all("a",href=True):
            u=urljoin(idx,a["href"]).split("#")[0]
            if u.startswith(("http://","https://")) and host_ok(u,domains): out.add(u)
    return sorted(out)

def parts(ds):
    m=re.search(ISO,ds)
    if m:return int(m.group(1)),int(m.group(2)),int(m.group(3))
    m=re.search(DATE,ds)
    if not m:return None
    return (int(m.group(1)) if m.group(1) else None,int(m.group(2)),int(m.group(3)))

def tp(ts):
    m=re.search(TM,ts); return (int(m.group(1)),int(m.group(2))) if m else None

def parse(m,s,tz):
    p1=parts(m.group("a")); p2=parts(m.group("c")) if m.group("c") else None
    t1=tp(m.group("b")); t2=tp(m.group("d"))
    if not p1 or not t1 or not t2:return None
    y=p1[0] or (int(re.search(r"(20\d{2})\s*年",s).group(1)) if re.search(r"(20\d{2})\s*年",s) else NOW.year)
    start=datetime(y,p1[1],p1[2],t1[0],t1[1],tz)
    if p2:
        end=datetime(p2[0] or y,p2[1],p2[2],t2[0],t2[1],tz)
    else:end=datetime(y,p1[1],p1[2],t2[0],t2[1],tz)
    if end<=start:
        if not p2:end+=timedelta(days=1)
        else:return None
    if end-start>timedelta(hours=18):return None
    return start,end

def ranges(s,tz):
    out=[]
    for pat in (ISOR,FULL):
        for m in pat.finditer(s):
            try:r=parse(m,s,tz)
            except Exception:r=None
            if r:out.append(r)
    return list(dict.fromkeys(out))

def windows(s,labels,n=280):
    out=[]
    for label in labels:
        for m in re.finditer(label,s,re.I):out.append(s[m.end():m.end()+n])
    return out

def labeled(s,tz):
    labels=(r"メンテナンス時間\s*[:：]?",r"メンテナンス実施日時\s*[:：]?",r"メンテナンス実施時間\s*[:：]?",r"メンテナンス日時\s*[:：]?",r"maintenance\s*(?:time|period)\s*[:：]?")
    out=[]
    for w in windows(s,labels):out+=ranges(w,tz)
    return list(dict.fromkeys(out))

def parse_game(kind,s):
    if kind=="nte": return labeled(s,JST)
    if kind=="endfield":
        out=[]
        for m in re.finditer(r"■\s*メンテナンス実施日時|メンテナンス実施日時",s,re.I):
            sec=s[m.end():m.end()+1000]
            a=re.search(r"Asia\s*サーバー\s*[:：]?\s*(.*?)(?:Americas|Europe|※|$)",sec,re.I|re.S)
            if a:out+=ranges(a.group(1),UTC8)
        return list(dict.fromkeys(out))
    if kind=="hoyoverse": return labeled(s,JST)
    if kind=="stellasora":
        out=labeled(s,JST)
        for m in re.finditer(r"メンテナンス(?:を|のため).*?(?:。|※|$)",s,re.I|re.S):
            out+=ranges(m.group(0)[:350],JST)
        return list(dict.fromkeys(out))
    if kind=="dolphin": return labeled(s,JST)
    if kind=="bluearchive": return labeled(s,JST)
    if kind=="nikke": return labeled(s,JST)
    if kind=="ww": return labeled(s,UTC8)
    return []

def event(game,url,start,end,ttl):
    uid=hashlib.sha256(f"{game}|{url}|{start.isoformat()}|{end.isoformat()}".encode()).hexdigest()+"@game-maintenance"
    return {"uid":uid,"summary":f"{game} メンテナンス","start":start,"end":end,"url":url,"ttl":ttl}

def scrape(key,conf):
    name,indexes,domains,kind=conf; out=[]
    for u in discover(indexes,domains):
        try:
            html=get(u); s,bs=text(html); ttl=title(bs)
            if "メンテ" not in (ttl+"\n"+s) and "maintenance" not in (ttl+"\n"+s).lower(): continue
            for st,en in parse_game(kind,s):
                print(f"[FOUND] {name}: {st.astimezone(JST):%Y-%m-%d %H:%M} - {en.astimezone(JST):%Y-%m-%d %H:%M}")
                out.append(event(name,u,st,en,ttl))
        except Exception as e: print("[WARN]",name,u,e)
        time.sleep(.03)
    return out

def esc(x): return x.replace("\\","\\\\").replace(";","\\;").replace(",","\\,").replace("\n","\\n").replace("\r","")

def fold(x):
    chunks=[]; cur=""; n=0
    for c in x:
        b=len(c.encode())
        if cur and n+b>75: chunks.append(cur); cur=" "+c; n=1+b
        else: cur+=c; n+=b
    chunks.append(cur); return "\r\n".join(chunks)

def ics(events):
    lines=["BEGIN:VCALENDAR","VERSION:2.0","PRODID:-//Game Maintenance Calendar v5//JP//","CALSCALE:GREGORIAN","METHOD:PUBLISH","X-WR-CALNAME:ゲーム メンテナンス","X-WR-TIMEZONE:Asia/Tokyo"]
    stamp=datetime.now(timezone.utc)
    for e in sorted(events,key=lambda x:x["start"]):
        lines += ["BEGIN:VEVENT",f"UID:{e['uid']}",f"DTSTAMP:{stamp:%Y%m%dT%H%M%SZ}",f"DTSTART:{e['start'].astimezone(timezone.utc):%Y%m%dT%H%M%SZ}",f"DTEND:{e['end'].astimezone(timezone.utc):%Y%m%dT%H%M%SZ}",f"SUMMARY:{esc(e['summary'])}",f"DESCRIPTION:{esc('公式告知: '+e['ttl']+'\\n'+e['url'])}",f"URL:{e['url']}","STATUS:CONFIRMED","TRANSP:OPAQUE","END:VEVENT"]
    lines.append("END:VCALENDAR"); return "\r\n".join(fold(x) for x in lines)+"\r\n"

def main():
    all=[]
    for k,c in SOURCES.items(): all+=scrape(k,c)
    lo=NOW-timedelta(days=PAST); hi=NOW+timedelta(days=FUTURE)
    uniq={e["uid"]:e for e in all if lo<=e["start"].astimezone(JST)<=hi}
    if not uniq: raise SystemExit("No safely parsed maintenance events; calendar.ics was not changed.")
    open("calendar.ics","w",encoding="utf-8",newline="").write(ics(list(uniq.values())))
    print(f"[OK] {len(uniq)} events written.")

if __name__=="__main__": main()
