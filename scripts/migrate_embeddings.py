"""
Одноразовая миграция: pickle.dumps(np.ndarray) -> raw float32 bytes.

Зачем: pickle.loads на ненадёжных данных = удалённое выполнение кода.
Биометрическая БД — критичная, доверять её содержимому нельзя.
После миграции face_service.bytes_to_embedding читает только raw-формат
по фиксированной длине (2048 байт = 512 * float32).

Запуск (из корня проекта):
    python -m scripts.migrate_embeddings

По умолчанию обходит все tenant-базы из master_db.tenants.
Сухой прогон:  python -m scripts.migrate_embeddings --dry-run
"""
import argparse
import pickle
import sys

import numpy as np
from sqlalchemy.orm import sessionmaker

# чтобы скрипт работал при запуске python scripts/migrate_embeddings.py
sys.path.insert(0, ".")

from core.logger import get_logger  # noqa: E402
from core.master_db import get_master_db  # noqa: E402
from core.tenant_db import get_tenant_engine  # noqa: E402
from models.master import Tenant  # noqa: E402
from models.tenant import FaceEmbedding  # noqa: E402

logger = get_logger("migrate_embeddings")

EMBEDDING_DIM = 512
EMBEDDING_DTYPE = np.float32
EMBEDDING_BYTES = EMBEDDING_DIM * np.dtype(EMBEDDING_DTYPE).itemsize


def migrate_tenant(db_name: str, dry_run: bool) -> tuple[int, int, int]:
    """
    Возвращает (всего, мигрировано, пропущено-уже-в-raw).
    """
    engine = get_tenant_engine(db_name)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    db = Session()

    total = 0
    migrated = 0
    skipped = 0
    failed = 0

    try:
        rows = db.query(FaceEmbedding).all()
        total = len(rows)

        for row in rows:
            data = row.embedding
            if data is None:
                failed += 1
                logger.warning("[%s] id=%s: пустой embedding", db_name, row.id)
                continue

            if len(data) == EMBEDDING_BYTES:
                skipped += 1
                continue

            try:
                arr = pickle.loads(data)  # noqa: S301
            except Exception as e:
                failed += 1
                logger.error(
                    "[%s] id=%s: не pickle и не raw (%d байт): %s",
                    db_name, row.id, len(data), e,
                )
                continue

            arr = np.asarray(arr, dtype=EMBEDDING_DTYPE)
            if arr.shape != (EMBEDDING_DIM,):
                failed += 1
                logger.error(
                    "[%s] id=%s: неожиданная форма %s",
                    db_name, row.id, arr.shape,
                )
                continue

            row.embedding = np.ascontiguousarray(arr).tobytes()
            migrated += 1

        if dry_run:
            db.rollback()
            logger.info(
                "[%s] DRY-RUN: всего=%d мигрировать=%d уже-raw=%d ошибок=%d",
                db_name, total, migrated, skipped, failed,
            )
        else:
            db.commit()
            logger.info(
                "[%s] OK: всего=%d мигрировано=%d уже-raw=%d ошибок=%d",
                db_name, total, migrated, skipped, failed,
            )
    finally:
        db.close()

    return total, migrated, skipped


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run", action="store_true",
        help="только посчитать, не сохранять",
    )
    parser.add_argument(
        "--tenant", default=None,
        help="мигрировать только одну базу (по db_name)",
    )
    args = parser.parse_args()

    master = next(get_master_db())
    try:
        q = master.query(Tenant)
        if args.tenant:
            q = q.filter(Tenant.db_name == args.tenant)
        tenants = q.all()
    finally:
        master.close()

    if not tenants:
        logger.warning("Tenant'ы не найдены")
        return 1

    grand_total = grand_migrated = grand_skipped = 0
    for t in tenants:
        try:
            total, migrated, skipped = migrate_tenant(t.db_name, args.dry_run)
        except Exception as e:
            logger.error("[%s] миграция упала: %s", t.db_name, e)
            continue
        grand_total += total
        grand_migrated += migrated
        grand_skipped += skipped

    logger.info(
        "ИТОГО: tenants=%d записей=%d мигрировано=%d уже-raw=%d",
        len(tenants), grand_total, grand_migrated, grand_skipped,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
