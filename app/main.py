import os, json, base64, asyncio, re
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from google.cloud import speech
from google.oauth2 import service_account

app = FastAPI()

@app.get("/healthz")
async def healthz():
    return {"status":"ok","lang":os.getenv("LANG","tr-TR")}

def get_gcp_client():
    creds_info = json.loads(os.environ["GOOGLE_APPLICATION_CREDENTIALS_JSON"])
    creds = service_account.Credentials.from_service_account_info(creds_info)
    client = speech.SpeechClient(credentials=creds)
    return client

def build_streaming_config():
    sample_rate = int(os.getenv("SPEECH_SAMPLE_RATE","16000"))
    language = os.getenv("SPEECH_LANGUAGE","tr-TR")
    use_adaptation = os.getenv("SPEECH_USE_ADAPTATION","false").lower() == "true"
    phrases = [p.strip() for p in os.getenv("SPEECH_PHRASES","").split(",") if p.strip()]

    config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
        sample_rate_hertz=sample_rate,
        language_code=language,
        enable_automatic_punctuation=True,
    )

    if use_adaptation and phrases:
        phrase_set = speech.SpeechAdaptation.PhraseSet(
            phrases=[speech.SpeechAdaptation.PhraseSet.Phrase(value=p) for p in phrases],
        )
        adaptation = speech.SpeechAdaptation(phrase_sets=[phrase_set])
        streaming_config = speech.StreamingRecognitionConfig(
            config=config,
            interim_results=True,
            single_utterance=False,
            adaptation=adaptation,
        )
    else:
        streaming_config = speech.StreamingRecognitionConfig(
            config=config,
            interim_results=True,
            single_utterance=False,
        )
    return streaming_config

UNITS = {"sıfır":0,"bir":1,"iki":2,"üç":3,"dört":4,"beş":5,"altı":6,"yedi":7,"sekiz":8,"dokuz":9}
TENS  = {"on":10,"yirmi":20,"otuz":30,"kırk":40,"elli":50,"altmış":60,"yetmiş":70,"seksen":80,"doksan":90}

def normalize_tr(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^\w%\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def parse_tr_number_0_100(text: str):
    t = normalize_tr(text)
    tokens = t.split()
    if "yüz" in tokens: return 100
    m = re.search(r"%\s*(\d{1,3})", t)
    if m:
        v = int(m.group(1))
        if 0 <= v <= 100: return v
    m = re.search(r"\b(\d{1,3})\b", t)
    if m:
        v = int(m.group(1))
        if 0 <= v <= 100: return v
    n = len(tokens)
    for i in range(n):
        w = tokens[i]
        if w in TENS and i+1 < n and tokens[i+1] in UNITS:
            return TENS[w] + UNITS[tokens[i+1]]
        if w in TENS: return TENS[w]
        if w in UNITS: return UNITS[w]
    return None

def extract_percent(text: str):
    m = re.search(r"%\s*(\d{1,3})", text)
    if m:
        v = int(m.group(1))
        if 0 <= v <= 100: return v
    m = re.search(r"\b(\d{1,3})\b", text)
    if m:
        v = int(m.group(1))
        if 0 <= v <= 100: return v
    return parse_tr_number_0_100(text)

def is_stop_intent(text: str) -> bool:
    t = normalize_tr(text)
    stop_phrases = [
        "asistan dur","asistan kapat","asistan bitir","asistan sus",
        "dur","kapat","bitir","yeter","tamam yeter","durdur"
    ]
    return any(p in t for p in stop_phrases)

async def ws_audio_to_gcp(ws: WebSocket, client: speech.SpeechClient, streaming_config: speech.StreamingRecognitionConfig):
    queue: asyncio.Queue[bytes] = asyncio.Queue()

    async def producer():
        try:
            while True:
                raw = await ws.receive_text()
                msg = json.loads(raw)
                t = msg.get("type")
                if t == "audio_chunk" and msg.get("format") == "PCM_S16LE_16000":
                    chunk = base64.b64decode(msg.get("data",""))
                    await queue.put(chunk)
                elif t == "session_end":
                    break
        except WebSocketDisconnect:
            pass
        finally:
            await queue.put(b"")

    def requests():
        while True:
            chunk = asyncio.run(queue.get())
            if not chunk:
                break
            yield speech.StreamingRecognizeRequest(audio_content=chunk)

    async def consume_responses():
        responses = client.streaming_recognize(streaming_config, requests())
        for resp in responses:
            for result in resp.results:
                if result.is_final:
                    yield result.alternatives[0].transcript

    prod_task = asyncio.create_task(producer())
    try:
        async for text in consume_responses():
            await ws.send_text(json.dumps({"type":"final_transcript","text": text}))
            if is_stop_intent(text):
                await ws.send_text(json.dumps({"type":"assistant_say","text":"Görüşürüz. Oturumu kapatıyorum."}))
                await ws.send_text(json.dumps({"type":"session_end"}))
                break
            percent = extract_percent(text)
            if percent is not None:
                await ws.send_text(json.dumps({"type":"function_call","name":"set_pwm","args":{"percent": percent}}))
                await ws.send_text(json.dumps({"type":"assistant_say","text": f"PWM'i yüzde {percent} olarak ayarlıyorum."}))
    finally:
        prod_task.cancel()

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    try:
        await ws.send_text(json.dumps({"type":"assistant_say","text":"Merhaba, hazırım."}))
        client = get_gcp_client()
        streaming_config = build_streaming_config()
        await ws_audio_to_gcp(ws, client, streaming_config)
    except WebSocketDisconnect:
        pass
