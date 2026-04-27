from sqlalchemy import (
    Column, Integer, String, Boolean,
    DateTime, Date, Text, Float,
    ForeignKey, UniqueConstraint, LargeBinary
)
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from core.tenant_db import TenantBase


class Department(TenantBase):
    __tablename__ = "departments"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # связи
    groups = relationship("Group", back_populates="department")
    users = relationship("User", back_populates="department")


class Group(TenantBase):
    __tablename__ = "groups"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    department_id = Column(Integer, ForeignKey("departments.id"), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # связи
    department = relationship("Department", back_populates="groups")
    users = relationship("User", back_populates="group")


class User(TenantBase):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)

    role = Column(String, nullable=False, default="user")
    # admin / manager / user

    department_id = Column(Integer, ForeignKey("departments.id"), nullable=True)
    group_id = Column(Integer, ForeignKey("groups.id"), nullable=True)

    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # связи
    department = relationship("Department", back_populates="users")
    group = relationship("Group", back_populates="users")
    attendances = relationship("Attendance", back_populates="user")
    face_embeddings = relationship("FaceEmbedding", back_populates="user")

    @property
    def full_name(self):
        return f"{self.last_name} {self.first_name}"


class FaceEmbedding(TenantBase):
    __tablename__ = "face_embeddings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    embedding = Column(LargeBinary, nullable=False)
    quality_score = Column(Float, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # связи
    user = relationship("User", back_populates="face_embeddings")


class Device(TenantBase):
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    login = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    token = Column(String, unique=True, nullable=True, index=True)
    location = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    last_seen_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    attendances = relationship("Attendance", back_populates="device")


class Attendance(TenantBase):
    __tablename__ = "attendances"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=True)

    date = Column(Date, nullable=False)
    marked_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    status = Column(String, nullable=False, default="auto")
    # auto   — автоматически через камеру
    # manual — вручную через админа/менеджера

    note = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "date", name="uq_user_date"),
    )

    user = relationship("User", back_populates="attendances")
    device = relationship("Device", back_populates="attendances")