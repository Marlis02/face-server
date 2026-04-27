from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from core.master_db import MasterBase


class Tenant(MasterBase):
    __tablename__ = "tenants"

    id = Column(Integer, primary_key=True, index=True)
    slug = Column(String, unique=True, nullable=False, index=True)
    # slug — короткое имя: "kgtu", "zavod", "office1"
    # используется в URL: /kgtu/login

    name = Column(String, nullable=False)
    # полное название: "КГТУ им. Раззакова"

    type = Column(String, nullable=False, default="university")
    # тип: university / enterprise / office

    db_name = Column(String, unique=True, nullable=False)
    # имя базы данных: "tenant_kgtu"

    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))