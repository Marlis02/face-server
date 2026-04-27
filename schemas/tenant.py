from pydantic import BaseModel
from datetime import datetime


class TenantCreate(BaseModel):
    slug: str        # kgtu, zavod, office1
    name: str        # КГТУ им. Раззакова
    type: str        # university / enterprise / office


class TenantResponse(BaseModel):
    id: int
    slug: str
    name: str
    type: str
    db_name: str
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True