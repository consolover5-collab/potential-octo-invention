# tg-parsing

Userbot-мониторинг Telegram-барахолок: ловит объявления по ключевым словам или фото (Groq Vision) и автоматически пишет продавцу DM.

## Архитектура

| Компонент | Роль |
|-----------|------|
| **Telethon userbot** | Слушает сообщения в заданных группах от вашего аккаунта |
| **aiogram control-bot** | Управление через Telegram: кнопки, настройки, лог |
| **Groq Vision** | Анализ фото когда нет текста (llava-v1.5-7b, бесплатно) |
| **SQLite** | Хранение найденных объявлений и dedup-таблица для DM |

## Быстрый старт

```bash
cp config.example.json config.json   # заполнить поля
pip install -r requirements.txt
python main.py
```

Откройте control-бота в Telegram и отправьте `/start`.

## config.json — ключевые поля

```jsonc
{
  "telegram": {
    "api_id": 123456,          // my.telegram.org
    "api_hash": "abc...",
    "phone": "+7...",          // номер userbot-аккаунта
    "bot_token": "7...:AA...", // токен от @BotFather
    "owner_id": 123456789      // ваш Telegram user_id (необязательно)
  },
  "monitoring": {
    "chats": ["@market", -1001234567890],  // @username или chat_id
    "keywords": ["колонка", "JBL"],
    "max_price": 10000,        // 0 = без ограничений
    "use_vision": true
  },
  "actions": {
    "auto_dm": true,
    "dry_run": false,          // true = логировать без реальных отправок
    "dm_template": "Привет! Видел объявление про {type}...",
    "forward_mode": "notify_with_meta",  // или "forward_raw"
    "notify_chat_id": "me",    // числовой chat_id или "me" (→ вам после /start)
    "opt_out_list": []
  },
  "vision": {
    "api_key": "gsk_..."       // Groq API key
  }
}
```

Полный шаблон — `config.example.json`.

## Control-бот — кнопки

**Главное меню**

| Кнопка | Действие |
|--------|----------|
| 📡 Чаты | Список + добавить/удалить. Принимает `@username`, chat_id или **пересланное сообщение** из группы |
| 🔑 Ключевые слова | Слова/фразы через запятую |
| 💰 Макс. цена | Фильтр по цене (рублей) |
| 🧪 Тест | Проверить текст/фото через pipeline вручную |
| 📋 Последние находки | 10 последних совпадений из БД |
| ⚙️ Настройки | Vision, уведомления, авторизация userbot, перезапуск |
| 🎯 Управление действиями | Авто-DM, forward_mode, dry-run, шаблон, opt-out |
| 📜 Лог действий | 20 последних событий |
| ⏸/▶️ Пауза/Запуск | Userbot без перезапуска сервиса |
| 📊 Лимиты | Остаток квот DM и Groq |

**Настройки (⚙️)**

| Кнопка | Действие |
|--------|----------|
| 👁 Vision | Вкл/выкл анализ фото |
| 📬 Кому уведомления | Сохранить chat_id или `me` |
| 🔐 Авторизовать userbot | QR-код + ссылка `tg://login?token=…` |
| 🔢 Ввести код вручную | Код из официального Telegram-клиента, затем 2FA при необходимости. Оба сообщения **удаляются** после обработки |
| 🔄 Перезапустить бота | SIGTERM → systemd поднимает сервис заново (~10 с) |

## Плейсхолдеры шаблона DM (`dm_template`)

`{type}` `{price}` `{link}` `{author}` `{chat_title}` `{message_snippet}`

## Авторизация userbot

Сервис запускается без интерактивного ввода. Если сессия не авторизована — userbot предупреждает в логе и ждёт.

**Авторизация через control-бота:**
1. `/start` → ⚙️ Настройки → 🔐 Авторизовать userbot
2. Отсканируйте QR или перейдите по ссылке `tg://login?token=…` на мобильном
3. Если получаете ошибку "outdated" → 🔢 Ввести код вручную → введите код из Telegram-клиента
4. Если аккаунт с 2FA → бот запросит пароль (сообщение удалится сразу)

> ⚠️ После February 2023 Telegram не присылает коды входа через SMS — только через официальное приложение (`SentCodeTypeApp`).

## 🔑 Groq API (бесплатно)

1. Откройте https://console.groq.com → Sign Up
2. **API Keys** → Create API Key → скопируйте `gsk_…` (показывается один раз)
3. Вставьте в `config.json` → `vision.api_key`

**Бесплатные лимиты:**

| Параметр | Лимит |
|----------|-------|
| Запросов/мин | 30 |
| Запросов/день | 1 000 |
| Токенов/день | 500 000 |

> Vision вызывается **только** когда нет совпадения по тексту, но есть фото. При исчерпании лимита бот продолжает работать по тексту.

## systemd (Linux/VPS)

```bash
sudo cp deploy/tg-parsing.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tg-parsing
```

Рабочая директория и пользователь — в `tg-parsing.service`, адаптируйте под своё окружение.  
Для перезапуска после изменений используйте кнопку **🔄 Перезапустить бота** в control-боте или:

```bash
sudo systemctl restart tg-parsing
```

## Docker

```bash
docker compose -f deploy/docker-compose.yml up -d --build
```

Убедитесь что `config.json`, `data/`, `session/` примонтированы согласно `docker-compose.yml`.
