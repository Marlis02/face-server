from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from core.master_db import get_master_db
from core.security import decode_token
from models.master import Tenant

security = HTTPBearer(auto_error=False)


def get_current_user_payload(
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """
    Читает токен из заголовка Authorization.
    auto_error=False — не блокирует WebSocket роуты.
    Реальная проверка происходит здесь.
    """
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="Токен не указан"
        )

    token = credentials.credentials
    payload = decode_token(token)

    if payload is None:
        raise HTTPException(
            status_code=401,
            detail="Токен недействителен или истёк"
        )

    return payload


def get_tenant_by_slug(
    tenant_slug: str,
    master_db: Session = Depends(get_master_db),
) -> Tenant:
    tenant = master_db.query(Tenant).filter(
        Tenant.slug == tenant_slug,
        Tenant.is_active == True,
    ).first()

    if not tenant:
        raise HTTPException(
            status_code=404,
            detail=f"Организация '{tenant_slug}' не найдена"
        )

    return tenant


def get_current_user(
    payload: dict = Depends(get_current_user_payload),
    master_db: Session = Depends(get_master_db),
) -> dict:
    tenant_slug = payload.get("tenant_slug")
    user_id = payload.get("user_id")
    role = payload.get("role")

    if not tenant_slug or not user_id:
        raise HTTPException(status_code=401, detail="Неверный токен")

    tenant = master_db.query(Tenant).filter(
        Tenant.slug == tenant_slug,
        Tenant.is_active == True,
    ).first()

    if not tenant:
        raise HTTPException(status_code=401, detail="Организация не найдена")

    return {
        "user_id": user_id,
        "role": role,
        "tenant_slug": tenant_slug,
        "db_name": tenant.db_name,
    }


def require_role(*roles: str):
    def checker(current_user: dict = Depends(get_current_user)):
        if current_user["role"] not in roles:
            raise HTTPException(
                status_code=403,
                detail=f"Нет доступа. Требуется роль: {', '.join(roles)}"
            )
        return current_user
    return checker