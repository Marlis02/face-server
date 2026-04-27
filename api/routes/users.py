from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from core.tenant_db import get_tenant_db
from core.security import hash_password
from core.dependencies import get_current_user, require_role
from models.tenant import User, Department, Group
from schemas.user import UserCreate, UserUpdate, UserResponse ,ChangePasswordRequest
from core.security import hash_password, verify_password

router = APIRouter()


@router.post("/{tenant_slug}/users", response_model=UserResponse)
def create_user(
    tenant_slug: str,
    data: UserCreate,
    current_user: dict = Depends(require_role("admin")),
):
    """Создать пользователя. Только admin."""

    # проверяем что role валидная
    if data.role not in ["admin", "manager", "user"]:
        raise HTTPException(
            status_code=400,
            detail="Роль должна быть: admin, manager, user"
        )

    tenant_db: Session = next(get_tenant_db(current_user["db_name"]))

    try:
        # проверяем что email не занят
        existing = tenant_db.query(User).filter(
            User.email == data.email
        ).first()
        if existing:
            raise HTTPException(
                status_code=400,
                detail="Пользователь с таким email уже существует"
            )

        # проверяем что department существует
        if data.department_id:
            dept = tenant_db.query(Department).filter(
                Department.id == data.department_id
            ).first()
            if not dept:
                raise HTTPException(
                    status_code=404,
                    detail="Отдел не найден"
                )

        # проверяем что group существует
        if data.group_id:
            group = tenant_db.query(Group).filter(
                Group.id == data.group_id
            ).first()
            if not group:
                raise HTTPException(
                    status_code=404,
                    detail="Группа не найдена"
                )

        user = User(
            email=data.email,
            password_hash=hash_password(data.password),
            first_name=data.first_name,
            last_name=data.last_name,
            role=data.role,
            department_id=data.department_id,
            group_id=data.group_id,
        )
        tenant_db.add(user)
        tenant_db.commit()
        tenant_db.refresh(user)

        return user
    finally:
        tenant_db.close()


@router.get("/{tenant_slug}/users", response_model=list[UserResponse])
def get_users(
    tenant_slug: str,
    current_user: dict = Depends(require_role("admin", "manager")),
):
    """Список всех пользователей. Admin и manager."""

    tenant_db: Session = next(get_tenant_db(current_user["db_name"]))

    try:
        return tenant_db.query(User).all()
    finally:
        tenant_db.close()


@router.get("/{tenant_slug}/users/{user_id}", response_model=UserResponse)
def get_user(
    tenant_slug: str,
    user_id: int,
    current_user: dict = Depends(require_role("admin", "manager")),
):
    """Получить пользователя по id."""

    tenant_db: Session = next(get_tenant_db(current_user["db_name"]))

    try:
        user = tenant_db.query(User).filter(User.id == user_id).first()

        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")

        return user
    finally:
        tenant_db.close()


@router.patch("/{tenant_slug}/users/{user_id}", response_model=UserResponse)
def update_user(
    tenant_slug: str,
    user_id: int,
    data: UserUpdate,
    current_user: dict = Depends(require_role("admin")),
):
    """Обновить пользователя. Только admin."""

    tenant_db: Session = next(get_tenant_db(current_user["db_name"]))

    try:
        user = tenant_db.query(User).filter(User.id == user_id).first()

        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")

        if data.email is not None:
            existing = tenant_db.query(User).filter(
                User.email == data.email,
                User.id != user_id,
            ).first()
            if existing:
                raise HTTPException(
                    status_code=400,
                    detail="Этот email уже используется"
                )
            user.email = data.email

        if data.first_name is not None:
            user.first_name = data.first_name

        if data.last_name is not None:
            user.last_name = data.last_name

        if data.role is not None:
            if data.role not in ["admin", "manager", "user"]:
                raise HTTPException(
                    status_code=400,
                    detail="Роль должна быть: admin, manager, user"
                )
            user.role = data.role

        if data.department_id is not None:
            dept = tenant_db.query(Department).filter(
                Department.id == data.department_id
            ).first()
            if not dept:
                raise HTTPException(
                    status_code=404,
                    detail="Отдел не найден"
                )
            user.department_id = data.department_id

        if data.group_id is not None:
            group = tenant_db.query(Group).filter(
                Group.id == data.group_id
            ).first()
            if not group:
                raise HTTPException(
                    status_code=404,
                    detail="Группа не найдена"
                )
            user.group_id = data.group_id

        if data.is_active is not None:
            user.is_active = data.is_active

        tenant_db.commit()
        tenant_db.refresh(user)

        return user
    finally:
        tenant_db.close()


@router.delete("/{tenant_slug}/users/{user_id}")
def delete_user(
    tenant_slug: str,
    user_id: int,
    current_user: dict = Depends(require_role("admin")),
):
    """Удалить пользователя. Только admin."""

    # нельзя удалить самого себя
    if user_id == current_user["user_id"]:
        raise HTTPException(
            status_code=400,
            detail="Нельзя удалить самого себя"
        )

    tenant_db: Session = next(get_tenant_db(current_user["db_name"]))

    try:
        user = tenant_db.query(User).filter(User.id == user_id).first()

        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")

        tenant_db.delete(user)
        tenant_db.commit()

        return {"message": f"Пользователь {user.full_name} удалён"}
    finally:
        tenant_db.close()
        
    


@router.post("/{tenant_slug}/users/change-password")
def change_password(
    tenant_slug: str,
    data: ChangePasswordRequest,
    current_user: dict = Depends(get_current_user),
):
    """
    Текущий пользователь меняет свой пароль.
    Доступно всем авторизованным.
    """

    tenant_db: Session = next(get_tenant_db(current_user["db_name"]))

    try:
        user = tenant_db.query(User).filter(
            User.id == current_user["user_id"]
        ).first()

        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")

        # проверяем старый пароль
        if not verify_password(data.old_password, user.password_hash):
            raise HTTPException(
                status_code=400,
                detail="Неверный текущий пароль"
            )

        # проверяем что новый пароль не совпадает со старым
        if data.old_password == data.new_password:
            raise HTTPException(
                status_code=400,
                detail="Новый пароль должен отличаться от старого"
            )

        # проверяем длину нового пароля
        if len(data.new_password) < 6:
            raise HTTPException(
                status_code=400,
                detail="Пароль должен быть не менее 6 символов"
            )

        user.password_hash = hash_password(data.new_password)
        tenant_db.commit()

        return {"message": "Пароль успешно изменён"}
    finally:
        tenant_db.close()


@router.post("/{tenant_slug}/users/{user_id}/reset-password")
def reset_password(
    tenant_slug: str,
    user_id: int,
    current_user: dict = Depends(require_role("admin")),
):
    """
    Admin сбрасывает пароль пользователя.
    Устанавливает временный пароль — пользователь должен сменить при входе.
    """

    tenant_db: Session = next(get_tenant_db(current_user["db_name"]))

    try:
        user = tenant_db.query(User).filter(
            User.id == user_id
        ).first()

        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")

        import secrets
        temp_password = secrets.token_urlsafe(8)

        user.password_hash = hash_password(temp_password)
        tenant_db.commit()

        return {
            "message": f"Пароль пользователя {user.full_name} сброшен",
            "temp_password": temp_password,
        }
    finally:
        tenant_db.close()