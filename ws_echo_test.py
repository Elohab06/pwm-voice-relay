import asyncio, websockets, json

async def main():
    uri = "ws://127.0.0.1:8000/ws"
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({"type":"session_start","locale":"tr-TR"}))
        echo = await ws.recv()
        print("Echo:", echo)

if __name__ == "__main__":
    asyncio.run(main())
