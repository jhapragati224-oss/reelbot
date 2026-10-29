"""Finds fresh stories from your sources (RSS links or keywords), ranks them, and asks you to approve."""
import re, json, pathlib, datetime, email.utils, requests
import xml.etree.ElementTree as ET
from urllib.parse import quote
import bot, studio

UA = {"User-Agent": "Mozilla/5.0"}
BUILTIN = ["https://news.google.com/rss?hl=en-IN&gl=IN&ceid=IN:en",   # Google News top stories, India
           "https://trends.google.com/trending/rss?geo=IN"]           # Google Trends, India (search spikes)


def fetch(src, hours):
    url = src if src.startswith("http") else (
        "https://news.google.com/rss/search?q=" + quote(f"{src} when:{hours}h") + "&hl=en-IN&gl=IN&ceid=IN:en")
    root = ET.fromstring(requests.get(url, headers=UA, timeout=30).content)
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)
    out = []
    for it in root.iter("item"):
        try:
            fresh = email.utils.parsedate_to_datetime(it.findtext("pubDate")) >= cutoff
        except Exception:
            fresh = True
        title = (it.findtext("title") or "").strip()
        if not (fresh and title):
            continue
        for ch in it.iter():
            tag = ch.tag.rsplit("}", 1)[-1]
            if tag == "approx_traffic" and ch.text:
                title += f" [trending: {ch.text.strip()} searches]"
            elif tag == "news_item_title" and ch.text and " | " not in title:
                title += f" | {ch.text.strip()}"
        label = "Google Trends India" if "trends.google" in url else (it.findtext("source") or src)[:60]
        out.append({"title": title, "src": label})
    return out[:15]


def yt_trending():
    key = bot.E.get("YT_API_KEY")
    if not key:
        return []
    r = requests.get("https://www.googleapis.com/youtube/v3/videos", timeout=30, params={
        "part": "snippet,statistics", "chart": "mostPopular", "regionCode": "IN",
        "videoCategoryId": "25", "maxResults": 10, "key": key}).json()
    return [{"title": f"{v['snippet']['title']} [YouTube trending, {int(v['statistics'].get('viewCount', 0)):,} views]",
             "src": "YouTube trending: " + v["snippet"]["channelTitle"]} for v in r.get("items", [])]


def suggest(sources, hours, n, trending=False):
    sources = (BUILTIN if trending else []) + list(sources)
    items, seen = [], set()
    for s in sources:
        try:
            got = fetch(s, hours)
        except Exception:
            continue
        for it in got:
            k = re.sub(r"\W+", "", it["title"].lower())[:40]
            if k not in seen:
                seen.add(k); items.append(it)
    if trending:
        try:
            items += yt_trending()
        except Exception:
            pass
    items = items[:80]
    if not items:
        bot.say("No fresh stories found for: " + ", ".join(sources))
        return
    listing = "\n".join(f"{i}. {x['title']}" for i, x in enumerate(items))
    p = f"""Pick the {n} stories that are the LATEST and MOST POPULAR right now in India and most likely to go viral as a 30-second reel for an Indian news and current-affairs Instagram page.
Prefer stories marked trending, stories that appear in several sources, and ones with high curiosity, strong emotion and easy sharing. Skip unverified rumours, communal flashpoints and graphic violence. Merge near-duplicates.
Return ONLY a JSON list: [{{"i": index, "why": "one sentence", "angle": "a fresh hook angle in one sentence"}}]
STORIES:
{listing}"""
    picks = studio.jparse(studio.gem(p))
    kb = json.dumps({"inline_keyboard": [[{"text": "Make reel", "callback_data": "mk:1"},
                                          {"text": "Skip", "callback_data": "sk:1"}]]})
    for pk in picks[:n]:
        x = items[int(pk["i"])]
        bot.tg("sendMessage", chat_id=bot.CHAT, reply_markup=kb,
               text=f"{x['title']}\nSource: {x['src']}\nWhy: {pk['why']}\nAngle: {pk['angle']}"[:4000])


if __name__ == "__main__":
    f = pathlib.Path("sources.txt")
    srcs = [l.strip() for l in f.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")] if f.exists() else []
    suggest(srcs, int(bot.E.get("HOURS", "12")), int(bot.E.get("N", "2")), trending=True)
