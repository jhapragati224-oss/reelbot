"""Reel bot: Telegram video/link in -> AI-picked 9:16 captioned reels -> your approval -> Instagram.
Runs on GitHub Actions every 5 min. Stateless: Telegram holds the queue and the previews."""
import os, re, sys, json, time, hashlib, pathlib, subprocess, requests

E = os.environ
TOKEN = E["TELEGRAM_TOKEN"]
TG = f"https://api.telegram.org/bot{TOKEN}"
CHAT = int(E["TELEGRAM_CHAT_ID"])
GRAPH = f"https://graph.facebook.com/{E.get('GRAPH_VERSION', 'v23.0')}"
MODEL = E.get("GEMINI_MODEL") or "gemini-2.5-flash"
WHISPER = E.get("WHISPER_MODEL") or "small"
W = pathlib.Path("work"); W.mkdir(exist_ok=True)
sys.modules.setdefault("bot", sys.modules[__name__])

HELP = ("Send me a video (under 20 MB) or a Google Drive link (set to 'Anyone with the link'). "
        "Add a note after it if you want a specific angle, e.g. 'focus on the funding angle'. "
        "I will edit up to 3 reels and send them here, ready for you to download and post. "
        "Or send /make <topic> <video links from X/YouTube> to build a reel from specific footage, or /trending for the most popular stories right now, or /find <topic> and I will bring you fresh stories to turn into reels.")


def tg(method, files=None, **data):
    r = requests.post(f"{TG}/{method}", data=data, files=files, timeout=300).json()
    if not r.get("ok"):
        raise RuntimeError(r)
    return r["result"]


def say(t):
    tg("sendMessage", chat_id=CHAT, text=t[:4000])


def sh(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError((p.stderr or p.stdout)[-800:])
    return p.stdout


def heavy():
    """Install heavy deps only when a video actually needs processing (keeps empty polls fast)."""
    sh([sys.executable, "-m", "pip", "install", "-q", "faster-whisper", "gdown", "yt-dlp", "edge-tts"])
    sh(["sudo", "apt-get", "update", "-qq"])
    sh(["sudo", "apt-get", "install", "-y", "-qq", "fonts-noto-core"])


# ---------- input ----------
def download(src):
    out = W / "in.mp4"
    kind, v = src
    if kind == "tg":
        try:
            info = tg("getFile", file_id=v)
        except Exception:
            raise RuntimeError("Telegram only lets bots download files under 20 MB. Upload to Google Drive and send the link instead.")
        out.write_bytes(requests.get(f"https://api.telegram.org/file/bot{TOKEN}/{info['file_path']}", timeout=300).content)
    elif "drive.google.com" in v:
        sh([sys.executable, "-m", "gdown", "--fuzzy", v, "-O", str(out)])
    else:
        sh([sys.executable, "-m", "yt_dlp", "-f", "mp4/best", "-o", str(out), v])
    return out


# ---------- AI steps ----------
def transcribe(path):
    from faster_whisper import WhisperModel
    model = WhisperModel(WHISPER, device="cpu", compute_type="int8")
    segs, _ = model.transcribe(str(path), word_timestamps=True, vad_filter=True)
    return [{"start": s.start, "end": s.end, "text": s.text.strip(),
             "words": [(w.word.strip(), w.start, w.end) for w in (s.words or [])]} for s in segs]


def ask_gemini(tr, note, dur):
    lines = "\n".join(f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}" for s in tr)[:60000]
    prompt = f"""You are a short-form video editor for an Indian news and current-affairs Instagram page (Hindi/Hinglish audience).
From the timestamped transcript below, pick up to 3 clips of 20-45 seconds each that will get the highest retention and shares.
Rules:
- start/end must align with segment boundaries in the transcript. Each clip must make sense on its own.
- Begin each clip on its strongest line (curiosity gap, surprising claim, emotional peak). Cut setup that comes before it.
- Prefer clips with an open loop or a punchy ending that invites rewatching and sharing.
- NEVER add facts that are not in the transcript. Do not invent claims, numbers or quotes.
- In "risk", flag anything that could mislead out of context, or is communal, defamatory or unverified. Use "none" if clean.
Creator note: {note or 'none'}
Video length: {dur:.0f}s
Return ONLY a JSON list of objects with keys:
start (number), end (number), headline (max 8 words, Hinglish, no emojis), caption (hook line, 1-2 short lines, then 'Follow for daily updates', under 300 chars),
hashtags (list of 5 strings starting with #), score (1-10), why (one sentence), risk (string).

TRANSCRIPT:
{lines}"""
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
        params={"key": E["GEMINI_API_KEY"]},
        json={"contents": [{"parts": [{"text": prompt}]}],
              "generationConfig": {"responseMimeType": "application/json"}},
        timeout=180).json()
    try:
        clips = json.loads(r["candidates"][0]["content"]["parts"][0]["text"])
    except Exception:
        raise RuntimeError(f"Gemini error: {str(r)[:500]}")
    ok = []
    for c in clips:
        c["start"] = max(0.0, float(c["start"])); c["end"] = min(dur, float(c["end"]))
        if c["end"] - c["start"] > 60:
            c["end"] = c["start"] + 60
        if c["end"] - c["start"] >= 8:
            ok.append(c)
    return sorted(ok, key=lambda c: -float(c.get("score", 0)))[:3]


# ---------- render ----------
def make_ass(tr, c, path):
    def t(x):
        x = max(0.0, x)
        return f"{int(x // 3600)}:{int(x % 3600 // 60):02d}:{x % 60:05.2f}"
    words = [(w, s - c["start"], e - c["start"]) for seg in tr for (w, s, e) in seg["words"]
             if s >= c["start"] - 0.1 and e <= c["end"] + 0.1]
    dur = c["end"] - c["start"]
    head = c["headline"].replace("{", "").replace("}", "").upper()
    out = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 0\n\n"
           "[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,"
           "Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
           "Style: Cap,Noto Sans,84,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,6,0,2,60,60,520,1\n"
           "Style: Head,Noto Sans,60,&H00000000,&H00000000,&H0000FFFF,&H0000FFFF,1,0,0,0,100,100,0,0,3,12,0,8,60,60,150,1\n\n"
           "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n")
    out += f"Dialogue: 0,{t(0)},{t(dur)},Head,,0,0,0,,{head}\n"
    chunks = [words[i:i + 3] for i in range(0, len(words), 3)]
    for i, ch in enumerate(chunks):
        s = ch[0][1]
        e = chunks[i + 1][0][1] if i + 1 < len(chunks) else ch[-1][2]
        txt = " ".join(w for w, _, _ in ch).upper().replace("{", "").replace("}", "")
        out += f"Dialogue: 0,{t(s)},{t(max(e, s + 0.3))},Cap,,0,0,0,,{txt}\n"
    path.write_text(out, encoding="utf-8")


def render(src, tr, c, i):
    a, o = W / f"c{i}.ass", W / f"reel{i}.mp4"
    make_ass(tr, c, a)
    fc = ("[0:v]split=2[a][b];"
          "[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5[bg];"
          "[b]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
          f"[bg][fg]overlay=(W-w)/2:(H-h)/2,subtitles={a}[v]")
    sh(["ffmpeg", "-y", "-ss", str(c["start"]), "-to", str(c["end"]), "-i", str(src),
        "-filter_complex", fc, "-map", "[v]", "-map", "0:a?", "-af", "loudnorm=I=-16:TP=-1.5",
        "-c:v", "libx264", "-crf", "23", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(o)])
    return o


# ---------- hosting + posting ----------
def cloud_parts():
    return re.match(r"cloudinary://(\w+):([^@]+)@(.+)", E["CLOUDINARY_URL"].strip()).groups()


def upload(path, pid):
    key, sec, cloud = cloud_parts()
    ts = str(int(time.time()))
    sig = hashlib.sha1(f"public_id={pid}&timestamp={ts}{sec}".encode()).hexdigest()
    with open(path, "rb") as f:
        r = requests.post(f"https://api.cloudinary.com/v1_1/{cloud}/video/upload",
                          data={"api_key": key, "timestamp": ts, "public_id": pid, "signature": sig},
                          files={"file": f}, timeout=600).json()
    if "secure_url" not in r:
        raise RuntimeError(f"Cloudinary: {r}")


def publish(url, caption):
    tok, uid = E["IG_ACCESS_TOKEN"], E["IG_USER_ID"]
    r = requests.post(f"{GRAPH}/{uid}/media", data={"media_type": "REELS", "video_url": url, "caption": caption,
                                                   "share_to_feed": "true", "access_token": tok}).json()
    if "id" not in r:
        raise RuntimeError(f"Instagram: {r}")
    cid = r["id"]
    for _ in range(40):
        s = requests.get(f"{GRAPH}/{cid}", params={"fields": "status_code", "access_token": tok}).json()
        if s.get("status_code") == "FINISHED":
            break
        if s.get("status_code") in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram processing: {s}")
        time.sleep(15)
    r = requests.post(f"{GRAPH}/{uid}/media_publish", data={"creation_id": cid, "access_token": tok}).json()
    if "id" not in r:
        raise RuntimeError(f"Instagram publish: {r}")


# ---------- flows ----------
def send_preview(out, pid, cap):
    with open(out, "rb") as f:
        tg("sendVideo", files={"video": f}, chat_id=CHAT, caption=cap[:1000], supports_streaming="true")


def make_reels(src, note):
    say("Got it. Editing now, this takes about 5-15 minutes.")
    heavy()
    path = download(src)
    dur = float(sh(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]))
    tr = transcribe(path)
    if not tr:
        raise RuntimeError("No speech found in this video. Reels are picked from spoken content.")
    clips = ask_gemini(tr, note, dur)
    if not clips:
        raise RuntimeError("AI found no strong 20+ second clip in this video.")
    stamp = int(time.time())
    for i, c in enumerate(clips, 1):
        out = render(path, tr, c, i)
        pid = f"r{stamp}_{i}"
        cap = (c["caption"] + "\n\n" + " ".join(c.get("hashtags", [])))[:1000]
        send_preview(out, pid, cap)
        say(f"Reel {i}: score {c.get('score')}/10\nWhy: {c.get('why')}\nRisk check: {c.get('risk')}\n"
            f"Clip {c['start']:.0f}s-{c['end']:.0f}s")


def on_button(q):
    tg("answerCallbackQuery", callback_query_id=q["id"])
    if q["from"]["id"] != CHAT:
        return
    act, pid = q["data"].split(":", 1)
    msg = q["message"]
    tg("editMessageReplyMarkup", chat_id=CHAT, message_id=msg["message_id"],
       reply_markup=json.dumps({"inline_keyboard": []}))
    if act == "sk":
        return
    if act == "mk":
        say("Researching and building this reel. Takes about 5-10 minutes.")
        heavy()
        import studio
        studio.build(msg.get("text", ""))
        return
    if act == "no":
        say("Rejected.")
        return
    cloud = cloud_parts()[2]
    publish(f"https://res.cloudinary.com/{cloud}/video/upload/{pid}.mp4", msg.get("caption", ""))
    say("Posted to Instagram.")


def handle(u):
    if "callback_query" in u:
        return on_button(u["callback_query"])
    m = u.get("message") or {}
    if m.get("chat", {}).get("id") != CHAT:
        return
    text = m.get("text") or m.get("caption") or ""
    if text.startswith("/make"):
        say("Building from your links. Takes about 5-10 minutes.")
        heavy()
        import studio
        studio.build(re.sub(r"https?://\S+", "", text[5:]).strip(), re.findall(r"https?://\S+", text))
        return
    if text.startswith("/trending") or (text.startswith("/find") and not text[5:].strip()):
        import discover
        discover.suggest([], 24, 4, trending=True)
        return
    if text.startswith("/find"):
        import discover
        discover.suggest([text[5:].strip()], 24, 4)
        return
    doc = m.get("document") or {}
    media = m.get("video") or (doc if doc.get("mime_type", "").startswith("video") else None)
    url = re.search(r"https?://\S+", text)
    note = re.sub(r"https?://\S+", "", text).strip()
    if media:
        make_reels(("tg", media["file_id"]), note)
    elif url:
        make_reels(("url", url.group(0)), note)
    else:
        say(HELP)


def main():
    ups = tg("getUpdates", timeout=0, allowed_updates=json.dumps(["message", "callback_query"]))
    if not ups:
        return
    tg("getUpdates", offset=ups[-1]["update_id"] + 1, timeout=0)  # confirm so nothing runs twice
    for u in ups:
        try:
            handle(u)
        except Exception as e:
            say(f"Error: {str(e)[:900]}")


if __name__ == "__main__":
    main()
