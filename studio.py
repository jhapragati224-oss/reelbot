"""Studio: story -> researched script -> voiceover -> stock B-roll -> fast-cut captioned reel."""
import re, sys, json, time, random, asyncio, pathlib, datetime, requests
import bot
from bot import W, E, sh, say

VOICE = E.get("VOICE") or "hi-IN-MadhurNeural"
LANG = E.get("SCRIPT_LANG") or "Hindi in Devanagari script"


def gem(prompt, search=False):
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    if search:
        body["tools"] = [{"google_search": {}}]  # grounded in real search results
    else:
        body["generationConfig"] = {"responseMimeType": "application/json"}
    r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{bot.MODEL}:generateContent",
                      params={"key": E["GEMINI_API_KEY"]}, json=body, timeout=240).json()
    try:
        return "".join(p.get("text", "") for p in r["candidates"][0]["content"]["parts"])
    except Exception:
        raise RuntimeError(f"Gemini error: {str(r)[:500]}")


def jparse(t):
    return json.loads(re.search(r"(\[.*\]|\{.*\})", t, re.S).group(1))


def write_script(topic):
    p = f"""You are head writer for a viral Indian news Instagram page. Use Google Search to research this story, then write a 30-40 second reel script.
STORY: {topic}
Rules:
- Use ONLY facts you found in search results. If sources disagree or something is unconfirmed, say "reports say" or leave it out. Never invent numbers, quotes or names.
- Scene 1 is a pattern-interrupt hook: the most surprising verified fact or a sharp question, max 12 words. No greeting, no intro.
- 6-8 scenes. Each scene is ONE short spoken sentence (max 15 words) in {LANG}, conversational, one new fact per scene, curiosity rising.
- Last scene: an open-loop or opinion question that invites comments and shares, then a short follow line.
- broll: a 2-3 word ENGLISH stock-footage search query that visually fits the scene (generic visuals: crowd, city, courtroom, money, flag, rain...).
- Stay neutral. Report, do not attack communities or individuals.
Return ONLY JSON: {{"headline":"max 7 words English/Hinglish","scenes":[{{"voice":"...","broll":"..."}}],"caption":"hook line plus 2 short lines, under 300 chars","hashtags":["#a","#b","#c","#d","#e"],"sources":["outlet names used"],"risk":"anything unverified or sensitive, or none"}}"""
    return jparse(gem(p, search=True))


async def _tts(text, path):
    import edge_tts
    await edge_tts.Communicate(text, VOICE, rate="+8%").save(path)


def dur(p):
    return float(sh(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)]))


def pexels(q, used):
    key = E.get("PEXELS_API_KEY")
    if not key:
        return None
    r = requests.get("https://api.pexels.com/videos/search", headers={"Authorization": key}, timeout=30,
                     params={"query": q, "orientation": "portrait", "per_page": 8, "size": "medium"}).json()
    for v in r.get("videos", []):
        if v["id"] in used:
            continue
        fs = [f for f in v["video_files"] if f.get("file_type") == "video/mp4"]
        if not fs:
            continue
        f = min(fs, key=lambda f: abs((f.get("height") or 0) - 1920))
        used.add(v["id"])
        out = W / f"b{v['id']}.mp4"
        out.write_bytes(requests.get(f["link"], timeout=120).content)
        return out


def grab(url, name):
    out = W / f"{name}.mp4"
    cmd = [sys.executable, "-m", "yt_dlp", "-f", "mp4[height<=1080]/best[height<=1080]/best",
           "--max-filesize", "80M", "--no-playlist", "-o", str(out)]
    if E.get("YT_COOKIES"):
        ck = W / "cookies.txt"; ck.write_text(E["YT_COOKIES"]); cmd += ["--cookies", str(ck)]
    sh(cmd + [url])
    return out


def yt_search(topic):
    key = E.get("YT_API_KEY")
    if not key:
        return []
    after = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=4)).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = requests.get("https://www.googleapis.com/youtube/v3/search", timeout=30, params={
        "part": "snippet", "q": topic, "type": "video", "videoDuration": "short", "maxResults": 8,
        "publishedAfter": after, "regionCode": "IN", "key": key}).json()
    return [(i["id"]["videoId"], i["snippet"]["channelTitle"]) for i in r.get("items", [])]


def real_footage(topic, urls):
    pool, credits, fails = [], [], 0
    for i, u in enumerate(urls):
        try:
            pool.append(grab(u, f"u{i}")); credits.append(re.sub(r"https?://(www\.)?", "", u).split("/")[0])
        except Exception:
            fails += 1
    if len(pool) < 3:
        for vid, ch in yt_search(topic):
            try:
                pool.append(grab(f"https://youtu.be/{vid}", f"y{vid}")); credits.append(ch)
            except Exception:
                fails += 1
            if len(pool) >= 4:
                break
    if fails:
        say(f"{fails} video download(s) were blocked or failed. This is common on cloud servers.")
    return pool, credits


def build(topic, urls=()):
    sc = write_script(topic)
    scenes = sc["scenes"][:8]
    d = W / "studio"; d.mkdir(exist_ok=True)
    auds, durs = [], []
    for i, s in enumerate(scenes):
        a = d / f"a{i}.mp3"
        asyncio.run(_tts(s["voice"], str(a)))
        auds.append(a); durs.append(dur(a))
    (d / "a.txt").write_text("".join(f"file '{a.resolve()}'\n" for a in auds))
    voice = d / "voice.mp3"
    sh(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(d / "a.txt"), "-c:a", "libmp3lame", "-q:a", "3", str(voice)])

    pool, credits = real_footage(topic, list(urls))
    cursor = {p: random.uniform(0, 3) for p in pool}
    used, vids = set(), []
    for i, (s, du) in enumerate(zip(scenes, durs)):
        clip, start = None, 0.0
        if pool:
            clip = pool[i % len(pool)]
            vd = dur(clip)
            start = cursor[clip] if cursor[clip] + du < vd else 0.0
            cursor[clip] = start + du
        else:
            try:
                clip = pexels(s.get("broll", "city"), used)
            except Exception:
                clip = None
        if clip is None and not E.get("PEXELS_API_KEY"):
            raise RuntimeError("Could not get any real footage (downloads were blocked). Send me the clips: "
                               "/make <topic> <links>, or upload videos to Google Drive and send the link.")
        src = ["-ss", f"{start:.2f}", "-stream_loop", "-1", "-i", str(clip)] if clip else ["-f", "lavfi", "-i", "color=c=0x0b1220:s=1080x1920:r=30"]
        punch = "scale=1296:2304,crop=1080:1920," if i % 2 else ""  # alternate wide / punched-in framing
        fc = ("[0:v]split=2[a][b];[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5[bg];"
              "[b]scale=1080:1920:force_original_aspect_ratio=decrease[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2," + punch +
              "eq=contrast=1.12:saturation=1.2:brightness=-0.03,fps=30,setsar=1,format=yuv420p,fade=t=in:st=0:d=0.12[v]")
        o = d / f"s{i}.mp4"
        sh(["ffmpeg", "-y", *src, "-t", f"{du:.2f}", "-filter_complex", fc, "-map", "[v]", "-an",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", str(o)])
        vids.append(o)
    (d / "v.txt").write_text("".join(f"file '{v.resolve()}'\n" for v in vids))
    sh(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(d / "v.txt"), "-c", "copy", str(d / "silent.mp4")])

    total = sum(durs)
    tr = bot.transcribe(voice)  # exact word timing for captions
    ass = d / "cap.ass"
    bot.make_ass(tr, {"start": 0, "end": total + 1, "headline": sc["headline"]}, ass)

    inputs = ["-i", str(d / "silent.mp4"), "-i", str(voice)]
    if pathlib.Path("music.mp3").exists():  # optional: upload any royalty-free track named music.mp3
        inputs += ["-stream_loop", "-1", "-i", "music.mp3"]
        af = "[1:a]volume=1.0[vo];[2:a]volume=0.10[m];[vo][m]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-16:TP=-1.5[a]"
    else:
        af = "[1:a]loudnorm=I=-16:TP=-1.5[a]"
    out = W / "studio_reel.mp4"
    sh(["ffmpeg", "-y", *inputs, "-filter_complex", f"[0:v]subtitles={ass}[v];{af}", "-map", "[v]", "-map", "[a]",
        "-t", f"{total:.2f}", "-c:v", "libx264", "-crf", "23", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)])

    pid = f"s{int(time.time())}"
    cap = sc["caption"] + ("\n\nFootage: " + ", ".join(dict.fromkeys(credits)) if credits else "") + "\n\nSources: " + ", ".join(sc.get("sources", [])) + "\n\n" + " ".join(sc.get("hashtags", []))
    bot.send_preview(out, pid, cap)
    say(f"Risk check: {sc.get('risk')}\nSources used: {', '.join(sc.get('sources', []))}\n"
        f"Please verify the facts before you post.")
