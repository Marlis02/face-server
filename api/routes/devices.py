from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from core.tenant_db import get_tenant_db
from core.master_db import get_master_db
from core.dependencies import require_role
from core.security import hash_password, verify_password
from models.tenant import Device, Attendance
from models.master import Tenant
from schemas.device import (
    DeviceCreate, DeviceUpdate, DeviceResponse,
    DeviceLoginRequest, DeviceLoginResponse,
    DeviceInitResponse,
)
from datetime import datetime, timezone
import secrets

router = APIRouter()


def get_device_by_token(
    x_device_token: str = Header(...),
    tenant_slug: str = None,
    master_db: Session = Depends(get_master_db),
):
    """Dependency — проверяет device token из заголовка."""
    tenant = master_db.query(Tenant).filter(
        Tenant.slug == tenant_slug,
        Tenant.is_active == True,
    ).first()

    if not tenant:
        raise HTTPException(status_code=404, detail="Организация не найдена")

    tenant_db: Session = next(get_tenant_db(tenant.db_name))

    device = tenant_db.query(Device).filter(
        Device.token == x_device_token,
        Device.is_active == True,
    ).first()

    if not device:
        raise HTTPException(status_code=401, detail="Невалидный токен устройства")

    # обновляем last_seen_at
    device.last_seen_at = datetime.now(timezone.utc)
    tenant_db.commit()

    return {"device": device, "tenant": tenant, "tenant_db": tenant_db}


# ─── CRUD устройств (для admin) ───────────────────────────────

@router.post("/{tenant_slug}/devices", response_model=DeviceResponse)
def create_device(
    tenant_slug: str,
    data: DeviceCreate,
    current_user: dict = Depends(require_role("admin")),
):
    """Создать устройство. Только admin."""

    tenant_db: Session = next(get_tenant_db(current_user["db_name"]))

    try:
        # проверяем уникальность login
        existing = tenant_db.query(Device).filter(
            Device.login == data.login
        ).first()
        if existing:
            raise HTTPException(
                status_code=400,
                detail="Устройство с таким логином уже существует"
            )

        device = Device(
            name=data.name,
            login=data.login,
            password_hash=hash_password(data.password),
            location=data.location,
        )
        tenant_db.add(device)
        tenant_db.commit()
        tenant_db.refresh(device)

        return device
    finally:
        tenant_db.close()


@router.get("/{tenant_slug}/devices", response_model=list[DeviceResponse])
def get_devices(
    tenant_slug: str,
    current_user: dict = Depends(require_role("admin", "manager")),
):
    """Список всех устройств."""

    tenant_db: Session = next(get_tenant_db(current_user["db_name"]))

    try:
        return tenant_db.query(Device).all()
    finally:
        tenant_db.close()


@router.patch("/{tenant_slug}/devices/{device_id}", response_model=DeviceResponse)
def update_device(
    tenant_slug: str,
    device_id: int,
    data: DeviceUpdate,
    current_user: dict = Depends(require_role("admin")),
):
    """Обновить устройство. Только admin."""

    tenant_db: Session = next(get_tenant_db(current_user["db_name"]))

    try:
        device = tenant_db.query(Device).filter(
            Device.id == device_id
        ).first()

        if not device:
            raise HTTPException(status_code=404, detail="Устройство не найдено")

        if data.name is not None:
            device.name = data.name
        if data.login is not None:
            existing = tenant_db.query(Device).filter(
                Device.login == data.login,
                Device.id != device_id,
            ).first()
            if existing:
                raise HTTPException(
                    status_code=400,
                    detail="Логин уже используется"
                )
            device.login = data.login
        if data.password is not None:
            device.password_hash = hash_password(data.password)
        if data.location is not None:
            device.location = data.location
        if data.is_active is not None:
            device.is_active = data.is_active

        tenant_db.commit()
        tenant_db.refresh(device)

        return device
    finally:
        tenant_db.close()


@router.delete("/{tenant_slug}/devices/{device_id}")
def delete_device(
    tenant_slug: str,
    device_id: int,
    current_user: dict = Depends(require_role("admin")),
):
    """Удалить устройство. Только admin."""

    tenant_db: Session = next(get_tenant_db(current_user["db_name"]))

    try:
        device = tenant_db.query(Device).filter(
            Device.id == device_id
        ).first()

        if not device:
            raise HTTPException(status_code=404, detail="Устройство не найдено")

        tenant_db.delete(device)
        tenant_db.commit()

        return {"message": f"Устройство '{device.name}' удалено"}
    finally:
        tenant_db.close()


# ─── Авторизация устройства (для планшета) ────────────────────

@router.post("/{tenant_slug}/devices/login", response_model=DeviceLoginResponse)
def device_login(
    tenant_slug: str,
    data: DeviceLoginRequest,
    master_db: Session = Depends(get_master_db),
):
    """
    Логин планшета.
    Планшет отправляет login + password.
    Получает device_token для дальнейших запросов.
    """

    tenant = master_db.query(Tenant).filter(
        Tenant.slug == tenant_slug,
        Tenant.is_active == True,
    ).first()

    if not tenant:
        raise HTTPException(status_code=404, detail="Организация не найдена")

    tenant_db: Session = next(get_tenant_db(tenant.db_name))

    try:
        device = tenant_db.query(Device).filter(
            Device.login == data.login,
            Device.is_active == True,
        ).first()

        if not device or not verify_password(data.password, device.password_hash):
            raise HTTPException(
                status_code=401,
                detail="Неверный логин или пароль"
            )

        # генерируем новый токен при каждом логине
        device.token = secrets.token_urlsafe(32)
        device.last_seen_at = datetime.now(timezone.utc)
        tenant_db.commit()

        return DeviceLoginResponse(
            device_token=device.token,
            device_id=device.id,
            device_name=device.name,
        )
    finally:
        tenant_db.close()


@router.get("/{tenant_slug}/devices/init", response_model=DeviceInitResponse)
def device_init(
    tenant_slug: str,
    master_db: Session = Depends(get_master_db),
    x_device_token: str = Header(...),
):
    """
    Инициализация планшета.
    Планшет отправляет device_token.
    Получает информацию об организации и устройстве.
    """

    tenant = master_db.query(Tenant).filter(
        Tenant.slug == tenant_slug,
        Tenant.is_active == True,
    ).first()

    if not tenant:
        raise HTTPException(status_code=404, detail="Организация не найдена")

    tenant_db: Session = next(get_tenant_db(tenant.db_name))

    try:
        device = tenant_db.query(Device).filter(
            Device.token == x_device_token,
            Device.is_active == True,
        ).first()

        if not device:
            raise HTTPException(
                status_code=401,
                detail="Невалидный токен устройства"
            )

        # обновляем last_seen_at
        device.last_seen_at = datetime.now(timezone.utc)
        tenant_db.commit()

        return DeviceInitResponse(
            device_id=device.id,
            device_name=device.name,
            location=device.location,
            organization_name=tenant.name,
            organization_type=tenant.type,
        )
    finally:
        tenant_db.close()
        
@router.post("/{tenant_slug}/devices/{device_id}/reset-password")
def reset_device_password(
    tenant_slug: str,
    device_id: int,
    current_user: dict = Depends(require_role("admin")),
):
    """Сбросить пароль устройства. Только admin."""

    tenant_db: Session = next(get_tenant_db(current_user["db_name"]))

    try:
        device = tenant_db.query(Device).filter(
            Device.id == device_id
        ).first()

        if not device:
            raise HTTPException(status_code=404, detail="Устройство не найдено")

        # генерируем новый пароль
        import secrets
        new_password = secrets.token_urlsafe(8)

        device.password_hash = hash_password(new_password)
        # сбрасываем токен — устройство должно перелогиниться
        device.token = None
        tenant_db.commit()

        return {
            "message": f"Пароль устройства '{device.name}' сброшен",
            "login": device.login,
            "new_password": new_password,
        }
    finally:
        tenant_db.close()