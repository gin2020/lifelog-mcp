# LifeLog MCP

**LifeLog MCP** — backend персонального LifeLog, подключающий долговременную память, структурированный финансовый учёт и уведомления к AI через **Model Context Protocol (MCP)**.

Проект предоставляет MCP-сервер на Python/FastMCP, хранит данные в PostgreSQL и использует Telegram как канал идентификации и доставки уведомлений. Архитектура построена так, чтобы AI отвечал за понимание естественного языка и выбор подходящего инструмента, а сервер — за валидацию, изоляцию данных, транзакционность и детерминированные операции с PostgreSQL.

> Ветка \`main\` отражает текущее состояние проекта. Папки \`app/domains/*\` присутствуют как архитектурные заделы, но в текущей версии полноценная прикладная реализация сосредоточена прежде всего в памяти, финансах, идентификации и уведомлениях.

---

## Что такое LifeLog

LifeLog — персональная система накопления жизненных данных, в которой ChatGPT может выступать естественным интерфейсом к собственной базе пользователя.

Примеры сценариев:

- «Запомни, что я решил…»
- «Запиши расходы: бензин 2000 ₽».
- «Сколько я потратил на табак за месяц?»
- «Покажи расходы по категориям».
- «Сколько было расходов в конкретном магазине?»

Главный принцип разделения ответственности:

**AI понимает запрос → MCP-инструмент структурирует операцию → сервис выполняет бизнес-логику → PostgreSQL хранит данные → Outbox/Dispatcher доставляет уведомление.**

---

## Архитектура

\`\`\`text
                         ┌──────────────────────┐
                         │       ChatGPT        │
                         │ natural language +   │
                         │ tool selection       │
                         └──────────┬───────────┘
                                    │ MCP
                                    ▼
                         ┌──────────────────────┐
                         │    LifeLog MCP       │
                         │       FastMCP         │
                         └──────────┬───────────┘
                                    │
                ┌───────────────────┼───────────────────┐
                │                   │                   │
                ▼                   ▼                   ▼
        ┌──────────────┐    ┌──────────────┐    ┌───────────────┐
        │    Memory    │    │   Finance    │    │    System     │
        │   service    │    │   services   │    │     tools     │
        └──────┬───────┘    └──────┬───────┘    └───────────────┘
               │                   │
               └──────────┬────────┘
                          ▼
                   ┌─────────────┐
                   │ PostgreSQL  │
                   │             │
                   │ users       │
                   │ memories    │
                   │ finance_*   │
                   │ oauth_*     │
                   │ outbox      │
                   └──────┬──────┘
                          │
                          ▼
                 ┌──────────────────┐
                 │ Notification     │
                 │ Outbox           │
                 └────────┬─────────┘
                          ▼
                 ┌──────────────────┐
                 │ Notification     │
                 │ Dispatcher       │
                 └────────┬─────────┘
                          ▼
                    Telegram Bot API
\`\`\`

---

# Основные возможности

## 1. Долговременная память

LifeLog хранит записи памяти в таблице \`memories\`.

Каждая запись содержит владельца, UUID, текст, тип памяти и временные метки.

### MCP-инструменты

**\`remember\`**

Высокоуровневый инструмент для сохранения обычных воспоминаний:

- факты;
- события;
- мысли;
- идеи;
- наблюдения.

Финансовые операции через \`remember\` не сохраняются: для них используется отдельный финансовый инструмент.

**\`create_memory\`**

Низкоуровневый инструмент создания записи памяти.

**\`update_memory\` / \`delete_memory\`**

Изменение и удаление memory по идентификатору владельца запроса.

Оба инструмента проходят через \`MemoryService\`, который отвечает за транзакцию, ownership и создание события уведомления.

---

## 2. Финансовый учёт

Финансовая подсистема разделяет операцию и её позиции:

\`\`\`text
FinanceEvent
    │
    └── FinanceItem[]
\`\`\`

### FinanceEvent

Содержит:

- тип операции: \`expense\` или \`income\`;
- место операции;
- валюту;
- общую сумму;
- владельца;
- UUID;
- временные метки.

### FinanceItem

Каждая позиция содержит название, категорию, количество, единицу измерения и стоимость.

### Категории

Текущий enum категорий:

\`\`\`text
dairy
meat
vegetables
fruits
grocery
drinks
alcohol
tobacco
household
medicine
transport
online_payments
home
salary
part_time
gift
other
\`\`\`

Натуральное описание запроса преобразуется AI в строго типизированную финансовую структуру. Сервер не вызывает LLM внутри финансового запроса.

### MCP-инструмент

**\`create_finance_event\`**

Используется для покупок, расходов, оплаты услуг, доходов, зарплаты, подработки, продаж и других финансовых операций.

Инструмент принимает структурированную Pydantic-модель \`FinanceEventCreate\`.

**\`update_finance_event\`**

Изменяет метаданные финансового события: тип операции, место или валюту.

**\`update_finance_item\` / \`delete_finance_item\`**

Изменяют или удаляют отдельную позицию \`FinanceItem\` внутри события. После
каждой операции \`total_amount\` события пересчитывается по оставшимся позициям
в одной транзакции. Удаление последней позиции запрещено — для этого нужно
удалить всё событие через \`delete_finance_event\`.

**\`delete_finance_event\`**

Удаляет финансовое событие вместе со всеми его позициями.

---

# 3. Финансовая аналитика

**\`query_finance\`** — детерминированный MCP-инструмент чтения финансовых данных из PostgreSQL.

Поддерживаемые режимы:

\`\`\`text
total_amount
transactions
group_by_category
group_by_place
statistics
summary
\`\`\`

### Фильтры

- категория;
- тип операции;
- название товара;
- место;
- дата от;
- дата до.

В режиме \`transactions\` каждая позиция также возвращает \`event_id\` и \`item_id\`,
чтобы ChatGPT мог адресно исправить или удалить нужную запись.

### Сортировка

\`\`\`text
date
amount
title
place
category
count
\`\`\`

Направления:

\`\`\`text
asc
desc
\`\`\`

Лимит результата: от 1 до 100.

\`query_finance\` не интерпретирует естественный язык. Он получает нормализованные аргументы и выполняет детерминированные SQLAlchemy-запросы через \`FinanceQueryService\` и \`FinanceRepository\`.

\`\`\`text
Natural language
       ↓
ChatGPT
       ↓
structured MCP arguments
       ↓
FinanceQueryService
       ↓
FinanceRepository
       ↓
PostgreSQL
\`\`\`

---

# 4. Multi-user ownership

LifeLog содержит модели \`users\` и \`user_identities\`.

Каждая память и каждая финансовая операция принадлежат конкретному пользователю через \`user_id\`.

Изоляция реализована на серверной стороне:

- другой пользователь не может получить чужую memory;
- финансовые запросы ограничиваются владельцем;
- агрегаты и статистика строятся только по данным текущего пользователя.

Это дополнительно покрывается тестами в \`tests/test_user_scoping.py\`.

---

# 5. OAuth и вход через Telegram

Для многопользовательского режима реализован OAuth authorization server adapter поверх FastMCP.

Поддерживаются:

- динамическая регистрация OAuth-клиентов;
- authorization requests;
- одноразовые authorization codes;
- refresh tokens;
- JWT access tokens;
- token revocation;
- хранение состояния OAuth в PostgreSQL.

### Telegram OIDC

Telegram используется как identity provider.

Поток:

\`\`\`text
MCP Client
   ↓
LifeLog OAuth
   ↓
Telegram OIDC
   ↓
ID Token validation
   ↓
Telegram user ID
   ↓
User / UserIdentity
   ↓
Authorization Code
   ↓
Access + Refresh Token
\`\`\`

Предусмотрены:

- PKCE;
- state;
- nonce;
- проверка подписи ID token;
- issuer;
- audience;
- lifetime;
- nonce;
- token version для отзыва JWT.

Client secret, полученный через Dynamic Client Registration, шифруется перед сохранением в PostgreSQL.

---

# 6. Telegram Notifications

Telegram как канал уведомлений отделён от Telegram OIDC.

**Telegram OIDC** — идентификация.

**Telegram Bot API** — доставка сообщений.

Подписка пользователя хранится в:

\`\`\`text
telegram_notification_subscriptions
\`\`\`

Система построена на **Transactional Outbox Pattern**.

При создании memory или finance event в той же транзакции создаётся запись в:

\`\`\`text
notification_outbox
\`\`\`

Outbox хранит агрегат, тип события, владельца, безопасный payload, статус, количество попыток, время следующей попытки и информацию об ошибке.

---

# 7. Notification Dispatcher

Dispatcher работает отдельным процессом.

Он:

1. выбирает ожидающее событие;
2. атомарно захватывает его через PostgreSQL;
3. переводит в \`processing\`;
4. отправляет сообщение через Telegram Bot API;
5. при успехе устанавливает \`sent\`;
6. при временной ошибке возвращает событие в \`pending\`;
7. применяет exponential backoff;
8. учитывает \`retry_after\` Telegram;
9. отключает подписку после блокировки бота;
10. сохраняет результат обработки.

Используется processing lease, позволяющий повторно обработать зависшие события.

---

# 8. Privacy-safe notifications

Уведомления не передают в Telegram содержимое пользовательской записи.

Dispatcher отправляет нейтральные подтверждения, например:

\`\`\`text
Lifelog: сохранена запись memory #123.
\`\`\`

или:

\`\`\`text
Lifelog: сохранена запись finance_event #456.
\`\`\`

Telegram не используется как вторичная база пользовательского контента.

---

# 9. MCP transport

LifeLog запускается через **streamable HTTP transport**.

Основная точка входа:

\`\`\`text
app/main.py
\`\`\`

FastMCP работает в stateless HTTP-режиме.

Текущая конфигурация сервера:

\`\`\`text
host: 127.0.0.1
port: 8001
\`\`\`

Для внешнего доступа используется HTTPS reverse proxy. Transport security ограничивает допустимый host.

---

# Структура проекта

\`\`\`text
lifelog-mcp/
├── app/
│   ├── config/
│   │   ├── logging.py
│   │   └── settings.py
│   ├── core/
│   │   ├── auth_routes.py
│   │   ├── dependencies.py
│   │   ├── exceptions.py
│   │   ├── mcp.py
│   │   ├── oauth_provider.py
│   │   └── telegram_webhook_routes.py
│   ├── db/
│   │   ├── database.py
│   │   ├── migrations/
│   │   ├── models/
│   │   └── repositories/
│   ├── domains/
│   │   ├── contacts/
│   │   ├── events/
│   │   ├── files/
│   │   ├── finance/
│   │   ├── health/
│   │   ├── nutrition/
│   │   └── reminders/
│   ├── schemas/
│   ├── services/
│   ├── tools/
│   └── main.py
├── scripts/
│   └── notification_dispatcher.py
├── tests/
├── alembic.ini
├── requirements.txt
├── .env.example
├── README.md
└── server.py
\`\`\`

---

# Слои приложения

### \`app/tools\`

MCP interface layer — инструменты, доступные модели.

### \`app/services\`

Business logic — транзакции и правила предметной области.

### \`app/db/repositories\`

Детерминированные запросы к PostgreSQL через SQLAlchemy.

### \`app/db/models\`

ORM-модели PostgreSQL.

### \`app/schemas\`

Pydantic-схемы входных и выходных данных.

### \`app/core\`

MCP, authentication, OAuth и HTTP routes.

### \`app/domains\`

Каркас будущих доменов LifeLog.

---

# Миграции

Для схемы PostgreSQL используется **Alembic**.

История схемы включает:

1. первоначальную структуру;
2. финансовые таблицы;
3. расширение финансовых категорий;
4. multi-user ownership;
5. OAuth storage;
6. Notification Outbox.

Применение:

\`\`\`bash
alembic upgrade head
\`\`\`

---

# Требования

- Python 3.12+;
- PostgreSQL;
- PM2;
- Nginx или другой reverse proxy;
- HTTPS;
- Telegram Bot API для уведомлений;
- Telegram OIDC credentials для OAuth.

Основные зависимости:

- MCP / FastMCP;
- SQLAlchemy 2.x;
- Alembic;
- psycopg 3;
- Pydantic Settings;
- PyJWT;
- httpx;
- cryptography.

---

# Конфигурация

Конфигурация загружается из \`.env\`.

Основные переменные:

\`\`\`text
DATABASE_URL
DEFAULT_USER_TELEGRAM_ID

AUTH_ENABLED

OAUTH_ISSUER_URL
MCP_RESOURCE_URL

TELEGRAM_CLIENT_ID
TELEGRAM_CLIENT_SECRET
TELEGRAM_REDIRECT_URI

JWT_SIGNING_KEY

TELEGRAM_BOT_TOKEN
TELEGRAM_BOT_USERNAME
TELEGRAM_WEBHOOK_SECRET
TELEGRAM_WEBHOOK_URL
\`\`\`

Секреты не должны попадать в Git.

Используйте \`.env.example\` как шаблон.

---

# Запуск

## Виртуальное окружение

\`\`\`bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
\`\`\`

## Миграции

\`\`\`bash
alembic upgrade head
\`\`\`

---

# Запуск MCP через PM2

\`\`\`bash
pm2 start ".venv/bin/python -m app.main" --name lifelog-mcp
\`\`\`

Проверка:

\`\`\`bash
pm2 status
pm2 logs lifelog-mcp
\`\`\`

Перезапуск после изменения окружения:

\`\`\`bash
pm2 restart lifelog-mcp --update-env
\`\`\`

---

# Запуск Notification Dispatcher

\`\`\`bash
pm2 start .venv/bin/python \\
  --name lifelog-notification-dispatcher \\
  --cwd /root/lifelog-mcp \\
  -- -m scripts.notification_dispatcher
\`\`\`

Проверка:

\`\`\`bash
pm2 logs lifelog-notification-dispatcher
\`\`\`

---

# Telegram Webhook

Загрузка переменных:

\`\`\`bash
set -a
source .env
set +a
\`\`\`

Регистрация:

\`\`\`bash
curl --fail-with-body \\
  --data-urlencode "url=\$TELEGRAM_WEBHOOK_URL" \\
  --data-urlencode "secret_token=\$TELEGRAM_WEBHOOK_SECRET" \\
  --data-urlencode 'allowed_updates=["message"]' \\
  "https://api.telegram.org/bot\${TELEGRAM_BOT_TOKEN}/setWebhook"
\`\`\`

Проверка:

\`\`\`bash
curl \\
  "https://api.telegram.org/bot\${TELEGRAM_BOT_TOKEN}/getWebhookInfo"
\`\`\`

Если в ответе \`url\` пустой, webhook не зарегистрирован.

---

# Проверка системы

Минимальный smoke-test:

1. проверить PM2;
2. проверить PostgreSQL;
3. проверить Telegram webhook;
4. вызвать MCP \`ping\`;
5. создать memory;
6. создать finance event;
7. проверить \`notification_outbox\`;
8. проверить dispatcher;
9. убедиться в доставке Telegram-уведомления.

---

# Тестирование

В репозитории есть тесты для:

- подключения к БД;
- memory service;
- обработки ошибок memory;
- уведомлений;
- OAuth provider;
- финансовых запросов;
- \`remember\`;
- ошибок \`remember\`;
- изоляции данных между пользователями.

Запуск:

\`\`\`bash
python -m unittest discover -s tests -p 'test_*.py'
\`\`\`

---

# Текущий статус реализации

### Реализовано

- MCP server на FastMCP;
- streamable HTTP transport;
- PostgreSQL + SQLAlchemy;
- Alembic migrations;
- долговременная память;
- структурированный финансовый учёт;
- финансовые агрегаты и статистика;
- multi-user ownership;
- Telegram OIDC authentication;
- OAuth authorization server layer;
- JWT access tokens;
- refresh token rotation;
- token revocation;
- Telegram Bot API;
- Telegram webhook;
- Transactional Outbox;
- отдельный notification dispatcher;
- retry/backoff;
- privacy-safe notification payloads;
- тесты ключевых подсистем.

### Архитектурные заделы

В репозитории подготовлены доменные пакеты:

- contacts;
- events;
- files;
- finance;
- health;
- nutrition;
- reminders.

Они пока являются архитектурным каркасом, а не полностью реализованными пользовательскими модулями.

Отдельно выделен \`embedding_service.py\` под будущую семантическую работу с памятью.

---

# Архитектурные принципы

**1. MCP — интерфейс, а не база данных.**

MCP-инструменты не должны содержать всю бизнес-логику.

**2. AI не является источником истины.**

AI преобразует естественный язык в структурированную команду, а сервер выполняет операцию.

**3. PostgreSQL — источник истины.**

Фактические записи находятся в базе.

**4. User ownership проверяется на сервере.**

Изоляция пользователей реализована на уровне сервисов и запросов к БД.

**5. Уведомления не должны ломать основную транзакцию.**

Для этого используется Transactional Outbox.

**6. Каналы доставки отделены от бизнес-логики.**

Telegram — текущий канал, а Outbox допускает добавление новых каналов.

**7. Домены расширяются постепенно.**

Новые предметные области добавляются поверх существующего ядра без переписывания базовой инфраструктуры.

---

# 10. Database Backup

LifeLog includes a standalone PostgreSQL backup service.

The backup service is intentionally separate from the MCP server and Telegram Notification Dispatcher. It does not use `notification_outbox`.

### Backup flow

```text
PostgreSQL
    ↓
pg_dump -Fc
    ↓
AES-256-GCM encryption
    ↓
Telegram Bot API (sendDocument)
    ↓
Encrypted backup in Telegram
```

The backup is created once per day at the configured time.

### Configuration

Add the following variables to `.env`:

```text
BACKUP_ENABLED=true
BACKUP_TIME=09:00
BACKUP_TIMEZONE=Europe/Berlin
BACKUP_ENCRYPTION_KEY=<random-secret>
```

The encryption key is a secret and must never be committed to Git or written to logs.

The backup service uses the existing:

- `DATABASE_URL` for PostgreSQL connection information;
- `TELEGRAM_BOT_TOKEN` for Telegram Bot API access;
- `DEFAULT_USER_TELEGRAM_ID` as the Telegram recipient.

### Backup commands

Run a backup immediately:

```bash
.venv/bin/python -m scripts.backup --run-once
```

Run the backup/recovery validation:

```bash
.venv/bin/python -m scripts.backup --test
```

Decrypt a downloaded encrypted backup:

```bash
.venv/bin/python -m scripts.backup --decrypt <encrypted-file>
```

The decrypted dump should only exist temporarily and must be treated as sensitive data.

### PM2

Run the backup service as a separate PM2 process:

```bash
pm2 start .venv/bin/python --name lifelog-backup \
  --cwd /root/lifelog-mcp -- -m scripts.backup

pm2 save
```

Check the process:

```bash
pm2 status
pm2 logs lifelog-backup
```

The service keeps its own lock/state information to avoid duplicate execution after restarts.

### Security

- PostgreSQL backups use the custom `pg_dump -Fc` format.
- The backup is encrypted with AES-256-GCM before it is uploaded.
- Unencrypted temporary files are removed after successful encryption/upload and during failure cleanup.
- Encryption keys, database credentials and Telegram credentials are never included in the backup message.
- Telegram receives only the encrypted backup file.
- The backup job does not depend on the notification outbox.

### Recovery validation

The `--test` command validates the backup/recovery path without modifying the production database. It creates a temporary backup, decrypts it, verifies that the PostgreSQL dump is readable, and cleans up temporary files.

For a production restore, first decrypt the downloaded Telegram file and then restore the resulting PostgreSQL dump with the appropriate PostgreSQL restore tooling.

# License

Отдельная лицензия проекта в репозитории пока не заявлена.
