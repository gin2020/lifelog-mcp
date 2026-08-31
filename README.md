# Lifelog MCP

Lifelog MCP — персональное хранилище памяти и финансовых событий с доступом через Model Context Protocol (MCP).

## Архитектура

```
ChatGPT
    │
    ▼
Lifelog MCP (FastMCP)
    │
    ▼
PostgreSQL
    │
    ├── Memories
    ├── Finance Events
    ├── Notification Outbox
    └── Telegram Subscriptions
            │
            ▼
Notification Dispatcher
            │
            ▼
Telegram Bot API
```

## Основные возможности

- Хранение долговременной памяти.
- Учёт финансовых операций.
- Авторизация через Telegram Login.
- Подтверждение сохранения данных через Telegram.
- Надёжная доставка уведомлений через Outbox Pattern.

---

# Требования

- Python
- PostgreSQL
- PM2
- Nginx
- HTTPS

---

# Первый запуск

## 1. Клонировать проект

```bash
git clone ...
cd lifelog-mcp
```

## 2. Создать виртуальное окружение

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 3. Заполнить .env

Минимально необходимо:

```text
DATABASE_URL=
AUTH_SECRET_KEY=

TELEGRAM_CLIENT_ID=
TELEGRAM_CLIENT_SECRET=

TELEGRAM_BOT_TOKEN=
TELEGRAM_BOT_USERNAME=
TELEGRAM_WEBHOOK_SECRET=
TELEGRAM_WEBHOOK_URL=
```

---

# Миграции

```bash
alembic upgrade head
```

---

# Регистрация Telegram Webhook

После первого запуска необходимо ОБЯЗАТЕЛЬНО зарегистрировать webhook.

```bash
set -a
source .env
set +a

curl --fail-with-body \
  --data-urlencode "url=$TELEGRAM_WEBHOOK_URL" \
  --data-urlencode "secret_token=$TELEGRAM_WEBHOOK_SECRET" \
  --data-urlencode 'allowed_updates=["message"]' \
  "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook"
```

Проверка:

```bash
curl \
"https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo"
```

Ожидаемый результат:

- `"ok": true`
- `"url"` содержит адрес webhook.

Если `"url": ""`, webhook НЕ зарегистрирован.

Без зарегистрированного webhook команды `/start` и `/stop` работать не будут.

---

# Запуск MCP

```bash
pm2 start ...
```

---

# Запуск Notification Dispatcher

```bash
pm2 start .venv/bin/python \
  --name lifelog-notification-dispatcher \
  --cwd /root/lifelog-mcp \
  -- -m scripts.notification_dispatcher
```

---

# Проверка

1. Авторизоваться через Telegram Login.
2. Написать боту:

```
/start
```

3. Проверить таблицу:

```sql
SELECT * FROM telegram_notification_subscriptions;
```

Должна появиться запись.

4. Создать Memory или FinanceEvent.

5. Проверить:

```sql
SELECT status
FROM notification_outbox
ORDER BY created_at DESC
LIMIT 1;
```

Ожидается:

```
sent
```

6. Получить сообщение в Telegram.

---

# Полезные команды

Перезапуск MCP

```bash
pm2 restart lifelog-mcp --update-env
```

Перезапуск Dispatcher

```bash
pm2 restart lifelog-notification-dispatcher --update-env
```

Просмотр логов MCP

```bash
pm2 logs lifelog-mcp
```

Просмотр логов Dispatcher

```bash
pm2 logs lifelog-notification-dispatcher
```

Проверка webhook

```bash
curl \
"https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo"
```

---

# Важно

Если уведомления перестали приходить:

1. Проверить `getWebhookInfo`.
2. Проверить наличие записи в `telegram_notification_subscriptions`.
3. Проверить `notification_outbox`.
4. Проверить логи dispatcher.
5. Проверить статус процессов PM2.

---

# Архитектурные принципы

- MCP не зависит от Telegram.
- Telegram не влияет на сохранение данных.
- Notification Dispatcher работает независимо.
- Все уведомления проходят через Notification Outbox.
- Возможна дальнейшая интеграция Email, Push и других каналов.
