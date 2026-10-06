# LifeLog MCP — Architecture

## 1. Назначение

LifeLog MCP — серверный слой персональной системы LifeLog, который предоставляет AI доступ к постоянным пользовательским данным через Model Context Protocol.

Архитектура строится вокруг разделения ответственности:

```text
Natural language
      ↓
AI / MCP client
      ↓
MCP tools
      ↓
Application services
      ↓
Repositories / ORM
      ↓
PostgreSQL
      ↓
Durable events
      ↓
Notification Dispatcher
      ↓
External delivery channel
```

AI отвечает за интерпретацию естественного языка и выбор инструмента. LifeLog MCP отвечает за корректность операции, ownership, транзакции, безопасность и сохранение состояния.

---

## 2. Архитектурная модель

Система разделена на несколько логических уровней:

```text
┌─────────────────────────────────────────────┐
│                MCP Interface                │
│                  app/tools                  │
└──────────────────────┬──────────────────────┘
                       │
┌──────────────────────▼──────────────────────┐
│             Application Services            │
│                app/services                 │
└──────────────────────┬──────────────────────┘
                       │
        ┌──────────────▼──────────────┐
        │   Repositories / ORM / DB   │
        │ app/db/repositories         │
        │ app/db/models               │
        │ PostgreSQL                  │
        └──────────────┬──────────────┘
                       │
        ┌──────────────▼──────────────┐
        │       Infrastructure        │
        │ MCP / OAuth / Telegram      │
        │ notifications / config      │
        └─────────────────────────────┘
```

Основное правило: верхний слой не должен получать прямой доступ к низкоуровневым деталям, если соответствующая ответственность уже принадлежит следующему слою.

---

## 3. MCP layer

### 3.1. FastMCP

Экземпляр сервера создаётся в `app/core/mcp.py`.

Основные свойства текущего MCP-сервера:

- `FastMCP("Lifelog")`;
- stateless HTTP;
- JSON responses;
- streamable HTTP transport;
- локальный bind на `127.0.0.1:8001`;
- ограничение допустимого host через transport security.

Точка запуска:

```text
app/main.py
```

При импорте `app.tools.*` MCP-инструменты регистрируются в общем экземпляре `mcp`.

---

## 4. MCP tools

Каталог `app/tools` является публичным interface layer.

Ключевые инструменты:

### `remember`

Высокоуровневое сохранение обычных воспоминаний: факты, события, мысли, идеи и наблюдения. Финансовые операции этим инструментом не записываются.

### `create_memory`

Низкоуровневое создание memory.

### `update_memory` / `delete_memory`

Изменение и удаление memory в ownership scope.

### `create_finance_event`

Создание структурированной финансовой операции.

### `update_finance_event`

Изменение метаданных финансовой операции.

### `update_finance_item` / `delete_finance_item`

Изменение или удаление отдельной позиции финансовой операции с атомарным
пересчётом `FinanceEvent.total_amount` по оставшимся позициям.

### `delete_finance_event`

Удаление финансовой операции вместе с её позициями.

### `query_finance`

Чтение, фильтрация и агрегация финансовых данных.

### `ping`

Минимальная системная проверка доступности MCP.

---

## 5. Почему AI и backend разделены

LifeLog не превращает backend в дополнительную LLM-систему.

Например, запрос:

```text
Сколько я потратил на бензин в августе?
```

AI формирует структурированные аргументы:

```text
mode = total_amount
category = transport
product_name = бензин
date_from = ...
date_to = ...
operation_type = expense
```

После этого backend выполняет детерминированный SQL-запрос.

```text
AI = interpretation
Backend = execution
```

---

## 6. Application services

Каталог `app/services` содержит бизнес-логику.

### MemoryService

Отвечает за создание, получение, перечисление и удаление memory в ownership scope. При создании memory он также добавляет Notification Outbox event в текущую транзакцию.

Поток:

```text
create_memory
    ↓
get_request_user_id()
    ↓
MemoryService.create_memory()
    ↓
INSERT memories
    ↓
INSERT notification_outbox
    ↓
COMMIT
```

### FinanceService

Создаёт `FinanceEvent` и связанные `FinanceItem`, добавляет outbox event и выполняет единый commit.

Поток:

```text
create_finance_event
        ↓
FinanceService
        ↓
FinanceEvent + FinanceItem[]
        ↓
notification_outbox
        ↓
COMMIT
```

### FinanceQueryService

Маршрутизирует структурированный финансовый запрос в один из поддерживаемых режимов и передаёт его `FinanceRepository`.

### NotificationOutboxService

Добавляет durable notification event в текущую транзакцию вызывающего сервиса.

Ключевой принцип: бизнес-объект и его outbox event создаются атомарно.

---

## 7. Database layer

### SQLAlchemy

Подключение реализовано в `app/db/database.py`.

Используются SQLAlchemy 2.x, PostgreSQL, psycopg и connection pool с `pool_pre_ping=True`.

### ORM models

Основные сущности:

| Таблица | Назначение |
|---|---|
| `users` | Внутренний владелец данных |
| `user_identities` | Внешняя identity пользователя |
| `memories` | Долговременная память |
| `finance_events` | Финансовые операции |
| `finance_items` | Позиции финансовых операций |
| `telegram_notification_subscriptions` | Telegram-подписки |
| `notification_outbox` | Durable queue событий |
| `oauth_clients` | OAuth clients |
| `oauth_authorization_requests` | Незавершённые OAuth-запросы |
| `oauth_authorization_codes` | Одноразовые authorization codes |
| `oauth_refresh_tokens` | Refresh token storage |

---

## 8. Ownership and data isolation

Ownership является серверным свойством пользовательской операции.

`get_request_user_id()` получает текущего пользователя:

```text
MCP access token
      ↓
JWT subject
      ↓
User.uuid
      ↓
User.id
```

Если authentication отключён, используется bootstrap user из конфигурации.

Ownership применяется непосредственно в SQL-запросах:

```sql
WHERE memories.user_id = :current_user_id
```

и:

```sql
WHERE finance_events.user_id = :current_user_id
```

Это является защитой на серверном уровне, а не только UI-фильтрацией.

---

## 9. Financial query pipeline

```text
MCP client
   ↓
query_finance(...)
   ↓
FinanceQueryRequest
   ↓
FinanceQueryService
   ↓
FinanceRepository
   ↓
SQLAlchemy
   ↓
PostgreSQL
   ↓
FinanceQueryResult
```

Поддерживаются режимы `total_amount`, `transactions`, `group_by_category`, `group_by_place`, `statistics` и `summary`.

Фильтры: категория, тип операции, название товара, место, `date_from`, `date_to`.

Сортировка: `date`, `amount`, `title`, `place`, `category`, `count`; направления `asc` и `desc`.

Результаты режима `transactions` содержат `event_id` и `item_id`, необходимые
для адресного редактирования и удаления позиции через MCP.

---

## 10. Transaction boundaries

Изменяющие состояние операции выполняются транзакционно.

```text
BEGIN
  INSERT business object
  INSERT notification_outbox
COMMIT
```

При исключении выполняется `ROLLBACK`.

Это особенно важно для memory и finance creation.

---

## 11. Transactional Outbox

Outbox решает проблему согласованности между PostgreSQL и внешней системой доставки.

Без Outbox:

```text
save DB
  ↓
send Telegram
```

После сбоя между этими шагами состояние могло бы потерять уведомление.

С Outbox:

```text
BEGIN
  save business object
  save notification event
COMMIT
       ↓
  Dispatcher
       ↓
  Telegram API
```

Если Telegram недоступен, событие остаётся в PostgreSQL.

---

## 12. Notification Dispatcher

Точка входа worker-процесса:

```text
scripts/notification_dispatcher.py
```

Цикл обработки:

```text
poll
 ↓
claim event
 ↓
processing
 ↓
deliver
 ↓
sent / retry / failed
```

Для конкурирующего доступа используется PostgreSQL row locking с `FOR UPDATE SKIP LOCKED`.

### Processing lease

После claim событие получает статус `processing` и временную lease. Если worker завершился аварийно, событие может снова стать доступным после истечения lease.

### Retry

Временные ошибки возвращают событие в `pending` с exponential backoff. Дополнительно учитывается `retry_after` Telegram. Максимальный backoff ограничен одним часом.

### Failure classes

- `sent` — успешно доставлено;
- `pending` — ожидает повторной попытки;
- `failed` — терминальная ошибка;
- Telegram 403 — подписка отключается, событие завершается как `failed`.

---

## 13. Telegram architecture

Telegram имеет две независимые роли.

### Identity

`Telegram OIDC` используется для идентификации пользователя.

### Notification

`Telegram Bot API` используется только для доставки уведомлений.

Эти подсистемы не должны смешиваться. Благодаря этому канал уведомлений можно менять независимо от identity provider.

---

## 13. Telegram architecture

Telegram is intentionally split into three independent roles:

1. **Telegram OIDC** — user identity for LifeLog OAuth.
2. **Telegram Bot API** — system notification delivery.
3. **Telegram User API / MTProto** — access to the user's own Telegram account.

The third subsystem is a personal-account integration rather than a bot integration. It uses Telethon and an encrypted MTProto session associated with the LifeLog user.

### 13.1. Personal Telegram account

The authenticated Telegram account is stored as `TelegramAccount`, one active account per LifeLog user in the current implementation.

```text
LifeLog user
    ↓
TelegramAccount
    ├── telegram_user_id
    ├── username / display_name
    └── encrypted MTProto session
            ↓
       Telethon client
            ↓
       Telegram network
```

The raw MTProto session is never stored in plaintext. Session material is encrypted before persistence. Telegram credentials are therefore kept server-side and are not exposed through MCP tool arguments.

### 13.2. Authentication flow

Two staged authorization paths are supported:

- phone/code authentication;
- official Telegram QR login.

QR login uses Telethon's `QRLogin` flow. The live QR runtime object and its token remain process-local; the database stores only the short-lived durable authorization state needed to complete the flow. After successful authorization the resulting MTProto session is encrypted and persisted.

```text
MCP client
    ↓
telegram_connect
    ↓
QR / phone authorization
    ↓
Telethon session
    ↓
encrypted MTProto session
    ↓
TelegramAccount
```

### 13.3. Explicit contact allowlist

Personal-account operations are not exposed as unrestricted access to the user's entire Telegram history.

A peer must first be explicitly allowlisted in `telegram_allowed_peers`.

Stable identity is based on:

```text
peer_type + telegram_peer_id
```

rather than on a display name.

Contact discovery is performed by `telegram_search_contacts`, which can resolve a user by phone, username, Telegram ID, or contact name. Search results expose the stable Telegram peer ID so that contacts without a username can still be safely allowlisted.

```text
telegram_search_contacts
        ↓
resolved peer
        ↓
telegram_add_allowed_contact
        ↓
telegram_allowed_peers
        ↓
read / monitor / send
```

### 13.4. Reading messages

Two read paths are deliberately separated:

- `telegram_get_messages` — explicit history read for one allowlisted peer, optionally starting after a message ID;
- `telegram_get_new_messages` — reads new inbound messages only from active dialogue monitors and advances a stored watermark.

Outgoing messages are filtered from the "new messages" result.

Monitoring is bounded to explicitly allowlisted peers. The architecture does not perform a global scan of all Telegram dialogs.

### 13.5. Monitoring model

`TelegramDialogueMonitor` stores the monitor state for one allowlisted peer.

```text
telegram_start_monitoring
        ↓
TelegramDialogueMonitor
        ├── monitor_kind
        ├── anchor_message_id
        ├── last_read_message_id
        └── is_active
```

Supported monitor kinds are currently `new_messages` and `reply`.

The monitor can be enabled or disabled independently of the contact allowlist.

The current monitor is a bounded polling primitive. It does not itself imply that ChatGPT receives unsolicited MCP messages. A client must call `telegram_get_new_messages` to retrieve new inbound messages.

### 13.6. Safe sending model

Sending is intentionally a two-stage operation.

```text
telegram_send_message
        ↓
encrypted pending draft
        ↓
interactive confirmation UI
        ↓
telegram_confirm_send
        ↓
Telegram User API
```

`telegram_send_message` never sends to Telegram directly. It creates a short-lived `TelegramSendRequest` containing the encrypted message draft and the resolved allowlisted recipient.

The MCP Apps confirmation card displays the exact recipient and exact message. The user must explicitly choose **Send** or **Cancel**.

On successful delivery, the ciphertext is cleared. Expired, cancelled, or already-used requests cannot be sent again.

The UI resource is exposed through:

```text
ui://telegram/send-confirmation-v1.html
```

The text fallback remains available for clients that do not render the MCP Apps card.

### 13.7. Telegram security boundary

The personal Telegram integration enforces several boundaries:

- LifeLog ownership is checked before every account operation.
- Personal-account access is limited to allowlisted peers.
- Peer identity uses Telegram IDs, not display-name matching.
- MTProto sessions are encrypted at rest.
- QR runtime state is not persisted as reusable QR tokens.
- Message drafts waiting for confirmation are encrypted.
- Sending requires an explicit second step.
- Read operations return normalized message data rather than exposing the raw Telethon client.
- User message bodies are not written to application logs by the Telegram monitor worker.

This keeps Telegram as an external integration boundary rather than turning it into an uncontrolled secondary database.

### 13.8. Personal Telegram vs Bot Telegram

These paths must remain separate:

| Subsystem | Role | Typical data flow |
|---|---|---|
| Telegram OIDC | Identity | Telegram → OAuth → LifeLog user |
| Telegram Bot API | Notifications | LifeLog Outbox → Dispatcher → Telegram bot |
| Telegram User API / MTProto | Personal account | LifeLog → encrypted session → user's Telegram account |

A future change to notification delivery should not require changing the personal Telegram integration, and vice versa.


## 14. OAuth architecture

OAuth provider находится в `app/core/oauth_provider.py`.

Основной pipeline:

```text
MCP client
   ↓
Dynamic Client Registration
   ↓
Authorization request
   ↓
Telegram OIDC
   ↓
authorization code
   ↓
PKCE verification
   ↓
JWT access token
   ↓
refresh token
```

Authorization requests и codes имеют ограниченный срок жизни и хранятся в PostgreSQL в hashed/temporary form.

Access token — подписанный JWT с subject пользователя, client id, scopes, issuer, audience, lifetime и token version.

Refresh token хранится в виде hash и ротируется при обновлении.

Для массового отзыва JWT используется `User.token_version`.

---

## 15. Telegram OIDC security

Реализация проверяет:

- authorization code exchange;
- PKCE verifier;
- ID token signature;
- JWKS key;
- issuer;
- audience;
- token lifetime;
- nonce.

JWKS ключи кэшируются на ограниченный срок.

---

## 16. Dynamic Client Registration

OAuth clients хранятся в `oauth_clients`.

Client secret, если он есть, шифруется перед сохранением.

Текущая реализация явно отклоняет `private_key_jwt`.

---

## 17. Schemas

Каталог `app/schemas` отделяет MCP arguments/results от ORM entities.

Основные схемы:

- `FinanceEventCreate`;
- `FinanceItemCreate`;
- `FinanceQueryRequest`;
- `FinanceQueryResult`;
- memory schemas.

---

## 18. Repository pattern

`FinanceRepository` является границей между application service и SQLAlchemy queries.

Repository отвечает за filters, joins, grouping, sorting, aggregation и limit.

Service выбирает use case, repository выполняет запрос.

---

## 19. Alembic migrations

Изменения PostgreSQL schema управляются Alembic.

История эволюции:

```text
initial schema
      ↓
finance tables
      ↓
finance categories
      ↓
multi-user ownership
      ↓
OAuth storage
      ↓
notification outbox
```

Production schema должна изменяться через миграции.

---

## 20. Infrastructure boundary

Приложение разделено на два долгоживущих процесса:

### MCP

`lifelog-mcp` отвечает за MCP transport, authentication, tools и бизнес-операции.

### Dispatcher

`lifelog-notification-dispatcher` отвечает за доставку событий.

Dispatcher может перезапускаться независимо от MCP process.

---

## 21. Deployment model

```text
Internet
   │
   ▼
HTTPS reverse proxy
   │
   ▼
127.0.0.1:8001
   │
   ▼
LifeLog MCP
   │
   ▼
PostgreSQL
```

Отдельно:

```text
PM2
 ├── lifelog-mcp
 └── lifelog-notification-dispatcher
```

PostgreSQL не должен публиковаться напрямую наружу.

---

## 22. Configuration boundary

Центральная конфигурация находится в `app/config/settings.py`.

Используется Pydantic Settings и environment variables.

Конфигурация включает PostgreSQL, bootstrap user, authentication, OAuth, Telegram OIDC, Telegram Bot и JWT signing key.

При `AUTH_ENABLED=true` обязательные OAuth settings проходят дополнительную валидацию.

---

## 23. Logging

Логирование конфигурируется через `app/config/logging.py`.

Границы, на которых выполняется логирование:

```text
MCP tool
   ↓
service
   ↓
notification delivery
```

Логи предназначены для диагностики. Пользовательский контент не должен попадать в notification payload.

---

## 24. Error handling

Для изменяющих состояние операций:

```text
MCP tool
   ↓
Service
   ↓
try/except
   ↓
rollback
   ↓
raise
```

Dispatcher классифицирует ошибки доставки по retryable/terminal/blocked subscription.

---

## 25. Domain expansion

В `app/domains` подготовлены каркасы:

```text
contacts
events
files
finance
health
nutrition
reminders
```

Наличие каталога не означает полноценную реализацию domain service.

Рекомендуемый путь добавления нового домена:

```text
domain
  ↓
ORM model
  ↓
repository
  ↓
service
  ↓
schema
  ↓
MCP tool
  ↓
tests
```

---

## 26. Future semantic memory

Выделен `app/services/embedding_service.py`.

Сейчас это заготовка.

Будущий retrieval pipeline:

```text
user query
    ↓
embedding
    ↓
vector similarity
    ↓
relevant memories
    ↓
context for AI
```

Embeddings должны быть дополнительным retrieval layer, а не заменой PostgreSQL как source of truth.

---

## 27. Architectural invariants

### Invariant 1 — ownership

Каждая пользовательская операция выполняется в контексте конкретного владельца.

### Invariant 2 — transactional writes

Memory/finance write и соответствующее outbox event создаются в одной транзакции.

### Invariant 3 — deterministic queries

Финансовая аналитика выполняется детерминированным SQLAlchemy-кодом.

### Invariant 4 — transport isolation

Внешний клиент видит MCP interface, а не прямой доступ к PostgreSQL.

### Invariant 5 — identity isolation

Telegram OIDC и Telegram Bot API остаются разными подсистемами.

### Invariant 6 — notification decoupling

Основная бизнес-операция не должна ждать Telegram API.

### Invariant 7 — domain isolation

Новый domain не должен переносить бизнес-логику в MCP tool слой.

---

## 28. Extension rules

При добавлении новой функции:

1. определить domain/use case;
2. определить schema;
3. разместить бизнес-правила в service;
4. вынести сложные запросы в repository;
5. привязать данные к `user_id`, если объект пользовательский;
6. при необходимости создать Outbox event в той же транзакции;
7. добавить MCP tool только как interface layer;
8. добавить tests на happy path, ошибки и ownership.

Предпочтительный путь:

```text
MCP tool
   ↓
Service
   ↓
Repository / integration service
   ↓
DB / external API
```

---

## 29. Testing strategy

В репозитории присутствуют тесты для:

- database connectivity;
- memory service;
- memory errors;
- notifications;
- OAuth provider;
- finance queries;
- remember tool;
- remember errors;
- user scoping.

Критические инварианты при расширении системы:

```text
ownership
transaction boundaries
token validation
one-time OAuth codes
refresh token rotation
notification retry semantics
```

---

## 30. Source of truth

### PostgreSQL

Source of truth для users, identities, memories, finance, OAuth persistence и notification state.

### MCP

Protocol interface.

### ChatGPT / AI

Interpreter и orchestration layer.

### Telegram

External identity and delivery channel.

Telegram не является authoritative storage для пользовательских данных.

---

## 31. Evolution path

Целевая модель:

```text
                     ┌───────────────┐
                     │   AI / MCP    │
                     └───────┬───────┘
                             │
                     ┌───────▼───────┐
                     │ Application   │
                     │   Services    │
                     └───────┬───────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
              ▼              ▼              ▼
          Memory          Finance        Domains
              │              │              │
              └──────────────┼──────────────┘
                             ▼
                       PostgreSQL
                             │
                  ┌──────────┴──────────┐
                  ▼                     ▼
             Retrieval             Outbox
            / Embeddings                │
                                        ▼
                                  Notifications
```

Архитектура должна позволять добавлять новые LifeLog domains, semantic retrieval, notification channels, identity providers и frontend/Mini App interfaces без переноса этих функций в MCP protocol layer.

---

## 32. Current implementation boundary

### Реализовано

- FastMCP server;
- PostgreSQL + SQLAlchemy;
- memory persistence;
- finance persistence;
- finance querying;
- multi-user ownership;
- Telegram OIDC/OAuth infrastructure;
- Telegram notifications;
- transactional outbox;
- dispatcher;
- tests for critical paths.

### Архитектурный каркас

- `app/domains/*`;
- `embedding_service.py`;
- дополнительные notification channels.

Документ намеренно отделяет реализованный код от архитектурных заделов.

---

## 33. Summary

LifeLog MCP архитектурно представляет собой связку:

```text
AI interface
    +
MCP protocol
    +
application services
    +
typed schemas
    +
PostgreSQL persistence
    +
multi-user ownership
    +
OAuth identity
    +
Transactional Outbox
    +
independent notification worker
```

Такое разделение создаёт основу для расширяемой персональной платформы данных, где AI является интерфейсом, а данные, правила и безопасность остаются под контролем серверной системы.
