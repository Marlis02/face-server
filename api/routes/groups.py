from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from core.tenant_db import get_tenant_db
from core.dependencies import require_role
from models.tenant import Group, Department, User
from schemas.group import GroupCreate, GroupResponse

router = APIRouter()


@router.post("/{tenant_slug}/groups", response_model=GroupResponse)
def create_group(
    tenant_slug: str,
    data: GroupCreate,
    current_user: dict = Depends(require_role("admin")),
):
    """Создать группу. Только admin."""

    tenant_db: Session = next(get_tenant_db(current_user["db_name"]))

    try:
        # проверяем что department существует
        dept = tenant_db.query(Department).filter(
            Department.id == data.department_id
        ).first()
        if not dept:
            raise HTTPException(
                status_code=404,
                detail="Отдел не найден"
            )

        # проверяем что название не занято в этом отделе
        existing = tenant_db.query(Group).filter(
            Group.name == data.name,
            Group.department_id == data.department_id,
        ).first()
        if existing:
            raise HTTPException(
                status_code=400,
                detail="Группа с таким названием уже существует в этом отделе"
            )

        group = Group(
            name=data.name,
            department_id=data.department_id,
        )
        tenant_db.add(group)
        tenant_db.commit()
        tenant_db.refresh(group)

        return group
    finally:
        tenant_db.close()


@router.get("/{tenant_slug}/groups", response_model=list[GroupResponse])
def get_groups(
    tenant_slug: str,
    department_id: int = None,
    current_user: dict = Depends(require_role("admin", "manager")),
):
    """
    Список групп.
    Можно фильтровать по отделу: /groups?department_id=1
    """

    tenant_db: Session = next(get_tenant_db(current_user["db_name"]))

    try:
        query = tenant_db.query(Group)

        if department_id:
            query = query.filter(Group.department_id == department_id)

        return query.all()
    finally:
        tenant_db.close()


@router.get("/{tenant_slug}/groups/{group_id}", response_model=GroupResponse)
def get_group(
    tenant_slug: str,
    group_id: int,
    current_user: dict = Depends(require_role("admin", "manager")),
):
    """Получить группу по id."""

    tenant_db: Session = next(get_tenant_db(current_user["db_name"]))

    try:
        group = tenant_db.query(Group).filter(
            Group.id == group_id
        ).first()

        if not group:
            raise HTTPException(status_code=404, detail="Группа не найдена")

        return group
    finally:
        tenant_db.close()


@router.patch("/{tenant_slug}/groups/{group_id}", response_model=GroupResponse)
def update_group(
    tenant_slug: str,
    group_id: int,
    data: GroupCreate,
    current_user: dict = Depends(require_role("admin")),
):
    """Обновить группу. Только admin."""

    tenant_db: Session = next(get_tenant_db(current_user["db_name"]))

    try:
        group = tenant_db.query(Group).filter(
            Group.id == group_id
        ).first()

        if not group:
            raise HTTPException(status_code=404, detail="Группа не найдена")

        # проверяем что department существует
        dept = tenant_db.query(Department).filter(
            Department.id == data.department_id
        ).first()
        if not dept:
            raise HTTPException(status_code=404, detail="Отдел не найден")

        # проверяем что название не занято
        existing = tenant_db.query(Group).filter(
            Group.name == data.name,
            Group.department_id == data.department_id,
            Group.id != group_id,
        ).first()
        if existing:
            raise HTTPException(
                status_code=400,
                detail="Группа с таким названием уже существует в этом отделе"
            )

        group.name = data.name
        group.department_id = data.department_id
        tenant_db.commit()
        tenant_db.refresh(group)

        return group
    finally:
        tenant_db.close()


@router.delete("/{tenant_slug}/groups/{group_id}")
def delete_group(
    tenant_slug: str,
    group_id: int,
    current_user: dict = Depends(require_role("admin")),
):
    """Удалить группу. Только admin."""

    tenant_db: Session = next(get_tenant_db(current_user["db_name"]))

    try:
        group = tenant_db.query(Group).filter(
            Group.id == group_id
        ).first()

        if not group:
            raise HTTPException(status_code=404, detail="Группа не найдена")

        # проверяем что нет пользователей в этой группе
        users = tenant_db.query(User).filter(
            User.group_id == group_id
        ).first()
        if users:
            raise HTTPException(
                status_code=400,
                detail="Нельзя удалить группу — в ней есть пользователи"
            )

        tenant_db.delete(group)
        tenant_db.commit()

        return {"message": f"Группа '{group.name}' удалена"}
    finally:
        tenant_db.close()