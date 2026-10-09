# Kovapp beta 3, версия 11

Kovapp («Ковчег») — задачи, проекты, склад, оборудование и мессенджер компании: сайт для
компьютера и телефона и приложение для Android. Beta 3 объединяет две прежние линии разработки —
веб-версию (до v22) и мобильную (до v67) — в одну: один сервер для сайта и приложения, один
код интерфейса `web_app`, из которого собирается и APK.

| Документ | О чём |
|---|---|
| `docs/RUN-BETA3.md` | **как запустить**: Windows, рабочий сервер Debian с nginx и MariaDB, HTTPS, уведомления, сборка APK, ночные работы, проверка, откат |
| `docs/PC-MIGRATION.md` | как перенести изменения в настольное приложение (ПК-версию) |
| `docs/permissions.md` | права всех ролей |
| `docs/performance-2026-09-28.md` | работа под нагрузкой: процессы, пределы, MariaDB |
| `docs/db-compression-2026-10-03.md` | сжатие таблиц с историей и резервные копии |
| `docs/mobile-architecture.md`, `docs/mobile-security.md`, `mobile/README.md` | приложение для Android: устройство, безопасность, сборка |
| `docs/history/` | журналы изменений обеих прежних линий |

Автотесты — отдельный модуль `kovapp_autotest` (документация — его `docs/RUNNING.md`).
Перечень всех изменений с исходной версии — документ `docs/kovapp-beta3-changes.docx`.

---

# Ковчег — как это лежит на сервере

Одна папка, внутри всё нужное. Ниже — что где и в каком порядке запускать.

```
kovcheg/                     ← корень, его путь сервер зовёт APP_ROOT
├── .env                     настройки (скопировать из .env.example)
├── .env.example             образец с пояснениями к каждой строке
│
├── server/                  сервер на FastAPI
│   ├── server.py            точка запуска
│   ├── server_app/          приложение: маршруты, права, обработка файлов
│   ├── database/            модели и первичное создание базы
│   ├── tests/               дымовая проверка
│   ├── gen_vapid.py         создание ключей для уведомлений на телефон
│   ├── set_password.py      смена пароля сотруднику из командной строки
│   ├── find_orphan_files.py поиск файлов хранилища без записи в базе
│   ├── migrate.py           перенос данных между версиями
│   └── requirements-v2.txt  дополнительные зависимости
│
├── web_app/                 то, что открывается в браузере
│   ├── index.html           само приложение
│   ├── sheet.html           отдельная вкладка для таблиц
│   ├── js/                  код: core/ — общее, views/ — по файлу на раздел
│   ├── css/                 оформление: theme.css — цвета обеих тем, common.css — общие
│   │                        элементы и окна, sections/ — по файлу на раздел, files.css —
│   │                        файлы и просмотр, overrides.css — замок и размеры под палец
│   ├── chat/                переписка мессенджера (свои chat.js и chat.css)
│   └── icons/ stickers/     значки и наборы стикеров
│
├── mobile/                  приложение для Android (Kovapp), см. mobile/README.md
│
├── sql/
│   ├── kovcheg-all.sql              обновление базы, шаги 2–6 подряд
│   ├── kovcheg-equipment-photo.sql  только снимок оборудования, отдельно
│   └── kovcheg-native-push.sql      только таблица телефонов с приложением
│
├── docs/
│   ├── file-access-plan.md   доступ к файлам: правила и включение FILE_ACCESS_STRICT
│   ├── permissions.md        права всех ролей сотрудников
│   ├── changes-2026-09-25.md что изменено в этой версии
│   ├── mobile-*.md           приложение: устройство, безопасность, проверки
│   └── changes-2026-09-22.md изменения прошлой версии
│
├── file_storage/            вложения чата, файлы задач и проектов, снимки
├── logs/                    журналы работы
└── backups/                 снимки базы
```

Три нижние папки создаются сами при первом запуске. Нужно только, чтобы у
службы было право записи в них.

---

## Развёртывание с нуля

### 1. Положить папку и поставить зависимости

```bash
cd /opt/kovcheg          # или куда вам удобнее
python3 -m venv .venv
. .venv/bin/activate
pip install -r server/requirements-v2.txt
```

В `requirements-v2.txt` только дополнения к основному набору: `Pillow`,
`pywebpush`, `psutil` и `openpyxl`. Без `openpyxl` таблицы xlsx будут
открываться по-старому — картинкой из PDF, а не ячейками.

### 2. Настроить

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"   # ключ подписи
nano .env
```

Обязательно заполнить: `DB_PASSWORD`, `JWT_SECRET_KEY`, `INIT_ADMIN_PASSWORD`.
Проверить `SERVER_PORT` — по умолчанию в коде 8000, в примере настроек 54545.
Со слабым или коротким ключом подписи сервер не запустится — это сделано
намеренно, чтобы такой сервер не оказался в работе по недосмотру.

### 3. Завести базу

```sql
CREATE DATABASE kovcheg CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE kovcheg_messenger CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'kovcheg'@'localhost' IDENTIFIED BY 'ваш пароль';
GRANT ALL PRIVILEGES ON kovcheg.* TO 'kovcheg'@'localhost';
GRANT ALL PRIVILEGES ON kovcheg_messenger.* TO 'kovcheg'@'localhost';
FLUSH PRIVILEGES;
```

Таблицы сервер создаст сам при первом запуске.

### 4. Запустить

```bash
python server/server.py
```

Порт берётся из `SERVER_PORT`; если его не задать, будет 8000. В примере
настроек стоит 54545 — тот, на котором сервер работает сейчас.

Приложение открывается по адресу `http://<сервер>:54545/app/`, если в
`.env` заполнен `WEB_APP_DIR`. Иначе `web_app` раздаёт nginx — см. ниже.

---

## Обновление уже работающего сервера

1. **Снять резервную копию.**
   ```bash
   mysqldump -u root -p kovcheg > kovcheg_backup.sql
   ```

2. **Заменить `server/` и `web_app/`.** Файлы `.env`, `file_storage/`,
   `logs/` и `backups/` не трогать.

3. **Применить SQL:**
   ```bash
   mysql -u root -p kovcheg < sql/kovcheg-all.sql
   ```
   Скрипт можно выполнять повторно: каждый шаг сначала проверяет, нужен
   ли он. В конце он выводит сводку — все ли таблицы на месте и
   проставлены ли маркеры прав.

4. **Доставить зависимости** (если обновляетесь со старой версии):
   ```bash
   pip install -r server/requirements-v2.txt
   ```

5. **Перезапустить сервер.**

После обновления попросите людей обновить вкладку жёстко (Ctrl+F5):
браузеры держат значки и файлы оформления в кэше долго.

---

## Раздача через nginx

Если веб-приложение отдаёт nginx, оставьте `WEB_APP_DIR` в `.env` пустым.

```nginx
server {
    listen 80;
    server_name kovcheg.example.ru;

    # Веб-приложение
    location / {
        root /opt/kovcheg/web_app;
        try_files $uri $uri/ /index.html;
    }

    # Сервер
    location /api/ {
        proxy_pass http://127.0.0.1:54545/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    # Сокет для чата и живых обновлений
    location /ws/ {
        proxy_pass http://127.0.0.1:54545;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600s;
    }

    # Вложения бывают большими
    client_max_body_size 512m;
}
```

Адрес сервера для веб-приложения задаётся в `web_app/js/core/api-host.js`.
Пустое значение означает «тот же адрес, с которого открыта страница».

---

## Служба systemd

```ini
[Unit]
Description=Kovcheg
After=network.target mysql.service

[Service]
Type=simple
User=kovcheg
WorkingDirectory=/opt/kovcheg
ExecStart=/opt/kovcheg/.venv/bin/python /opt/kovcheg/server/server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

`WorkingDirectory` должен указывать на корень: оттуда берётся `.env` и
разрешаются относительные пути вроде `./file_storage`.

---

## Что стоит проверить после запуска

- Вход администратора и смена его пароля.
- Открытие xlsx — должны быть ячейки, а не картинка. Если картинка,
  значит не установлен `openpyxl`.
- Документы Word и PowerPoint (docx, pptx) и PDF показывает само устройство пользователя; LibreOffice на сервере не нужен. Старые форматы (doc, odt, rtf, ppt) скачиваются.
- Отправка вложения в чат: проверяется и запись в `file_storage`, и
  перекодирование.
- Уведомления на телефон — только если заполнены ключи VAPID.
- Уведомления в приложении Kovapp — строка «Уведомления приложения Kovapp:
  включены» в журнале при старте; подробно — `mobile/README.md`, раздел 4.
- Доступ к файлам: `FILE_ACCESS_STRICT=0` в `.env` на время наблюдения, строки
  «отказ бы был» в журнале, затем `1`. Подробно — `docs/file-access-plan.md`.

---

## Приложение для Android

В папке `mobile/` — приложение Kovapp: тот же интерфейс, что у сайта, плюс
уведомления при закрытом приложении (через Google или собственный канал для
Huawei и Honor), сжатие фото и видео на телефоне, скачивание в «Загрузки».
Сборка APK, настройка сервера и проверка на ПК без телефона — в
`mobile/README.md`. Для проверки на ПК есть симулятор телефона: двойной
щелчок по `mobile/simulator.bat` (или `python mobile/tools/simulator.py`). Устройство — `docs/mobile-architecture.md`, безопасность —
`docs/mobile-security.md`, результаты проверок — `docs/mobile-test-report.md`.

Серверу для приложения нужен модуль уведомлений: он входит в `server/`,
таблица `native_push_devices` создаётся сама при запуске. На сайт в браузере
модуль не влияет — Web Push работает как раньше.
