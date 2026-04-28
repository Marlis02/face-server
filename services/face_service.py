import cv2
import numpy as np
from insightface.app import FaceAnalysis
from sqlalchemy.orm import Session
from models.tenant import FaceEmbedding
from concurrent.futures import ThreadPoolExecutor
import pickle

# порог сходства — если выше то совпадение
SIMILARITY_THRESHOLD = 0.5

# максимум эмбеддингов на пользователя
MAX_EMBEDDINGS_PER_USER = 5

# пул потоков для тяжёлых операций (InsightFace)
# не блокирует event loop при обработке нескольких планшетов
executor = ThreadPoolExecutor(max_workers=2)


class FaceService:
    def __init__(self):
        self._app = None
        self._initialized = False
        # кэш: db_name → (матрица эмбеддингов, список user_id)
        # не ходим в базу на каждый кадр
        self._cache: dict[str, tuple] = {}

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

    # ─── Кэш эмбеддингов ──────────────────────────────────────

    def load_cache(self, db_name: str, db: Session):
        """
        Загружает все эмбеддинги из базы в память.
        Строит numpy матрицу для быстрого батч-сравнения.
        Вызывается автоматически при первом запросе.
        """
        rows = db.query(FaceEmbedding).all()

        if not rows:
            self._cache[db_name] = (None, [])
            return

        vectors = []
        user_ids = []

        for row in rows:
            vectors.append(self.bytes_to_embedding(row.embedding))
            user_ids.append(row.user_id)

        # матрица (N, 512) — все эмбеддинги сразу
        matrix = np.array(vectors, dtype=np.float32)
        self._cache[db_name] = (matrix, user_ids)

        print(f"✅ Кэш загружен: {len(user_ids)} эмбеддингов для {db_name}")

    def invalidate_cache(self, db_name: str):
        """
        Сбрасывает кэш.
        Вызывать когда добавили или удалили фото пользователя.
        """
        if db_name in self._cache:
            del self._cache[db_name]
            print(f"🔄 Кэш сброшен для {db_name}")

    def find_match(
        self,
        camera_embedding: np.ndarray,
        db_name: str,
        db: Session,
    ) -> dict | None:
        """
        Ищет совпадение через кэш + numpy батч.
        Не делает запрос в базу если кэш уже загружен.
        Возвращает {user_id, score} или None.
        """

        # загружаем кэш если нет
        if db_name not in self._cache:
            self.load_cache(db_name, db)

        matrix, user_ids = self._cache[db_name]

        if matrix is None or len(user_ids) == 0:
            return None

        # батч сравнение — одна матричная операция вместо цикла
        # matrix @ camera_embedding = вектор scores (N,)
        scores = matrix @ camera_embedding

        best_idx = int(np.argmax(scores))
        best_score = float(scores[best_idx])

        # проверяем порог
        if best_score >= SIMILARITY_THRESHOLD:
            return {
                "user_id": user_ids[best_idx],
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