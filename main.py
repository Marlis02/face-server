from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from core.logger import get_logger
from core.rate_limit import limiter
from core.master_db import master_engine, MasterBase
from api.routes import admin, auth, users, departments, groups, devices, attendance, faces
from api.websocket import router as ws_router
from services.face_service import face_service
import models.master

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    MasterBase.metadata.create_all(bind=master_engine)
    logger.info("Таблицы master_db созданы")
    face_service.initialize()
    yield
    logger.info("Сервер остановлен")


app = FastAPI(
    title="Система контроля посещаемости",
    version="0.1.0",
    lifespan=lifespan,
    swagger_ui_parameters={"persistAuthorization": True},
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(admin.router, prefix="/admin", tags=["Администрирование"])
app.include_router(auth.router, prefix="/api", tags=["Авторизация"])
app.include_router(users.router, prefix="/api", tags=["Пользователи"])
app.include_router(departments.router, prefix="/api", tags=["Отделы"])
app.include_router(groups.router, prefix="/api", tags=["Группы"])
app.include_router(devices.router, prefix="/api", tags=["Устройства"])
app.include_router(attendance.router, prefix="/api", tags=["Посещаемость"])
app.include_router(faces.router, prefix="/api", tags=["Лица"])
app.include_router(ws_router, tags=["WebSocket"])


@app.get("/")
def root():
    return {"message": "Сервер работает"}


@app.get("/health")
def health():
    return {
        "status": "ok",
        "face_model": face_service._initialized,
    }