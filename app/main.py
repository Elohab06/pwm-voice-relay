import os, json, base64, re, threading, queue, traceback
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
    return speech.SpeechClient(credentials=creds)

def build_streaming_config():
    sample_rate = int(os.getenv("SPEECH_SAMPLE_RATE","16000"))
    language = os.getenv("SPEECH_LANGUAGE","tr-TR")
    config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
        sample_rate_hertz=sample_rate,
        language_code=language,
        enable_automatic_punctuation=True,
    )
    return speech.StreamingRecognitionConfig(
        config=config, interim_results=True, single_utterance=False
    )

UNITS={"sıfır":0,"bir":1,"iki":2,"üç":3,"dört":4,"beş":5,"altı":6,"yedi":7,"sekiz":8,"dokuz":9}
TENS={"on":10,"yirmi":20,"otuz":30,"kırk":40,"elli":50,"altmış":60,"yetmiş":70,"seksen":80,"doksan":90}
def normalize_tr(s:str)->str:
    s=s.lower(); s=re.sub(r"[^\w%\s]"," ",s); s=re.sub(r"\s+"," ",s).strip(); return s
def parse_tr_number_0_100(text:str):
    t=normalize_tr(text); toks=t.split()
    if "yüz" in toks: return 100
    m=re.search(r"%\s*(\d{1,3})",t)
    if m:
        v=int(m.group(1)); 
        if 0<=v<=100: return v
    m=re.search(r"\b(\d{1,3})\b",t)
    if m:
        v=int(m.group(1)); 
        if 0<=v<=100: return v
    n=len(toks)
    for i in range(n):
        w=toks[i]
        if w in TENS and i+1<n and toks[i+1] in UNITS: return TENS[w]+UNITS[toks[i+1]]
        if w in TENS: return TENS[w]
        if w in UNITS: return UNITS[w]
    return None
def extract_percent(text:str):
    m=re.search(r"%\s*(\d{1,3})",text)
    if m:
        v=int(m.group(1)); 
        if 0<=v<=100: return v
    m=re.search(r"\b(\d{1,3})\b",text)
    if m:
        v=int(m.group(1)); 
        if 0<=v<=100: return v
    return parse_tr_number_0_100(text)
def is_stop_intent(text:str)->bool:
    t=normalize_tr(text)
    for p in ["asistan dur","asistan kapat","asistan bitir","asistan sus","dur","kapat","bitir","yeter","tamam yeter","durdur"]:
        if p in t: return True
    return False

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    # session_start ACK (bağlantıyı test etmek için)
    await ws.send_text(json.dumps({"type":"assistant_say","text":"(debug) session started"}))
    try:
        client=get_gcp_client()
        streaming_config=build_streaming_config()
        audio_q: queue.Queue[bytes|None]=queue.Queue()
        out_q: queue.Queue[tuple[str,bool]]=queue.Queue()
        first_flag={"seen":False}

        def requests():
            while True:
                chunk=audio_q.get()
                if chunk is None: break
                yield speech.StreamingRecognizeRequest(audio_content=chunk)

        def consume():
            try:
                for resp in client.streaming_recognize(streaming_config, requests()):
                    for result in resp.results:
                        text = result.alternatives[0].transcript or ""
                        out_q.put((text, result.is_final))
            except Exception as e:
                out_q.put((f"__ERROR__:{e}", True))

        th=threading.Thread(target=consume,daemon=True); th.start()

        while True:
            try:
                raw = await ws.receive_text()
            except WebSocketDisconnect:
                break
            msg=json.loads(raw)
            if msg.get("type")=="audio_chunk" and msg.get("format")=="PCM_S16LE_16000":
                if not first_flag["seen"]:
                    first_flag["seen"]=True
                    await ws.send_text(json.dumps({"type":"assistant_say","text":"(debug) ses paketleri alınıyor"}))
                audio_q.put(base64.b64decode(msg.get("data","")))
            elif msg.get("type")=="end_of_utterance":
                pass
            elif msg.get("type")=="session_end":
                break

            while not out_q.empty():
                text,is_final=out_q.get()
                if text.startswith("__ERROR__"):
                    await ws.send_text(json.dumps({"type":"assistant_say","text":f"(debug) error: {text[9:]}"}))
                    continue
                if not text: continue
                if is_final:
                    await ws.send_text(json.dumps({"type":"assistant_say","text":f"(debug) transcript: {text}"}))
                    if is_stop_intent(text):
                        await ws.send_text(json.dumps({"type":"session_end"})); audio_q.put(None); th.join(timeout=2.0); return
                    p=extract_percent(text)
                    if p is not None:
                        await ws.send_text(json.dumps({"type":"function_call","name":"set_pwm","args":{"percent":p}}))
                        await ws.send_text(json.dumps({"type":"assistant_say","text":f"PWM'i yüzde {p} olarak ayarlıyorum."}))
                else:
                    await ws.send_text(json.dumps({"type":"assistant_say","text":f"(debug) partial: {text[:40]}"}))
    except Exception as e:
        try:
            await ws.send_text(json.dumps({"type":"assistant_say","text":f"(debug) fatal: {e}"}))
        except:
            pass
