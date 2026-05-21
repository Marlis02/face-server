import asyncio
import json
import struct
from datetime import date, datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from core.logger import get_logger
from core.master_db import get_master_db
from core.tenant_db import get_tenant_db
from models.master import Tenant
from models.tenant import Device, User, Attendance
from services.face_service import face_service, executor

router = APIRouter()
logger = get_logger(__name__)


async def authorize_device(
    websocket: WebSocket,
    tenant_slug: str,
) -> tuple | None:
    """
    Авторизует устройство по токену из первого сообщения.
    Возвращает (tenant, device_id, device_name) или None если ошибка.
    """

    client_ip = websocket.client.host if websocket.client else "?"

    try:
        raw = await asyncio.wait_for(
            websocket.receive_text(),
            timeout=10.0,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "ws auth failed tenant=%s ip=%s reason=timeout",
            tenant_slug, client_ip,
        )
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
        logger.warning(
            "ws auth failed tenant=%s ip=%s reason=bad_json",
            tenant_slug, client_ip,
        )
        await websocket.send_text(json.dumps({
            "type": "error",
            "message": "Неверный формат — ожидается JSON с полем token",
        }))
        await websocket.close(code=4001)
        return None

    if not token:
        logger.warning(
            "ws auth failed tenant=%s ip=%s reason=no_token",
            tenant_slug, client_ip,
        )
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
        logger.warning(
            "ws auth failed tenant=%s ip=%s reason=tenant_not_found",
            tenant_slug, client_ip,
        )
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
            logger.warning(
                "ws auth failed tenant=%s ip=%s reason=bad_token",
                tenant_slug, client_ip,
            )
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

    logger.debug("frame received tracking_id=%d bytes=%d", tracking_id, len(jpeg_bytes))

    loop = asyncio.get_event_loop()

    # декодируем JPEG в отдельном потоке
    image = await loop.run_in_executor(
        executor,
        face_service.decode_jpeg,
        jpeg_bytes,
    )
    if image is None:
        logger.warning("decode failed tracking_id=%d", tracking_id)
        await websocket.send_text(json.dumps({
            "type": "result",
            "tracking_id": tracking_id,
            "status": "error",
            "message": "Не удалось декодировать изображение",
        }))
        return

    # получаем все эмбеддинги всех лиц на фото
    # ВНИМАНИЕ: исходные кадры лиц НЕ сохраняются на диск (§3.1 ВКР).
    # На сервере остаётся только биометрический эмбеддинг.
    embeddings = await loop.run_in_executor(
        executor,
        face_service.get_all_embeddings,
        image,
    )

    if not embeddings:
        logger.info("no face tracking_id=%d", tracking_id)
        await websocket.send_text(json.dumps({
            "type": "result",
            "tracking_id": tracking_id,
            "status": "no_face",
            "message": "Лицо не найдено",
        }))
        return

    logger.debug("faces detected tracking_id=%d count=%d", tracking_id, len(embeddings))

    tenant_db: Session = next(get_tenant_db(tenant.db_name))
    try:
        # берём первый эмбеддинг — самое большое лицо
        embedding = embeddings[0]
        match = face_service.find_match(embedding, tenant.db_name, tenant_db)

        if not match:
            logger.info("no match tracking_id=%d", tracking_id)
            await websocket.send_text(json.dumps({
                "type": "result",
                "tracking_id": tracking_id,
                "status": "unknown",
                "confidence": 0.0,
            }))
            return

        user_id = match["user_id"]
        confidence = match["score"]
        logger.info(
            "match tracking_id=%d user_id=%s score=%.3f",
            tracking_id, user_id, confidence,
        )

        # находим пользователя
        user = tenant_db.query(User).filter(
            User.id == user_id,
            User.is_active == True,
        ).first()

        if not user:
            logger.warning(
                "matched user not found tracking_id=%d user_id=%s",
                tracking_id, user_id,
            )
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
            logger.info(
                "already marked tracking_id=%d user_id=%s",
                tracking_id, user_id,
            )
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

        logger.info(
            "attendance marked user_id=%s device_id=%s score=%.3f",
            user_id, device_id, confidence,
        )

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

        logger.info(
            "device connected device_id=%s name=%s tenant=%s",
            device_id, device_name, tenant.slug,
        )

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
        logger.info("device disconnected device=%s", device_name)
    except Exception as e:
        logger.exception("websocket error device=%s: %s", device_name, e)
        try:
            await websocket.send_text(json.dumps({
                "type": "error",
                "message": str(e),
            }))
        except Exception:
            pass