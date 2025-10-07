from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import os, json, base64, io, wave, re, httpx

app = FastAPI()

@app.get("/healthz")
async def healthz():
    return {"status": "ok", "lang": os.getenv("LANG", "tr-TR")}

def pcm16le_to_wav(pcm_bytes: bytes, sample_rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()

# Basit TR sayı sözlüğü (0..100)
UNITS = {
    "sıfır":0, "bir":1, "iki":2, "üç":3, "dört":4, "beş":5,
    "altı":6, "yedi":7, "sekiz":8, "dokuz":9
}
TENS = {
    "on":10, "yirmi":20, "otuz":30, "kırk":40, "elli":50,
    "altmış":60, "yetmiş":70, "seksen":80, "doksan":90
}
SPECIAL = {"yüz":100}

def normalize_tr(s: str) -> str:
    s = s.lower()
    # Basit temizlik: yüzde, pwm, led vb. sözcükleri etkisiz kıl
    s = re.sub(r"[^\w\%\s]", " ", s)  # noktalama -> boşluk
    s = re.sub(r"\s+", " ", s).strip()
    return s

def parse_tr_number_0_100(text: str) -> int | None:
    t = normalize_tr(text)
    tokens = t.split()

    # "yüz" tek başına → 100
    if "yüz" in tokens:
        return 100

    # "% 45" veya "%45" → önce rakamla yakalama
    m = re.search(r"%\s*(\d{1,3})", t)
    if m:
        v = int(m.group(1))
        if 0 <= v <= 100:
            return v

    # Düz rakam → 0..100
    m = re.search(r"\b(\d{1,3})\b", t)
    if m:
        v = int(m.group(1))
        if 0 <= v <= 100:
            return v

    # Yazıyla (tens + unit) veya tek unit/tens
    n = len(tokens)
    for i in range(n):
        w = tokens[i]
        # tens + unit (örn. "kırk beş", "on bir")
        if w in TENS and i+1 < n and tokens[i+1] in UNITS:
            return TENS[w] + UNITS[tokens[i+1]]
        # tek tens (örn. "kırk")
        if w in TENS:
            return TENS[w]
        # tek unit (örn. "beş")
        if w in UNITS:
            return UNITS[w]

    return None

def extract_percent(text: str) -> int | None:
    # Önce hızlı yol: rakamlı ifadeler
    m = re.search(r"%\s*(\d{1,3})", text)
    if m:
        v = int(m.group(1))
        if 0 <= v <= 100:
            return v
    m = re.search(r"\b(\d{1,3})\b", text)
    if m:
        v = int(m.group(1))
        if 0 <= v <= 100:
            return v
    # Sonra yazıyla Türkçe sayı çöz
    return parse_tr_number_0_100(text)

async def transcribe_deepgram(wav_bytes: bytes, lang: str = "tr") -> str:
    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        return ""
    url = "https://api.deepgram.com/v1/listen?model=nova-2-general&language=" + lang
    headers = {"Authorization": f"Token {api_key}", "Content-Type": "audio/wav"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(url, headers=headers, content=wav_bytes)
        r.raise_for_status()
        data = r.json()
    try:
        return data["results"]["channels"][0]["alternatives"][0]["transcript"]
    except Exception:
        return ""

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    audio_buf = bytearray()
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                await ws.send_text(json.dumps({"type":"error","message":"invalid_json"}))
                continue

            mtype = msg.get("type")

            if mtype == "session_start":
                await ws.send_text(json.dumps({"type":"assistant_say","text":"Merhaba, hazırım."}))

            elif mtype == "audio_chunk":
                if msg.get("format") == "PCM_S16LE_16000":
                    audio_buf.extend(base64.b64decode(msg.get("data","")))

            elif mtype == "end_of_utterance":
                if not audio_buf:
                    await ws.send_text(json.dumps({"type":"assistant_say","text":"Ses alamadım, tekrar deneyelim."}))
                    continue
                wav_bytes = pcm16le_to_wav(bytes(audio_buf), sample_rate=16000)
                audio_buf.clear()

                transcript = await transcribe_deepgram(wav_bytes, lang="tr")
                if transcript:
                    await ws.send_text(json.dumps({"type":"final_transcript","text": transcript}))
                    percent = extract_percent(transcript)
                    if percent is not None:
                        await ws.send_text(json.dumps({"type":"function_call","name":"set_pwm","args":{"percent": percent}}))
                        await ws.send_text(json.dumps({"type":"assistant_say","text": f"PWM'i yüzde {percent} olarak ayarlıyorum."}))
                    else:
                        await ws.send_text(json.dumps({"type":"assistant_say","text":"Hangi yüzdeye ayarlamamı istersiniz? 0 ile 100 arasında söyleyin."}))
                else:
                    await ws.send_text(json.dumps({"type":"assistant_say","text":"Anlayamadım, lütfen tekrar edin."}))

            elif mtype == "session_end":
                await ws.close()
                break

            else:
                await ws.send_text(json.dumps({"type":"error","message":f"unknown_type:{mtype}"}))
    except WebSocketDisconnect:
        pass
