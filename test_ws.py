import asyncio
import websockets
import struct
import json
import time

async def test():
    uri = "ws://localhost:8000/api/manas/ws"

    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({
            "token": "s1-6-cayFQfz-jKktTAbmUuen6Osv_1bxu4RrqgTOaU"
        }))

        response = await ws.recv()
        data = json.loads(response)
        print("Подключение:", json.dumps(data, ensure_ascii=False, indent=2))

        with open("/home/marlis/Documents/KSTU/diplom/attendance-backend/z-test/4.jpeg", "rb") as f:
            jpeg_bytes = f.read()

        # отправляем 3 раза — смотрим как меняется скорость
        for i in range(1, 4):
            tracking_id = i
            packet = struct.pack(">i", tracking_id) + jpeg_bytes

            start = time.perf_counter()
            await ws.send(packet)

            result = await ws.recv()
            elapsed_ms = (time.perf_counter() - start) * 1000

            data = json.loads(result)
            print(f"\nЗапрос {i}:")
            print(json.dumps(data, ensure_ascii=False, indent=2))
            print(f"Время: {elapsed_ms:.1f} мс")

asyncio.run(test())