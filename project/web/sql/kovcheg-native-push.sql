-- =====================================================================
--  Ковчег — таблица телефонов с приложением Kovapp (Android)
--
--  Нужна для уведомлений в приложении. Сервер создаёт её сам при запуске
--  (server_app/migrations.py), этот скрипт — для ручного обновления.
--  Повторный запуск безопасен (IF NOT EXISTS).
--
--  База: MySQL/MariaDB, схема `kovcheg`. Перед запуском — резервная копия:
--      mysqldump -u root -p kovcheg > kovcheg_backup.sql
--  Запуск:
--      mysql -u root -p kovcheg < kovcheg-native-push.sql
-- =====================================================================

-- Секрет устройства хранится только хешем (SHA-256), как пароль.
-- payload_key шифрует текст уведомлений, идущих через Google (FCM).
-- При удалении сотрудника его телефоны удаляются вместе с ним.
CREATE TABLE IF NOT EXISTS native_push_devices (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    device_id     VARCHAR(32)  NOT NULL,
    user_id       INT          NOT NULL,
    provider      VARCHAR(16)  NOT NULL DEFAULT 'direct',
    secret_hash   VARCHAR(64)  NOT NULL,
    payload_key   VARCHAR(64)  NOT NULL,
    fcm_token     TEXT         NULL,
    model         VARCHAR(120) NULL,
    app_version   VARCHAR(32)  NULL,
    created_at    DATETIME     NULL,
    registered_at DATETIME     NULL,
    last_seen_at  DATETIME     NULL,
    UNIQUE KEY uq_native_push_device (device_id),
    KEY ix_native_push_devices_user_id (user_id),
    CONSTRAINT fk_native_push_user FOREIGN KEY (user_id)
        REFERENCES employees (id) ON DELETE CASCADE
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci;

-- Проверка: сколько телефонов зарегистрировано у сотрудников
SELECT e.id, CONCAT_WS(' ', e.last_name, e.first_name) AS employee,
       d.provider, d.model, d.app_version, d.last_seen_at
FROM native_push_devices d JOIN employees e ON e.id = d.user_id
ORDER BY d.last_seen_at DESC;
