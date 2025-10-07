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

def extract_percent(text: str) -> int | None:
    m = re.search(r"%\s*(\d{1,3})", text)
    if m:
        v = int(m.group(1))
        if 0 <= v <= 100: return v
    m = re.search(r"\b(\d{1,3})\b", text)
    if m:
        v = int(m.group(1))
        if 0 <= v <= 100: return v
    return None

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
