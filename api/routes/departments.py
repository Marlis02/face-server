from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from core.tenant_db import get_tenant_db
from core.dependencies import require_role
from models.tenant import Department, Group, User
from schemas.department import DepartmentCreate, DepartmentResponse

router = APIRouter()


@router.post("/{tenant_slug}/departments", response_model=DepartmentResponse)
def create_department(
    tenant_slug: str,
    data: DepartmentCreate,
    current_user: dict = Depends(require_role("admin")),
):
    """Создать отдел/факультет. Только admin."""

    tenant_db: Session = next(get_tenant_db(current_user["db_name"]))

    try:
        # проверяем что название не занято
        existing = tenant_db.query(Department).filter(
            Department.name == data.name
        ).first()
        if existing:
            raise HTTPException(
                status_code=400,
                detail="Отдел с таким названием уже существует"
            )

        department = Department(name=data.name)
        tenant_db.add(department)
        tenant_db.commit()
        tenant_db.refresh(department)

        return department
    finally:
        tenant_db.close()


@router.get("/{tenant_slug}/departments", response_model=list[DepartmentResponse])
def get_departments(
    tenant_slug: str,
    current_user: dict = Depends(require_role("admin", "manager")),
):
    """Список всех отделов."""

    tenant_db: Session = next(get_tenant_db(current_user["db_name"]))

    try:
        return tenant_db.query(Department).all()
    finally:
        tenant_db.close()


@router.get("/{tenant_slug}/departments/{department_id}", response_model=DepartmentResponse)
def get_department(
    tenant_slug: str,
    department_id: int,
    current_user: dict = Depends(require_role("admin", "manager")),
):
    """Получить отдел по id."""

    tenant_db: Session = next(get_tenant_db(current_user["db_name"]))

    try:
        department = tenant_db.query(Department).filter(
            Department.id == department_id
        ).first()

        if not department:
            raise HTTPException(status_code=404, detail="Отдел не найден")

        return department
    finally:
        tenant_db.close()


@router.patch("/{tenant_slug}/departments/{department_id}", response_model=DepartmentResponse)
def update_department(
    tenant_slug: str,
    department_id: int,
    data: DepartmentCreate,
    current_user: dict = Depends(require_role("admin")),
):
    """Обновить название отдела. Только admin."""

    tenant_db: Session = next(get_tenant_db(current_user["db_name"]))

    try:
        department = tenant_db.query(Department).filter(
            Department.id == department_id
        ).first()

        if not department:
            raise HTTPException(status_code=404, detail="Отдел не найден")

        # проверяем что новое название не занято
        existing = tenant_db.query(Department).filter(
            Department.name == data.name,
            Department.id != department_id,
        ).first()
        if existing:
            raise HTTPException(
                status_code=400,
                detail="Отдел с таким названием уже существует"
            )

        department.name = data.name
        tenant_db.commit()
        tenant_db.refresh(department)

        return department
    finally:
        tenant_db.close()


@router.delete("/{tenant_slug}/departments/{department_id}")
def delete_department(
    tenant_slug: str,
    department_id: int,
    current_user: dict = Depends(require_role("admin")),
):
    """Удалить отдел. Только admin."""

    tenant_db: Session = next(get_tenant_db(current_user["db_name"]))

    try:
        department = tenant_db.query(Department).filter(
            Department.id == department_id
        ).first()

        if not department:
            raise HTTPException(status_code=404, detail="Отдел не найден")

        # проверяем что нет групп в этом отделе
        groups = tenant_db.query(Group).filter(
            Group.department_id == department_id
        ).first()
        if groups:
            raise HTTPException(
                status_code=400,
                detail="Нельзя удалить отдел — в нём есть группы"
            )

        # проверяем что нет пользователей в этом отделе
        users = tenant_db.query(User).filter(
            User.department_id == department_id
        ).first()
        if users:
            raise HTTPException(
                status_code=400,
                detail="Нельзя удалить отдел — в нём есть пользователи"
            )

        tenant_db.delete(department)
        tenant_db.commit()

        return {"message": f"Отдел '{department.name}' удалён"}
    finally:
        tenant_db.close()