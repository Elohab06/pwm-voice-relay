from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import os, json

app = FastAPI()

@app.get("/healthz")
async def healthz():
    return {"status": "ok", "lang": os.getenv("LANG", "tr-TR")}

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
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
                # Basit karşılama
                await ws.send_text(json.dumps({"type":"assistant_say","text":"Merhaba, hazırım."}))

            elif mtype == "audio_chunk":
                # Şimdilik yok sayıyoruz (ASR bağlayınca işleyeceğiz)
                pass

            elif mtype == "end_of_utterance":
                # Şimdilik sahte sonuç: PWM'i %40 yap
                await ws.send_text(json.dumps({"type":"function_call","name":"set_pwm","args":{"percent":40}}))
                await ws.send_text(json.dumps({"type":"assistant_say","text":"PWM'i yüzde 40'a alıyorum."}))

            elif mtype == "session_end":
                await ws.close()
                break

            else:
                await ws.send_text(json.dumps({"type":"error","message":f"unknown_type:{mtype}"}))

    except WebSocketDisconnect:
        pass
