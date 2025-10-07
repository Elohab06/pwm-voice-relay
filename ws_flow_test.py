import asyncio, websockets, json

async def main():
    uri = "ws://127.0.0.1:8000/ws"
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({"type":"session_start","locale":"tr-TR"}))
        print("-> session_start sent")
        print("<-", await ws.recv())  # assistant_say: Merhaba, hazırım.

        # Şimdilik ASR yok; doğrudan end_of_utterance simüle edelim
        await ws.send(json.dumps({"type":"end_of_utterance"}))
        print("<-", await ws.recv())  # function_call set_pwm 40
        print("<-", await ws.recv())  # assistant_say: PWM'i yüzde 40'a alıyorum.

        await ws.send(json.dumps({"type":"session_end"}))

if __name__ == "__main__":
    asyncio.run(main())
