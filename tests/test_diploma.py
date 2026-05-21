"""
Тестовый протокол ВКР (T-1 … T-12).

Каждый тест соответствует строке протокола и проверяет ровно её ожидание.
FaceService подменён фиктивным (см. conftest.py); реальная модель InsightFace
в тестах не используется — проверяется бизнес-логика backend'а.
"""
import json
import struct

import cv2
import numpy as np
from sqlalchemy.orm import sessionmaker

from conftest import (
    ADMIN_SECRET,
    DEFAULT_EMBEDDING,
    TEST_SLUG,
    _drop_database,
)
from core.tenant_db import get_tenant_engine
from models.master import Tenant
from models.tenant import Attendance, FaceEmbedding, User
from services.face_service import face_service


def _tiny_jpeg() -> bytes:
    """Минимальный валидный JPEG — чтобы cv2.imdecode не падал."""
    img = np.full((10, 10, 3), 128, dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


TINY_JPEG = _tiny_jpeg()


# ─── T-1. Создание учреждения ────────────────────────────────────────

def test_t01_create_tenant(client):
    """Запись в master_db, БД, таблицы, admin."""
    slug = "t1tenant"

    # на случай мусора с прошлого упавшего прогона
    from core.master_db import master_engine
    s = sessionmaker(bind=master_engine)()
    s.query(Tenant).filter(Tenant.slug == slug).delete()
    s.commit()
    s.close()
    _drop_database(f"tenant_{slug}")

    r = client.post(
        "/admin/tenants",
        json={"slug": slug, "name": "Учреждение T1", "type": "university"},
        headers={"X-Admin-Secret": ADMIN_SECRET},
    )
    try:
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["slug"] == slug
        assert body["db_name"] == f"tenant_{slug}"
        assert body["is_active"] is True

        # tenant-БД действительно создана и в ней есть admin
        eng = get_tenant_engine(f"tenant_{slug}")
        TS = sessionmaker(bind=eng)
        sdb = TS()
        admin = sdb.query(User).filter(User.role == "admin").first()
        assert admin is not None
        assert admin.email.endswith(f"-{slug}.com")
        sdb.close()
    finally:
        # teardown — НЕ должно мешать остальным тестам
        s = sessionmaker(bind=master_engine)()
        s.query(Tenant).filter(Tenant.slug == slug).delete()
        s.commit()
        s.close()
        _drop_database(f"tenant_{slug}")


# ─── T-2. Логин с верными данными ─────────────────────────────────────

def test_t02_login_ok(client, tenant_slug):
    """Пара access/refresh, role=admin."""
    r = client.post(
        f"/api/{tenant_slug}/login",
        json={"email": "admin@example.com", "password": "admin_pwd_123"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "access_token" in body and body["access_token"]
    assert "refresh_token" in body and body["refresh_token"]
    assert body["role"] == "admin"
    assert body["access_token"] != body["refresh_token"]


# ─── T-3. Логин с неверным паролем ────────────────────────────────────

def test_t03_login_bad_password(client, tenant_slug):
    """HTTP 401."""
    r = client.post(
        f"/api/{tenant_slug}/login",
        json={"email": "admin@example.com", "password": "WRONG_PASSWORD"},
    )
    assert r.status_code == 401, r.text
    assert "пароль" in r.json()["detail"].lower()


# ─── T-4. Фото без лица ──────────────────────────────────────────────

def test_t04_upload_photo_without_face(
    client, tenant_slug, admin_headers, user1_id, face_fake,
):
    """HTTP 400, «Лицо не найдено»."""
    # FakeFace вернёт None — будто лица нет
    face_fake.return_embedding = None
    # перенастраиваем поведение под "лица нет"
    original_get = face_service.get_embedding
    face_service.get_embedding = lambda image: None
    try:
        r = client.post(
            f"/api/{tenant_slug}/users/{user1_id}/face",
            headers=admin_headers,
            files={"file": ("face.jpg", TINY_JPEG, "image/jpeg")},
        )
        assert r.status_code == 400, r.text
        assert "лицо не найдено" in r.json()["detail"].lower()
    finally:
        face_service.get_embedding = original_get
        face_fake.return_embedding = DEFAULT_EMBEDDING.copy()


# ─── T-5. 6-я фотография ─────────────────────────────────────────────

def test_t05_face_limit_exceeded(
    client, db, tenant_slug, admin_headers, user1_id, face_fake,
):
    """HTTP 400, «Лимит 5/5»."""
    # пред-заполняем 5 эмбеддингов руками
    for _ in range(5):
        db.add(FaceEmbedding(
            user_id=user1_id,
            embedding=face_service.embedding_to_bytes(DEFAULT_EMBEDDING),
            quality_score=1.0,
        ))
    db.commit()

    r = client.post(
        f"/api/{tenant_slug}/users/{user1_id}/face",
        headers=admin_headers,
        files={"file": ("face.jpg", TINY_JPEG, "image/jpeg")},
    )
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert "лимит" in detail.lower()
    assert "5" in detail


# ─── T-6. Планшет с неверным токеном ────────────────────────────────

def test_t06_websocket_bad_token(client, tenant_slug):
    """Закрытие WS, код 4001."""
    from starlette.websockets import WebSocketDisconnect

    with client.websocket_connect(f"/api/{tenant_slug}/ws") as ws:
        ws.send_text(json.dumps({"token": "BAD_TOKEN_XYZ"}))
        msg = json.loads(ws.receive_text())
        assert msg["type"] == "error"
        assert "невалидный" in msg["message"].lower() or "токен" in msg["message"].lower()
        # сервер должен закрыть соединение
        try:
            ws.receive_text()
            assert False, "WebSocket должен был закрыться"
        except WebSocketDisconnect as exc:
            assert exc.code == 4001


# ─── Helper для WS-распознавания ─────────────────────────────────────

def _make_frame(tracking_id: int, payload: bytes = b"jpeg_fake_bytes") -> bytes:
    """[4 байта tracking_id big-endian][JPEG]"""
    return struct.pack(">i", tracking_id) + payload


def _connect_and_auth(client, tenant_slug, device_token):
    ws = client.websocket_connect(f"/api/{tenant_slug}/ws")
    conn = ws.__enter__()
    conn.send_text(json.dumps({"token": device_token}))
    hello = json.loads(conn.receive_text())
    assert hello["type"] == "connected", hello
    return ws, conn


# ─── T-7. Распознавание зарегистрированного ──────────────────────────

def test_t07_recognize_registered(
    client, db, tenant_slug, device_token, user1_id, face_fake,
):
    """auto, confidence ≥ 0.5."""
    # настраиваем "найден" с заведомо хорошим score
    face_fake.match_result = {"user_id": user1_id, "score": 0.87}

    ws_cm, ws = _connect_and_auth(client, tenant_slug, device_token)
    try:
        ws.send_bytes(_make_frame(1))
        result = json.loads(ws.receive_text())
        assert result["type"] == "result"
        assert result["status"] == "marked"
        assert result["user_id"] == user1_id
        assert result["confidence"] >= 0.5

        # запись в attendance с status='auto' появилась
        att = db.query(Attendance).filter(Attendance.user_id == user1_id).one()
        assert att.status == "auto"
    finally:
        ws_cm.__exit__(None, None, None)
        face_fake.match_result = None


# ─── T-8. Распознавание незарегистрированного ────────────────────────

def test_t08_recognize_unknown(client, tenant_slug, device_token, face_fake):
    """unknown."""
    face_fake.match_result = None

    ws_cm, ws = _connect_and_auth(client, tenant_slug, device_token)
    try:
        ws.send_bytes(_make_frame(2))
        result = json.loads(ws.receive_text())
        assert result["type"] == "result"
        assert result["status"] == "unknown"
        assert result["confidence"] == 0.0
    finally:
        ws_cm.__exit__(None, None, None)


# ─── T-9. Повторное распознавание ────────────────────────────────────

def test_t09_recognize_already_marked(
    client, db, tenant_slug, device_token, user1_id, face_fake,
):
    """already_marked, без дубликата."""
    face_fake.match_result = {"user_id": user1_id, "score": 0.91}

    ws_cm, ws = _connect_and_auth(client, tenant_slug, device_token)
    try:
        # первый — отмечаем
        ws.send_bytes(_make_frame(10))
        first = json.loads(ws.receive_text())
        assert first["status"] == "marked"

        # второй — должно быть already_marked
        ws.send_bytes(_make_frame(11))
        second = json.loads(ws.receive_text())
        assert second["status"] == "already_marked"
        assert second["user_id"] == user1_id

        # дубликата в БД нет
        count = db.query(Attendance).filter(
            Attendance.user_id == user1_id
        ).count()
        assert count == 1
    finally:
        ws_cm.__exit__(None, None, None)
        face_fake.match_result = None


# ─── T-10. Два планшета параллельно ──────────────────────────────────

def test_t10_two_tablets_parallel(
    client, db, tenant_slug, face_fake,
):
    """Обе сессии без блокировок."""
    # заводим второе устройство и второго юзера
    eng = get_tenant_engine(f"tenant_{TEST_SLUG}")
    s = sessionmaker(bind=eng)()
    from core.security import hash_password
    from models.tenant import Device

    s.add(Device(
        name="Tablet-2", login="tablet2",
        password_hash=hash_password("111111"),
        token="valid-test-device-token-2", is_active=True,
    ))
    u2 = User(
        email="user2@example.com",
        password_hash=hash_password("x"),
        first_name="Пётр", last_name="Петров", role="user",
    )
    s.add(u2)
    s.commit()
    u2_id = u2.id
    u1_id = s.query(User).filter(User.email == "user1@example.com").one().id
    s.close()

    # каждый вызов find_match возвращает разного юзера в зависимости от db_name
    # (в тестах оба планшета попадают в тот же tenant — отличаем по count)
    calls = {"n": 0}
    def mock_find(embedding, db_name, db):
        calls["n"] += 1
        return {"user_id": u1_id if calls["n"] % 2 == 1 else u2_id, "score": 0.9}
    face_service.find_match = mock_find

    try:
        with client.websocket_connect(f"/api/{tenant_slug}/ws") as ws1, \
             client.websocket_connect(f"/api/{tenant_slug}/ws") as ws2:
            ws1.send_text(json.dumps({"token": "valid-test-device-token"}))
            ws2.send_text(json.dumps({"token": "valid-test-device-token-2"}))
            assert json.loads(ws1.receive_text())["type"] == "connected"
            assert json.loads(ws2.receive_text())["type"] == "connected"

            ws1.send_bytes(_make_frame(101))
            ws2.send_bytes(_make_frame(202))

            r1 = json.loads(ws1.receive_text())
            r2 = json.loads(ws2.receive_text())

            assert r1["status"] == "marked"
            assert r2["status"] == "marked"
            assert {r1["user_id"], r2["user_id"]} == {u1_id, u2_id}
    finally:
        face_service.find_match = lambda *a, **k: None


# ─── T-11. GET /users менеджером ──────────────────────────────────────

def test_t11_manager_can_list_users(client, tenant_slug, manager_headers):
    """HTTP 200."""
    r = client.get(f"/api/{tenant_slug}/users", headers=manager_headers)
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)
    emails = {u["email"] for u in r.json()}
    assert {"admin@example.com", "manager@example.com", "user1@example.com"} <= emails


# ─── T-12. DELETE /users менеджером ──────────────────────────────────

def test_t12_manager_cannot_delete_user(
    client, tenant_slug, manager_headers, user1_id,
):
    """HTTP 403."""
    r = client.delete(
        f"/api/{tenant_slug}/users/{user1_id}",
        headers=manager_headers,
    )
    assert r.status_code == 403, r.text


# ─── T-13. Экспорт журнала в Excel ────────────────────────────────────

def test_t13_export_attendance_xlsx(
    client, db, tenant_slug, admin_headers, user1_id,
):
    """xlsx с заголовком и хотя бы одной строкой данных."""
    from datetime import date as _date
    db.add(Attendance(
        user_id=user1_id, date=_date.today(),
        status="manual", note="T-13",
    ))
    db.commit()

    r = client.get(
        f"/api/{tenant_slug}/attendance/export",
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument"
    )
    assert "attachment" in r.headers.get("content-disposition", "")
    # xlsx — это zip-архив, начинается с PK\x03\x04
    assert r.content[:2] == b"PK"
    assert len(r.content) > 500
