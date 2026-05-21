# Attendance Backend

Серверная часть системы автоматизированного контроля посещаемости через
распознавание лиц. Дипломный проект КГТУ им. И. Раззакова.

Микросервис на FastAPI + PostgreSQL + InsightFace (ArcFace). Поддерживает
несколько организаций (multi-tenant — у каждой своя БД), авторизацию
пользователей и устройств (планшетов), потоковую отметку через WebSocket
и экспорт журнала в Excel.

## Архитектура

```
┌──────────────────┐     HTTPS         ┌──────────────────┐
│  Веб-админка     │ ────────────────► │                  │
│  (React + Vite)  │     REST API      │                  │
└──────────────────┘                   │                  │
                                       │  FastAPI         │     ┌──────────────┐
┌──────────────────┐     WSS           │  (this repo)     │ ──► │ PostgreSQL   │
│  Планшет         │ ────────────────► │                  │     │  master_db   │
│  (Flutter)       │  поток JPEG       │  + InsightFace   │     │  tenant_*    │
└──────────────────┘                   │  (ArcFace 512-d) │     └──────────────┘
                                       └──────────────────┘
```

- **master_db** хранит организации (`tenants`) — где какая БД, активна ли.
- **tenant_<slug>** на каждую организацию — пользователи, устройства,
  посещения, биометрические эмбеддинги.
- Подключение к tenant-БД выбирается **по slug в URL**:
  `POST /api/{slug}/login`.

## Технологический стек

| Слой | Что |
|---|---|
| Framework | FastAPI, Uvicorn, Pydantic v2 |
| База | PostgreSQL 14+, SQLAlchemy 2.0 |
| Auth | JWT (HS256) — access + refresh, bcrypt для паролей |
| Биометрия | InsightFace (buffalo_l / ArcFace, 512-мерный эмбеддинг) |
| Транспорт | REST + WebSocket (поток JPEG-кадров) |
| Экспорт | XlsxWriter (`.xlsx` с форматированием) |
| Безопасность | slowapi (rate limit), logging audit |
| Тесты | pytest + httpx + TestClient |

## Структура проекта

```
attendance-backend/
├── main.py                  # точка входа FastAPI
├── core/
│   ├── master_db.py         # подключение к master_db
│   ├── tenant_db.py         # роутинг к tenant-БД
│   ├── security.py          # JWT, bcrypt, hash/verify
│   ├── dependencies.py      # auth-зависимости FastAPI
│   ├── rate_limit.py        # slowapi limiter
│   └── logger.py            # централизованное логирование
├── models/
│   ├── master.py            # Tenant
│   └── tenant.py            # User / Department / Group / Device / Attendance / FaceEmbedding
├── schemas/                 # Pydantic-схемы запросов/ответов
├── api/
│   ├── routes/              # REST endpoint'ы
│   │   ├── admin.py         # создание организаций (защищено ADMIN_SECRET)
│   │   ├── auth.py          # /login, /refresh, /me
│   │   ├── users.py         # CRUD + смена пароля
│   │   ├── departments.py / groups.py
│   │   ├── devices.py       # CRUD устройств, /devices/login для планшета
│   │   ├── attendance.py    # отметка вручную, журнал, статистика, /export → xlsx
│   │   └── faces.py         # загрузка фотографий → эмбеддинги
│   └── websocket.py         # /api/{slug}/ws — поток кадров от планшета
├── services/
│   └── face_service.py      # InsightFace, кэш эмбеддингов, поиск совпадений
├── scripts/
│   └── migrate_embeddings.py  # одноразовая миграция pickle → raw float32
├── tests/
│   ├── conftest.py          # тестовая БД + фейковый FaceService
│   ├── test_diploma.py      # T-1…T-13 — протокол ВКР
│   └── .env.test
├── logs/                    # ротируемые логи (создаётся автоматически)
├── requirements.txt         # pinned версии
├── requirements.lock.txt    # полный pip freeze
└── pytest.ini
```

## Быстрый старт

### 1. Зависимости

PostgreSQL 14+ запущен и доступен. Python 3.10+.

```bash
git clone <repo-url>
cd attendance-backend

python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Первый запуск скачает модель InsightFace `buffalo_l` (~300 МБ) в
`~/.insightface/models/`.

### 2. Переменные окружения

Создайте `.env` в корне проекта:

```bash
# Подключение к мастер-базе. Tenant-базы создаются на том же сервере.
MASTER_DB_URL=postgresql://postgres:PASSWORD@localhost:5432/master_db

# JWT
SECRET_KEY=<openssl rand -hex 32>
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=30

# Защита /admin/* эндпоинтов (создание организаций).
# Дефолта НЕТ — сервер не стартует без этой переменной.
ADMIN_SECRET=<python -c "import secrets; print(secrets.token_urlsafe(32))">

# Опционально (по умолчанию INFO)
LOG_LEVEL=INFO
```

Сгенерировать секреты одной командой:

```bash
python -c "import secrets; print('SECRET_KEY=' + secrets.token_hex(32)); print('ADMIN_SECRET=' + secrets.token_urlsafe(32))"
```

### 3. Подготовить базы

Достаточно создать master_db — tenant-базы создаются автоматически через
`POST /admin/tenants`:

```bash
createdb -U postgres master_db
```

Таблицы в master_db создаются автоматически при старте.

### 4. Запустить сервер

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

- Swagger UI: <http://localhost:8000/docs>
- Health: <http://localhost:8000/health>

### 5. Создать первую организацию

```bash
curl -X POST http://localhost:8000/admin/tenants \
  -H "X-Admin-Secret: $ADMIN_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"slug": "kstu", "name": "КГТУ", "type": "university"}'
```

Сервер автоматически:
1. Запишет организацию в `master_db.tenants`.
2. Создаст БД `tenant_kstu`.
3. Создаст все таблицы.
4. Заведёт первого admin'а с паролем `admin123`. **Смените его сразу:**

```bash
# логин под дефолтным admin'ом
curl -X POST http://localhost:8000/api/kstu/login \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@attendance-kstu.com", "password": "admin123"}'

# затем POST /api/kstu/users/change-password с access_token
```

## Безопасность

Что реализовано (см. §3 ВКР):

- **bcrypt** для всех паролей (пользователи + устройства).
- **JWT** с раздельными access (30 мин) и refresh (30 дней) токенами,
  тип токена в payload.
- **RBAC**: роли `admin / manager / user`, защита через
  `Depends(require_role("admin"))`. См. `core/dependencies.py`.
- **ADMIN_SECRET** — отдельный заголовок для админских ручек,
  сравнение через `secrets.compare_digest` (постоянное время).
  **Без дефолта** — сервер не стартует, если переменная не задана.
- **Rate limit** — slowapi на `/login` пользователя и `/devices/login`
  планшета: 10 запросов/мин с IP, защита от brute-force.
- **Audit-лог** — все попытки входа (успех/отказ с указанием причины и IP),
  смена паролей, сброс паролей, создание организаций — пишутся в
  `logs/server.log` с ротацией (10 МБ × 5 файлов).
- **Биометрия**:
  - Сохраняется **только эмбеддинг** (вектор float32×512), не фото.
  - Сериализация — `np.tobytes`, **не pickle** (pickle.loads на ненадёжных
    данных = RCE).
  - Старые pickle-эмбеддинги мигрируются через
    `python -m scripts.migrate_embeddings`.
- **Изоляция** — каждая организация в своей БД, межтенант-запросов нет.

## Поток работы планшета (WebSocket)

```
1. POST /api/{slug}/devices/login   { login, password }    → device_token
2. GET  /api/{slug}/devices/init    (X-Device-Token)       → имя устройства, организация
3. WS   /api/{slug}/ws
       └─ send_text  JSON {"token": "<device_token>"}      → {"type":"connected", ...}
       └─ send_bytes [4 байта BE tracking_id][JPEG-кадр]   → {"type":"result", "status":"marked|already_marked|unknown|no_face", ...}
       └─ повторять
```

Подробно см. `api/websocket.py`.

## Экспорт в Excel

```
GET /api/{slug}/attendance/export
    ?user_id=...&date_from=YYYY-MM-DD&date_to=...&group_id=...&department_id=...
Authorization: Bearer <admin или manager token>
```

Возвращает `.xlsx` с шапкой, фильтром по колонкам, статусами «авто/вручную»
разного цвета, сводной строкой. Имя файла —
`attendance_{slug}_{YYYYMMDD_HHMMSS}.xlsx`, отдаётся в `Content-Disposition`.

## Тесты

13 кейсов по протоколу ВКР, использует отдельную тестовую БД и фейковый
FaceService (модель InsightFace в тестах не грузится — проверяется
бизнес-логика).

### Подготовка (один раз)

```bash
createdb -U postgres master_db_test
```

Тестовая конфигурация — в `tests/.env.test` (заливается до импортов
проекта в `conftest.py`).

### Запуск

```bash
pytest                  # 13 тестов, ~4 секунды
pytest -v --tb=short    # подробный вывод (для приложения к ВКР)
```

### Карта тестов

| ID | Сценарий | Ожидание |
|---|---|---|
| T-1 | Создание учреждения | Запись в master_db + БД + таблицы + admin |
| T-2 | Логин с верными данными | Пара access/refresh, role=admin |
| T-3 | Логин с неверным паролем | HTTP 401 |
| T-4 | Фото без лица | HTTP 400, «Лицо не найдено» |
| T-5 | 6-я фотография | HTTP 400, «Лимит 5/5» |
| T-6 | Планшет с неверным токеном | Закрытие WS, код 4001 |
| T-7 | Распознавание зарегистрированного | `auto`, confidence ≥ 0.5 |
| T-8 | Распознавание незарегистрированного | `unknown` |
| T-9 | Повторное распознавание | `already_marked`, без дубликата |
| T-10 | Два планшета параллельно | Обе сессии без блокировок |
| T-11 | GET /users менеджером | HTTP 200 |
| T-12 | DELETE /users менеджером | HTTP 403 |
| T-13 | Экспорт журнала в Excel | xlsx с заголовком, валидный архив |

## Логирование

- Центральная настройка — `core/logger.py`.
- Все модули: `from core.logger import get_logger; logger = get_logger(__name__)`.
- Аргументы передавать через `%s`, **не** через f-строки
  (иначе форматирование происходит даже при выключенном уровне):
  ```python
  logger.info("user_id=%s score=%.3f", user_id, score)
  ```
- Уровень регулируется `LOG_LEVEL` (`DEBUG / INFO / WARNING / ERROR`).
- Файл: `logs/server.log`, ротация 10 МБ × 5.

## Полезные команды

```bash
# Сгенерировать секреты
python -c "import secrets; print(secrets.token_urlsafe(32))"

# Pinned зависимости (после установки нового пакета)
pip freeze > requirements.lock.txt

# Миграция старых pickle-эмбеддингов на raw float32
python -m scripts.migrate_embeddings --dry-run
python -m scripts.migrate_embeddings

# Запуск только одного теста
pytest tests/test_diploma.py::test_t07_recognize_registered -v
```

## Troubleshooting

**`RuntimeError: ADMIN_SECRET не задан`** — добавьте `ADMIN_SECRET=...` в `.env`.

**`Application startup failed` про InsightFace** — модель скачивается при
первом запуске, нужен интернет. Файлы кешируются в `~/.insightface/models/buffalo_l/`.

**Планшет не достучался, но в логе сервера пусто** — проверьте что
адрес в форме планшета содержит порт (`192.168.0.108:8000`, не просто
`192.168.0.108`), и что uvicorn запущен с `--host 0.0.0.0`.

**Тесты падают на `psycopg2 OperationalError`** — нет `master_db_test`.
Создайте: `createdb -U postgres master_db_test`.

**На фронте кнопка экспорта качает файл с дефолтным именем** — в
`CORSMiddleware` нужен `expose_headers=["Content-Disposition"]`, чтобы
браузер показал JS-у имя из заголовка ответа.
