import cv2
import numpy as np
from insightface.app import FaceAnalysis
from sqlalchemy.orm import Session
from models.tenant import FaceEmbedding
import pickle

# порог сходства — если выше то совпадение
SIMILARITY_THRESHOLD = 0.5

# максимум эмбеддингов на пользователя
MAX_EMBEDDINGS_PER_USER = 5


class FaceService:
    def __init__(self):
        self._app = None
        self._initialized = False

    def initialize(self):
        """
        Загружает модель InsightFace.
        Вызывается один раз при старте сервера.
        При первом запуске скачивает модель ~300 МБ.
        """
        if self._initialized:
            return

        print("⏳ Загрузка модели InsightFace...")

        self._app = FaceAnalysis(
            name="buffalo_l",       # модель ArcFace
            providers=["CPUExecutionProvider"],  # CPU
        )
        self._app.prepare(ctx_id=0, det_size=(640, 640))

        self._initialized = True
        print("✅ InsightFace загружен")

    def get_embedding(self, image: np.ndarray) -> np.ndarray | None:
        """
        Принимает numpy array (H, W, 3) BGR.
        Возвращает эмбеддинг (512,) или None если лицо не найдено.
        """
        if not self._initialized:
            raise RuntimeError("FaceService не инициализирован")

        faces = self._app.get(image)

        if not faces:
            return None

        # берём первое лицо (самое большое по площади)
        face = max(faces, key=lambda f: (
            f.bbox[2] - f.bbox[0]) * (f.bbox[3] - f.bbox[1])
        )

        return face.normed_embedding  # уже нормализованный вектор (512,)

    def embedding_to_bytes(self, embedding: np.ndarray) -> bytes:
        """Конвертирует эмбеддинг в байты для хранения в базе."""
        return pickle.dumps(embedding)

    def bytes_to_embedding(self, data: bytes) -> np.ndarray:
        """Конвертирует байты из базы обратно в эмбеддинг."""
        return pickle.loads(data)

    def cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        """
        Считает косинусное сходство между двумя эмбеддингами.
        Результат от 0 до 1. Чем выше — тем похожее.
        """
        # эмбеддинги уже нормализованы InsightFace
        # поэтому просто скалярное произведение
        return float(np.dot(a, b))

    def find_match(
        self,
        camera_embedding: np.ndarray,
        db: Session,
    ) -> dict | None:
        """
        Ищет совпадение среди всех эмбеддингов в базе.
        Возвращает {user_id, score} или None.
        """

        # загружаем все эмбеддинги из базы
        all_embeddings = db.query(FaceEmbedding).all()

        if not all_embeddings:
            return None

        best_user_id = None
        best_score = 0.0

        for emb_record in all_embeddings:
            # конвертируем байты → numpy array
            stored_embedding = self.bytes_to_embedding(emb_record.embedding)

            # считаем сходство
            score = self.cosine_similarity(camera_embedding, stored_embedding)

            if score > best_score:
                best_score = score
                best_user_id = emb_record.user_id

        # проверяем порог
        if best_score >= SIMILARITY_THRESHOLD:
            return {
                "user_id": best_user_id,
                "score": best_score,
            }

        return None

    def decode_jpeg(self, jpeg_bytes: bytes) -> np.ndarray | None:
        """
        Конвертирует JPEG байты в numpy array.
        Возвращает None если байты повреждены.
        """
        try:
            nparr = np.frombuffer(jpeg_bytes, np.uint8)
            image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            return image
        except Exception:
            return None

    def can_add_embedding(self, user_id: int, db: Session) -> bool:
        """Проверяет не превышен ли лимит эмбеддингов."""
        count = db.query(FaceEmbedding).filter(
            FaceEmbedding.user_id == user_id
        ).count()
        return count < MAX_EMBEDDINGS_PER_USER

    def get_embeddings_count(self, user_id: int, db: Session) -> int:
        """Возвращает количество эмбеддингов пользователя."""
        return db.query(FaceEmbedding).filter(
            FaceEmbedding.user_id == user_id
        ).count()


face_service = FaceService()