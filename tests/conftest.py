"""
Общие фикстуры pytest.

ВАЖНО: .env.test грузится до любых импортов проекта, иначе
core.master_db создаст engine на dev-БД.

Стратегия:
- Один tenant создаётся на сессию ('test_diploma').
- FaceService подменяется на FakeFaceService (модель InsightFace не грузится).
- В каждом тесте, где нужно — берём токены admin/manager из фикстур.
"""
import os
import sys
from pathlib import Path

import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

# ─── env ДО любых импортов проекта ────────────────────────────────────
TEST_ENV = Path(__file__).parent / ".env.test"
load_dotenv(TEST_ENV, override=True)

# гарантируем что корень проекта в sys.path при запуске из любого места
ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ─── теперь можно импортировать проект ────────────────────────────────
import numpy as np  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core.master_db import MasterBase, master_engine  # noqa: E402
from core.security import hash_password  # noqa: E402
from core.tenant_db import (  # noqa: E402
    create_tenant_database,
    create_tenant_tables,
    get_tenant_engine,
)
from models import master as _master_models  # noqa: E402, F401 — регистрирует модели
from models.master import Tenant  # noqa: E402
from models.tenant import Device, FaceEmbedding, User  # noqa: E402
from services.face_service import face_service  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402


TEST_SLUG = "testdiploma"
TEST_DB_NAME = f"tenant_{TEST_SLUG}"
ADMIN_SECRET = os.getenv("ADMIN_SECRET")
DEFAULT_EMBEDDING = np.ones(512, dtype=np.float32) / np.sqrt(512)


# ─── Fake FaceService ─────────────────────────────────────────────────

class _FakeFace:
    """
    Подмена методов InsightFace.

    Поведение настраивается через атрибуты на инстансе:
      face_fake.return_embedding  — что вернёт get_embedding/get_all_embeddings
      face_fake.match_result      — что вернёт find_match (dict или None)
    """

    def __init__(self):
        self.return_embedding = DEFAULT_EMBEDDING.copy()
        self.match_result: dict | None = None

    def initialize(self):  # модель не грузим
        face_service._initialized = True

    def get_embedding(self, image):
        return self.return_embedding

    def get_all_embeddings(self, image):
        if self.return_embedding is None:
            return []
        return [self.return_embedding]

    def find_match(self, embedding, db_name, db):
        return self.match_result

    def decode_jpeg(self, jpeg_bytes):
        # любое не-None изображение проходит дальше
        return np.zeros((100, 100, 3), dtype=np.uint8)


@pytest.fixture(scope="session")
def face_fake():
    """Подменяет методы face_service на детерминированные.
    Тесты управляют поведением через face_fake.return_embedding / match_result."""
    fake = _FakeFace()
    originals = {}
    for name in (
        "initialize", "get_embedding", "get_all_embeddings",
        "find_match", "decode_jpeg",
    ):
        originals[name] = getattr(face_service, name)
        setattr(face_service, name, getattr(fake, name))

    face_service._initialized = True

    yield fake

    for name, fn in originals.items():
        setattr(face_service, name, fn)


# ─── DB lifecycle ─────────────────────────────────────────────────────

def _drop_database(db_name: str) -> None:
    """Дроп tenant-БД через AUTOCOMMIT-подключение к master."""
    engine = create_engine(
        os.getenv("MASTER_DB_URL"),
        isolation_level="AUTOCOMMIT",
    )
    with engine.connect() as conn:
        conn.execute(text(
            f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname = '{db_name}' AND pid <> pg_backend_pid()"
        ))
        conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
    engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def setup_test_database(face_fake):
    """
    Один раз на сессию:
    1. Создаём таблицы master.
    2. Создаём tenant-БД и таблицы.
    3. Сидим первичных пользователей (admin/manager/user) + одно устройство.
    """
    # master_engine берёт MASTER_DB_URL из env уже загруженного .env.test
    MasterBase.metadata.create_all(bind=master_engine)

    master_session = sessionmaker(bind=master_engine)()

    # на случай если предыдущий прогон упал — чистим
    existing = master_session.query(Tenant).filter(
        Tenant.slug == TEST_SLUG
    ).first()
    if existing:
        master_session.delete(existing)
        master_session.commit()
        _drop_database(TEST_DB_NAME)

    tenant = Tenant(
        slug=TEST_SLUG,
        name="Тестовое учреждение",
        type="university",
        db_name=TEST_DB_NAME,
    )
    master_session.add(tenant)
    master_session.commit()
    master_session.close()

    create_tenant_database(TEST_DB_NAME)
    create_tenant_tables(TEST_DB_NAME)

    # начальные пользователи
    tenant_engine = get_tenant_engine(TEST_DB_NAME)
    TenantSession = sessionmaker(bind=tenant_engine)
    db = TenantSession()
    try:
        db.add_all([
            User(
                email="admin@example.com",
                password_hash=hash_password("admin_pwd_123"),
                first_name="Админ",
                last_name="Тестов",
                role="admin",
            ),
            User(
                email="manager@example.com",
                password_hash=hash_password("manager_pwd_123"),
                first_name="Менеджер",
                last_name="Тестов",
                role="manager",
            ),
            User(
                email="user1@example.com",
                password_hash=hash_password("user_pwd_123"),
                first_name="Иван",
                last_name="Иванов",
                role="user",
            ),
        ])
        db.add(Device(
            name="Тестовый планшет",
            login="tablet1",
            password_hash=hash_password("123456"),
            token="valid-test-device-token",
            is_active=True,
        ))
        db.commit()
    finally:
        db.close()

    yield  # тесты выполняются

    # teardown
    master_session = sessionmaker(bind=master_engine)()
    master_session.query(Tenant).filter(Tenant.slug == TEST_SLUG).delete()
    master_session.commit()
    master_session.close()
    _drop_database(TEST_DB_NAME)


@pytest.fixture
def db():
    """Свежая сессия к tenant-БД для подготовки/проверки данных."""
    engine = get_tenant_engine(TEST_DB_NAME)
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture(autouse=True)
def clean_attendance_and_extras(db):
    """Чистим attendances и временных юзеров/устройств между тестами,
    чтобы T-7/T-9/T-5 не мешали друг другу."""
    from models.tenant import Attendance
    db.query(Attendance).delete()
    # удаляем юзеров, которые были созданы только для текущего теста
    db.query(FaceEmbedding).filter(
        ~FaceEmbedding.user_id.in_(
            db.query(User.id).filter(User.email.in_([
                "admin@example.com", "manager@example.com", "user1@example.com",
            ]))
        )
    ).delete(synchronize_session=False)
    db.query(User).filter(
        ~User.email.in_([
            "admin@example.com", "manager@example.com", "user1@example.com",
        ])
    ).delete(synchronize_session=False)
    db.query(FaceEmbedding).delete()
    db.commit()
    yield


# ─── App / client ─────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def app(face_fake):
    """Импортируем main и отключаем rate-limit чтобы не упереться в 10/min."""
    from main import app as fastapi_app
    fastapi_app.state.limiter.enabled = False
    return fastapi_app


@pytest.fixture
def client(app):
    """TestClient без with — lifespan не запускается, что нам и нужно
    (FaceService уже застаблен, БД уже готова)."""
    return TestClient(app)


# ─── Логин-помощники ─────────────────────────────────────────────────

def _login(client: TestClient, email: str, password: str) -> str:
    r = client.post(
        f"/api/{TEST_SLUG}/login",
        json={"email": email, "password": password},
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture
def admin_token(client):
    return _login(client, "admin@example.com", "admin_pwd_123")


@pytest.fixture
def manager_token(client):
    return _login(client, "manager@example.com", "manager_pwd_123")


@pytest.fixture
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture
def manager_headers(manager_token):
    return {"Authorization": f"Bearer {manager_token}"}


@pytest.fixture
def tenant_slug():
    return TEST_SLUG


@pytest.fixture
def user1_id(db):
    return db.query(User).filter(User.email == "user1@example.com").one().id


@pytest.fixture
def device_token():
    """Валидный токен устройства, забит в seed."""
    return "valid-test-device-token"
