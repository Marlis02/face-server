from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base
from dotenv import load_dotenv
import os

load_dotenv()

DB_BASE_URL = os.getenv("MASTER_DB_URL").rsplit("/", 1)[0]

TenantBase = declarative_base()

# Кэш движков — не создаём новое подключение на каждый запрос
_engines: dict = {}


def get_tenant_engine(db_name: str):
    if db_name not in _engines:
        url = f"{DB_BASE_URL}/{db_name}"
        _engines[db_name] = create_engine(url)
    return _engines[db_name]


def get_tenant_db(db_name: str):
    engine = get_tenant_engine(db_name)
    Session = sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
    )
    db = Session()
    try:
        yield db
    finally:
        db.close()


def create_tenant_database(db_name: str):
    """Создаёт новую базу данных для организации."""
    # Подключаемся к master_db чтобы создать новую базу
    master_url = os.getenv("MASTER_DB_URL")
    engine = create_engine(
        master_url,
        isolation_level="AUTOCOMMIT",
    )
    with engine.connect() as conn:
        conn.execute(text(f"CREATE DATABASE {db_name}"))
    engine.dispose()


def create_tenant_tables(db_name: str):
    """Создаёт таблицы в базе организации."""
    from models.tenant import TenantBase as TB
    engine = get_tenant_engine(db_name)
    TB.metadata.create_all(bind=engine)