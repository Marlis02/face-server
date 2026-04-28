import asyncio
import json
import struct
import cv2
from datetime import date, datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from core.master_db import get_master_db
from core.tenant_db import get_tenant_db
from models.master import Tenant
from models.tenant import Device, User, Attendance
from services.face_service import face_service, executor

router = APIRouter()


async def authorize_device(
    websocket: WebSocket,
    tenant_slug: str,
) -> tuple | None:
    """
    Авторизует устройство по токену из первого сообщения.
    Возвращает (tenant, device_id, device_name) или None если ошибка.
    """

    try:
        raw = await asyncio.wait_for(
            websocket.receive_text(),
            timeout=10.0,
        )
    except asyncio.TimeoutError:
        await websocket.send_text(json.dumps({
            "type": "error",
            "message": "Таймаут авторизации",
        }))
        await websocket.close(code=4001)
        return None

    try:
        auth_data = json.loads(raw)
        token = auth_data.get("token")
    except json.JSONDecodeError:
        await websocket.send_text(json.dumps({
            "type": "error",
            "message": "Неверный формат — ожидается JSON с полем token",
        }))
        await websocket.close(code=4001)
        return None

    if not token:
        await websocket.send_text(json.dumps({
            "type": "error",
            "message": "Токен не указан",
        }))
        await websocket.close(code=4001)
        return None

    # находим организацию
    master_db: Session = next(get_master_db())
    try:
        tenant = master_db.query(Tenant).filter(
            Tenant.slug == tenant_slug,
            Tenant.is_active == True,
        ).first()
    finally:
        master_db.close()

    if not tenant:
        await websocket.send_text(json.dumps({
            "type": "error",
            "message": f"Организация '{tenant_slug}' не найдена",
        }))
        await websocket.close(code=4004)
        return None

    # находим устройство по токену
    tenant_db: Session = next(get_tenant_db(tenant.db_name))
    try:
        device = tenant_db.query(Device).filter(
            Device.token == token,
            Device.is_active == True,
        ).first()

        if not device:
            await websocket.send_text(json.dumps({
                "type": "error",
                "message": "Невалидный токен устройства",
            }))
            await websocket.close(code=4001)
            return None

        device.last_seen_at = datetime.now(timezone.utc)
        tenant_db.commit()

        device_id = device.id
        device_name = device.name
    finally:
        tenant_db.close()

    return tenant, device_id, device_name


async def process_frame(
    websocket: WebSocket,
    data: bytes,
    tenant: Tenant,
    device_id: int,
) -> None:
    """
    Обрабатывает один кадр с лицом.
    Тяжёлые операции (InsightFace) запускаются в отдельном потоке
    чтобы не блокировать event loop для других планшетов.
    """

    tracking_id = struct.unpack(">i", data[:4])[0]
    jpeg_bytes = data[4:]

    print(f"📸 [{tracking_id}] Получен кадр: {len(jpeg_bytes)} байт")

    loop = asyncio.get_event_loop()

    # декодируем JPEG в отдельном потоке
    image = await loop.run_in_executor(
        executor,
        face_service.decode_jpeg,
        jpeg_bytes,
    )
    if image is None:
        print(f"❌ [{tracking_id}] Не удалось декодировать изображение")
        await websocket.send_text(json.dumps({
            "type": "result",
            "tracking_id": tracking_id,
            "status": "error",
            "message": "Не удалось декодировать изображение",
        }))
        return

    print(f"🖼 [{tracking_id}] Декодировано: {image.shape}")
    
    if tracking_id <= 100:
        import os
        home = os.path.expanduser("~")
        save_path = os.path.join(home, f"debug_frame_{tracking_id}.jpg")
        cv2.imwrite(save_path, image)
        print(f"💾 [{tracking_id}] Сохранён: {save_path}")

    # получаем все эмбеддинги всех лиц на фото
    embeddings = await loop.run_in_executor(
        executor,
        face_service.get_all_embeddings,
        image,
    )

    if not embeddings:
        print(f"😶 [{tracking_id}] Лица не найдены")
        await websocket.send_text(json.dumps({
            "type": "result",
            "tracking_id": tracking_id,
            "status": "no_face",
            "message": "Лицо не найдено",
        }))
        return

    print(f"🧠 [{tracking_id}] Найдено лиц на фото: {len(embeddings)}")

    tenant_db: Session = next(get_tenant_db(tenant.db_name))
    try:
        # берём первый эмбеддинг — самое большое лицо
        embedding = embeddings[0]
        match = face_service.find_match(embedding, tenant.db_name, tenant_db)

        if not match:
            print(f"🔍 [{tracking_id}] Совпадение не найдено")
            await websocket.send_text(json.dumps({
                "type": "result",
                "tracking_id": tracking_id,
                "status": "unknown",
                "confidence": 0.0,
            }))
            return

        user_id = match["user_id"]
        confidence = match["score"]
        print(f"✨ [{tracking_id}] Найден user_id={user_id} confidence={confidence:.3f}")

        # находим пользователя
        user = tenant_db.query(User).filter(
            User.id == user_id,
            User.is_active == True,
        ).first()

        if not user:
            print(f"⚠️ [{tracking_id}] Пользователь user_id={user_id} не найден")
            await websocket.send_text(json.dumps({
                "type": "result",
                "tracking_id": tracking_id,
                "status": "unknown",
                "confidence": 0.0,
            }))
            return

        today = date.today()

        # проверяем уже отмечен или нет
        existing = tenant_db.query(Attendance).filter(
            Attendance.user_id == user_id,
            Attendance.date == today,
        ).first()

        if existing:
            print(f"🔁 [{tracking_id}] {user.full_name} уже отмечен сегодня")
            await websocket.send_text(json.dumps({
                "type": "result",
                "tracking_id": tracking_id,
                "status": "already_marked",
                "user_id": user_id,
                "name": user.full_name,
                "confidence": round(confidence, 3),
            }))
            return

        # записываем посещение
        attendance = Attendance(
            user_id=user_id,
            device_id=device_id,
            date=today,
            status="auto",
        )
        tenant_db.add(attendance)
        tenant_db.commit()

        print(f"✅ [{tracking_id}] Отмечен: {user.full_name} ({confidence:.3f})")

        await websocket.send_text(json.dumps({
            "type": "result",
            "tracking_id": tracking_id,
            "status": "marked",
            "user_id": user_id,
            "name": user.full_name,
            "confidence": round(confidence, 3),
        }))

    finally:
        tenant_db.close()


@router.websocket("/api/{tenant_slug}/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    tenant_slug: str,
):
    """
    WebSocket для планшета.
    Шаг 1: подключиться
    Шаг 2: отправить {"token": "xxx"}
    Шаг 3: получить {"type": "connected"}
    Шаг 4: отправлять кадры [4 байта tracking_id][JPEG]
    Шаг 5: получать результаты JSON
    """

    await websocket.accept()

    device_name = "unknown"

    try:
        # авторизация
        result = await authorize_device(websocket, tenant_slug)
        if result is None:
            return

        tenant, device_id, device_name = result

        # сообщаем об успешном подключении
        await websocket.send_text(json.dumps({
            "type": "connected",
            "message": f"Подключено к {tenant.name}",
            "device_id": device_id,
            "device_name": device_name,
        }))

        print(f"✅ Планшет подключён: {device_name} ({tenant.name})")

        # основной цикл
        while True:
            data = await websocket.receive_bytes()

            if len(data) < 5:
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "message": "Данные слишком маленькие",
                }))
                continue

            await process_frame(websocket, data, tenant, device_id)

    except WebSocketDisconnect:
        print(f"❌ Планшет отключился: {device_name}")
    except Exception as e:
        print(f"❌ Ошибка WebSocket [{device_name}]: {e}")
        try:
            await websocket.send_text(json.dumps({
                "type": "error",
                "message": str(e),
            }))
        except Exception:
            pass