import asyncio
import websockets
import struct
import json

async def test():
    uri = "ws://localhost:8000/api/manas/ws"

    async with websockets.connect(uri) as ws:
        # шаг 1 — отправляем токен первым сообщением
        await ws.send(json.dumps({
            "token": "s1-6-cayFQfz-jKktTAbmUuen6Osv_1bxu4RrqgTOaU"
        }))

        # шаг 2 — получаем подтверждение подключения
        response = await ws.recv()
        print("Подключение:", response)

        # шаг 3 — отправляем фото
        with open("test_face.jpg", "rb") as f:
            jpeg_bytes = f.read()

        tracking_id = 1
        packet = struct.pack(">i", tracking_id) + jpeg_bytes
        await ws.send(packet)
        print(f"Отправлено: {len(packet)} байт")

        # шаг 4 — получаем результат
        result = await ws.recv()
        print("Результат:", result)

asyncio.run(test())