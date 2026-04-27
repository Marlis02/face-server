from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from core.tenant_db import get_tenant_db
from core.dependencies import require_role
from models.tenant import User, FaceEmbedding
from services.face_service import face_service
import cv2
import numpy as np

router = APIRouter()


@router.post("/{tenant_slug}/users/{user_id}/face")
def upload_face(
    tenant_slug: str,
    user_id: int,
    file: UploadFile = File(...),
    current_user: dict = Depends(require_role("admin")),
):
    """
    Загружает фото пользователя и создаёт эмбеддинг.
    Максимум 5 эмбеддингов на пользователя.
    Только admin.
    """

    tenant_db: Session = next(get_tenant_db(current_user["db_name"]))

    try:
        # проверяем что пользователь существует
        user = tenant_db.query(User).filter(
            User.id == user_id,
            User.is_active == True,
        ).first()

        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")

        # проверяем лимит эмбеддингов
        if not face_service.can_add_embedding(user_id, tenant_db):
            count = face_service.get_embeddings_count(user_id, tenant_db)
            raise HTTPException(
                status_code=400,
                detail=f"Достигнут лимит фото ({count}/5). Удалите старое фото."
            )

        # проверяем формат файла
        if file.content_type not in ["image/jpeg", "image/png", "image/webp"]:
            raise HTTPException(
                status_code=400,
                detail="Поддерживаются только JPEG, PNG, WebP"
            )

        # читаем байты фото
        image_bytes = file.file.read()

        # декодируем в numpy array
        nparr = np.frombuffer(image_bytes, np.uint8)
        image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if image is None:
            raise HTTPException(
                status_code=400,
                detail="Не удалось прочитать изображение"
            )

        # извлекаем эмбеддинг
        embedding = face_service.get_embedding(image)

        if embedding is None:
            raise HTTPException(
                status_code=400,
                detail="Лицо не найдено на фото. Загрузите чёткое фото лица."
            )

        # считаем качество — норма эмбеддинга
        quality_score = float(np.linalg.norm(embedding))

        # сохраняем в базу
        face_embedding = FaceEmbedding(
            user_id=user_id,
            embedding=face_service.embedding_to_bytes(embedding),
            quality_score=quality_score,
        )
        tenant_db.add(face_embedding)
        tenant_db.commit()
        tenant_db.refresh(face_embedding)

        count = face_service.get_embeddings_count(user_id, tenant_db)

        return {
            "message": "Фото успешно добавлено",
            "embedding_id": face_embedding.id,
            "quality_score": round(quality_score, 3),
            "total_embeddings": count,
            "remaining_slots": 5 - count,
        }
    finally:
        tenant_db.close()


@router.get("/{tenant_slug}/users/{user_id}/face")
def get_user_faces(
    tenant_slug: str,
    user_id: int,
    current_user: dict = Depends(require_role("admin", "manager")),
):
    """Список эмбеддингов пользователя."""

    tenant_db: Session = next(get_tenant_db(current_user["db_name"]))

    try:
        user = tenant_db.query(User).filter(
            User.id == user_id,
        ).first()

        if not user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")

        embeddings = tenant_db.query(FaceEmbedding).filter(
            FaceEmbedding.user_id == user_id,
        ).all()

        return {
            "user_id": user_id,
            "full_name": user.full_name,
            "total_embeddings": len(embeddings),
            "remaining_slots": 5 - len(embeddings),
            "embeddings": [
                {
                    "id": e.id,
                    "quality_score": e.quality_score,
                    "created_at": e.created_at,
                }
                for e in embeddings
            ],
        }
    finally:
        tenant_db.close()


@router.delete("/{tenant_slug}/users/{user_id}/face/{embedding_id}")
def delete_face(
    tenant_slug: str,
    user_id: int,
    embedding_id: int,
    current_user: dict = Depends(require_role("admin")),
):
    """Удалить эмбеддинг. Только admin."""

    tenant_db: Session = next(get_tenant_db(current_user["db_name"]))

    try:
        embedding = tenant_db.query(FaceEmbedding).filter(
            FaceEmbedding.id == embedding_id,
            FaceEmbedding.user_id == user_id,
        ).first()

        if not embedding:
            raise HTTPException(status_code=404, detail="Эмбеддинг не найден")

        tenant_db.delete(embedding)
        tenant_db.commit()

        count = face_service.get_embeddings_count(user_id, tenant_db)

        return {
            "message": "Фото удалено",
            "remaining_embeddings": count,
        }
    finally:
        tenant_db.close()