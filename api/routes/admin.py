from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from core.logger import get_logger
from core.master_db import get_master_db
from core.tenant_db import create_tenant_database, create_tenant_tables
from core.security import hash_password
from models.master import Tenant
from models.tenant import User
from schemas.tenant import TenantCreate, TenantResponse
import os
import secrets

router = APIRouter()
logger = get_logger(__name__)

ADMIN_SECRET = os.getenv("ADMIN_SECRET", "supersecret")


def verify_admin_secret(x_admin_secret: str = Header(...)):
    """Простая защита через секретный ключ в заголовке."""
    if x_admin_secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Нет доступа")


@router.post("/tenants", response_model=TenantResponse)
def create_tenant(
    data: TenantCreate,
    master_db: Session = Depends(get_master_db),
    _: str = Depends(verify_admin_secret),
):
    """
    Создаёт новую организацию:
    1. Добавляет запись в master_db.tenants
    2. Создаёт новую базу данных
    3. Создаёт таблицы в новой базе
    4. Создаёт первого admin пользователя
    """

    # проверяем что slug уникален
    existing = master_db.query(Tenant).filter(
        Tenant.slug == data.slug
    ).first()
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Организация с slug '{data.slug}' уже существует"
        )

    # формируем имя базы данных
    db_name = f"tenant_{data.slug}"

    # создаём запись в master_db
    tenant = Tenant(
        slug=data.slug,
        name=data.name,
        type=data.type,
        db_name=db_name,
    )
    master_db.add(tenant)
    master_db.commit()
    master_db.refresh(tenant)

    # создаём базу данных
    try:
        create_tenant_database(db_name)
    except Exception as e:
        # если база уже существует — не страшно
        logger.warning("create_tenant_database %s: %s", db_name, e)

    # создаём таблицы в новой базе
    create_tenant_tables(db_name)

    # создаём первого admin пользователя
    from core.tenant_db import get_tenant_engine
    from sqlalchemy.orm import sessionmaker

    engine = get_tenant_engine(db_name)
    TenantSession = sessionmaker(bind=engine)
    tenant_db = TenantSession()

    try:
        admin = User(
            email=f"admin@attendance-{data.slug}.com",
            password_hash=hash_password("admin123"),
            first_name="Админ",
            last_name=data.name,
            role="admin",
        )
        tenant_db.add(admin)
        tenant_db.commit()
    finally:
        tenant_db.close()

    return tenant


@router.get("/tenants", response_model=list[TenantResponse])
def get_tenants(
    master_db: Session = Depends(get_master_db),
    _: str = Depends(verify_admin_secret),
):
    """Список всех организаций."""
    return master_db.query(Tenant).all()


@router.delete("/tenants/{slug}")
def delete_tenant(
    slug: str,
    master_db: Session = Depends(get_master_db),
    _: str = Depends(verify_admin_secret),
):
    """Деактивирует организацию."""
    tenant = master_db.query(Tenant).filter(
        Tenant.slug == slug
    ).first()

    if not tenant:
        raise HTTPException(status_code=404, detail="Организация не найдена")

    tenant.is_active = False
    master_db.commit()

    return {"message": f"Организация '{slug}' деактивирована"}