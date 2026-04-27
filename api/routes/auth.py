from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from core.tenant_db import get_tenant_db
from core.master_db import get_master_db
from core.security import verify_password, create_access_token, create_refresh_token, decode_token
from core.dependencies import get_tenant_by_slug, get_current_user
from models.master import Tenant
from models.tenant import User
from schemas.auth import LoginRequest, TokenResponse, RefreshRequest

router = APIRouter()


@router.post("/{tenant_slug}/login", response_model=TokenResponse)
def login(
    tenant_slug: str,
    data: LoginRequest,
    tenant: Tenant = Depends(get_tenant_by_slug),
):
    """
    Логин пользователя.
    URL: /kgtu/login
    Сервер по slug находит организацию и подключается к её базе.
    """

    # подключаемся к базе организации
    tenant_db: Session = next(get_tenant_db(tenant.db_name))

    try:
        # ищем пользователя по email
        user = tenant_db.query(User).filter(
            User.email == data.email,
            User.is_active == True,
        ).first()

        if not user:
            raise HTTPException(
                status_code=401,
                detail="Неверный email или пароль"
            )

        # проверяем пароль
        if not verify_password(data.password, user.password_hash):
            raise HTTPException(
                status_code=401,
                detail="Неверный email или пароль"
            )

        # данные для токена
        token_data = {
            "user_id": user.id,
            "role": user.role,
            "tenant_slug": tenant_slug,
        }

        return TokenResponse(
            access_token=create_access_token(token_data),
            refresh_token=create_refresh_token(token_data),
            role=user.role,
            user_id=user.id,
            full_name=user.full_name,
        )
    finally:
        tenant_db.close()


@router.post("/refresh", response_model=TokenResponse)
def refresh_token(
    data: RefreshRequest,
    master_db: Session = Depends(__import__('core.master_db', fromlist=['get_master_db']).get_master_db),
):
    """
    Обновляет access_token через refresh_token.
    """
    payload = decode_token(data.refresh_token)

    if not payload:
        raise HTTPException(status_code=401, detail="Refresh токен недействителен")

    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Неверный тип токена")

    tenant_slug = payload.get("tenant_slug")
    user_id = payload.get("user_id")

    # находим организацию
    from models.master import Tenant
    from core.master_db import get_master_db
    master_db: Session = next(get_master_db())

    try:
        tenant = master_db.query(Tenant).filter(
            Tenant.slug == tenant_slug,
            Tenant.is_active == True,
        ).first()

        if not tenant:
            raise HTTPException(status_code=401, detail="Организация не найдена")

        # находим пользователя
        tenant_db: Session = next(get_tenant_db(tenant.db_name))
        try:
            user = tenant_db.query(User).filter(
                User.id == user_id,
                User.is_active == True,
            ).first()

            if not user:
                raise HTTPException(status_code=401, detail="Пользователь не найден")

            token_data = {
                "user_id": user.id,
                "role": user.role,
                "tenant_slug": tenant_slug,
            }

            return TokenResponse(
                access_token=create_access_token(token_data),
                refresh_token=create_refresh_token(token_data),
                role=user.role,
                user_id=user.id,
                full_name=user.full_name,
            )
        finally:
            tenant_db.close()
    finally:
        master_db.close()


@router.get("/{tenant_slug}/me")
def get_me(
    tenant_slug: str,
    tenant: Tenant = Depends(get_tenant_by_slug),
    current_user: dict = Depends(get_current_user),
):
    tenant_db: Session = next(get_tenant_db(tenant.db_name))

    try:
        user = tenant_db.query(User).filter(
            User.id == current_user["user_id"],
        ).first()

        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")

        return {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
            "department_id": user.department_id,
            "group_id": user.group_id,
        }
    finally:
        tenant_db.close()