-- =====================================================================
--  Ковчег — общий скрипт обновления базы
--
--  Собран из шагов 2–5. Выполнять целиком и один раз; повторный запуск
--  безопасен: таблицы создаются с IF NOT EXISTS, обновления записей
--  отбирают только те строки, которых правка ещё не касалась.
--
--  База: MySQL/MariaDB, схема `kovcheg`.
--
--  ПЕРЕД ЗАПУСКОМ ОБЯЗАТЕЛЬНО СДЕЛАЙТЕ РЕЗЕРВНУЮ КОПИЮ:
--      mysqldump -u root -p kovcheg > kovcheg_backup.sql
--
--  Запуск:
--      mysql -u root -p kovcheg < kovcheg-all.sql
--
--  Что нужно на сервере помимо базы:
--      pip install openpyxl      — показ xlsx настоящими ячейками
--      apt install libreoffice-writer libreoffice-calc libreoffice-impress
--                                — предпросмотр документов (см. libreoffice-setup.md)
--
--  Содержание:
--      Шаг 2. Отдел «Склад», маркеры прав, столбцы проектов
--      Шаг 3. Раздел «Оборудование»
--      Шаг 4. Ответственные за этап
--      Шаг 5. Завершение задачи через подтверждение директора
--      Шаг 6. Снимок позиции оборудования
--      Итог.  Сводная проверка
-- =====================================================================

USE kovcheg;

-- =====================================================================
--  ШАГ 2. Отдел «Склад», маркеры прав, столбцы проектов
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Отдел «Склад»
--
-- Право выдавать товар определялось названием должности «Кладовщик».
-- Переименование должности молча забирало права, опечатка — выдавала.
-- Теперь решает отдел, поэтому людей с этой должностью переводим в
-- отдел «Склад», а саму должность заменяем на нейтральную.
-- ---------------------------------------------------------------------

-- Смотрим, кого это затронет (выполните и убедитесь, что список верный)
SELECT id, last_name, first_name, position, department
FROM employees
WHERE position = 'Кладовщик';

-- Перевод: отдел проставляем, должность делаем обычной
UPDATE employees
SET department = 'Склад',
    position = CASE WHEN position = 'Кладовщик' THEN 'Сотрудник' ELSE position END
WHERE position = 'Кладовщик';

-- Проверка
SELECT department, COUNT(*) AS people
FROM employees
GROUP BY department
ORDER BY department;


-- ---------------------------------------------------------------------
-- 2. Служебная запись администратора
--
-- Она скрывается из выборов по маркеру employees.status = 'администратор'.
-- У записей, заведённых до перехода на маркер, поле пустое, и права
-- выводились из названия должности. Проставляем маркер явно, иначе
-- запись останется видимой в списках.
-- ---------------------------------------------------------------------

-- Кого затронет
SELECT id, last_name, first_name, position, status
FROM employees
WHERE position = 'Администратор' AND (status IS NULL OR status = '');

UPDATE employees
SET status = 'администратор'
WHERE position = 'Администратор' AND (status IS NULL OR status = '');

-- Заодно директора: маркер должен стоять у всех, кому положен
UPDATE employees
SET status = 'директор'
WHERE position = 'Директор' AND (status IS NULL OR status = '');

-- Проверка: у всех ли проставлен маркер
SELECT COALESCE(NULLIF(status, ''), '(пусто)') AS marker, COUNT(*) AS people
FROM employees
GROUP BY marker;


-- ---------------------------------------------------------------------
-- 3. Столбцы раздела «Проекты»
--
-- Столбец строится по создателю проекта, столбца «Прочие» больше нет.
-- Проекты без создателя оказались бы в столбце «Без создателя» — такие
-- записи могли остаться после удаления сотрудника. Находим их.
-- ---------------------------------------------------------------------

SELECT p.id, p.name, p.creator_id, p.responsible_id
FROM projects p
WHERE p.creator_id IS NULL
   OR p.creator_id NOT IN (SELECT id FROM employees);

-- Если такие есть, назначьте им создателя вручную, например ответственного:
--   UPDATE projects SET creator_id = responsible_id
--   WHERE (creator_id IS NULL OR creator_id NOT IN (SELECT id FROM employees))
--     AND responsible_id IN (SELECT id FROM employees);
--
-- Либо конкретного сотрудника (подставьте нужный номер):
--   UPDATE projects SET creator_id = 1 WHERE creator_id IS NULL;


-- ---------------------------------------------------------------------
-- 4. Просмотр таблиц ячейками
--
-- Изменений в базе не требует. На сервере нужен пакет:
--     pip install openpyxl
-- Без него xlsx открывается по-старому — картинкой из PDF.
-- ---------------------------------------------------------------------

-- Готово. Перезапустите сервер после установки openpyxl.


-- =====================================================================
--  ШАГ 3. Раздел «Оборудование»
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Позиции оборудования
--
-- Отдельно от склада проекта: там строка привязана к объекту, здесь
-- оборудование числится за предприятием. «Выдано» отдельным полем не
-- хранится — это сумма незакрытых выдач, иначе цифра и журнал разъедутся.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS equipment_items (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(255)  NULL,
    quantity    DOUBLE        NULL,
    unit        VARCHAR(32)   NULL,
    created_by  INT           NULL,
    created_at  DATETIME      NULL,
    updated_by  INT           NULL,
    updated_at  DATETIME      NULL,
    CONSTRAINT fk_equip_created_by FOREIGN KEY (created_by)
        REFERENCES employees (id) ON DELETE SET NULL,
    CONSTRAINT fk_equip_updated_by FOREIGN KEY (updated_by)
        REFERENCES employees (id) ON DELETE SET NULL
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci;


-- ---------------------------------------------------------------------
-- 2. Выдачи и возвраты
--
-- Путь записи:
--   pending        — склад выдал, ждём подтверждения получателя
--   issued         — получатель подтвердил, вещь числится за ним
--   return_pending — получатель вернул, ждём приёмки складом
--   returned       — склад принял возврат
--   cancelled      — отказ на любом шаге
--
-- Занятыми считаются issued и return_pending: пока возврат не принят,
-- оборудование числится за человеком и в остаток не возвращается.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS equipment_issues (
    id                  INT AUTO_INCREMENT PRIMARY KEY,
    item_id             INT          NOT NULL,
    quantity            DOUBLE       NOT NULL,
    issuer_id           INT          NULL,
    receiver_id         INT          NULL,
    status              VARCHAR(16)  NOT NULL DEFAULT 'pending',
    created_at          DATETIME     NULL,
    confirmed_at        DATETIME     NULL,
    return_requested_at DATETIME     NULL,
    returned_at         DATETIME     NULL,
    returned_to         INT          NULL,
    cancelled_at        DATETIME     NULL,
    cancelled_by        INT          NULL,
    CONSTRAINT fk_equip_issue_item FOREIGN KEY (item_id)
        REFERENCES equipment_items (id) ON DELETE CASCADE,
    CONSTRAINT fk_equip_issue_issuer FOREIGN KEY (issuer_id)
        REFERENCES employees (id) ON DELETE SET NULL,
    CONSTRAINT fk_equip_issue_receiver FOREIGN KEY (receiver_id)
        REFERENCES employees (id) ON DELETE SET NULL,
    CONSTRAINT fk_equip_issue_returned_to FOREIGN KEY (returned_to)
        REFERENCES employees (id) ON DELETE SET NULL,
    CONSTRAINT fk_equip_issue_cancelled_by FOREIGN KEY (cancelled_by)
        REFERENCES employees (id) ON DELETE SET NULL,
    -- «Сколько на руках» считается на каждый показ раздела: без этого
    -- ключа запрос перебирал бы всю таблицу выдач
    INDEX ix_equip_issue_item_status (item_id, status),
    INDEX ix_equip_issue_receiver (receiver_id, status)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci;


-- ---------------------------------------------------------------------
-- 3. Журнал строки
--
-- Одна таблица на оба вида записей: kind = 'issue' — выдачи и возвраты,
-- kind = 'cell' — правки значений. В разделе они показываются вместе,
-- выдачи сверху, изменения ниже.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS equipment_log (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    item_id    INT          NOT NULL,
    kind       VARCHAR(16)  NOT NULL,
    action     VARCHAR(32)  NULL,
    field      VARCHAR(32)  NULL,
    old_value  VARCHAR(255) NULL,
    new_value  VARCHAR(255) NULL,
    issue_id   INT          NULL,
    user_id    INT          NULL,
    target_id  INT          NULL,
    created_at DATETIME     NULL,
    CONSTRAINT fk_equip_log_item FOREIGN KEY (item_id)
        REFERENCES equipment_items (id) ON DELETE CASCADE,
    CONSTRAINT fk_equip_log_user FOREIGN KEY (user_id)
        REFERENCES employees (id) ON DELETE SET NULL,
    CONSTRAINT fk_equip_log_target FOREIGN KEY (target_id)
        REFERENCES employees (id) ON DELETE SET NULL,
    INDEX ix_equip_log_item (item_id, created_at)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci;


-- ---------------------------------------------------------------------
-- 4. Единицы измерения
--
-- Раздел берёт тот же справочник, что и склад проекта. Если он пуст,
-- сервер заполнит его сам при первом открытии — этот блок нужен только
-- если хочется завести единицы заранее.
-- ---------------------------------------------------------------------
INSERT INTO warehouse_units (name, full_name)
SELECT * FROM (SELECT 'шт' AS n, 'Штука' AS f) AS t
WHERE NOT EXISTS (SELECT 1 FROM warehouse_units WHERE name = 'шт');

INSERT INTO warehouse_units (name, full_name)
SELECT * FROM (SELECT 'компл' AS n, 'Комплект' AS f) AS t
WHERE NOT EXISTS (SELECT 1 FROM warehouse_units WHERE name = 'компл');


-- ---------------------------------------------------------------------
-- 5. Кто получит какие права
--
-- Правят «Оборудование» и «Количество» — отдел «Бухгалтерия», директор,
-- администратор. Выдают и принимают возврат — отдел «Склад», директор,
-- администратор. Видят раздел все. Проверьте расстановку:
-- ---------------------------------------------------------------------
SELECT department,
       COUNT(*) AS people,
       CASE
           WHEN department = 'Бухгалтерия' THEN 'правит наименование и количество'
           WHEN department = 'Склад'       THEN 'выдаёт и принимает возврат'
           ELSE 'только просмотр'
       END AS rights
FROM employees
WHERE COALESCE(status, '') NOT IN ('директор', 'администратор')
GROUP BY department
ORDER BY department;

-- Руководство: у них полный доступ независимо от отдела
SELECT id, last_name, first_name, status
FROM employees
WHERE COALESCE(status, '') IN ('директор', 'администратор');


-- ---------------------------------------------------------------------
-- Проверка, что таблицы созданы
-- ---------------------------------------------------------------------
SELECT table_name, table_rows
FROM information_schema.tables
WHERE table_schema = 'kovcheg' AND table_name LIKE 'equipment%';


-- =====================================================================
--  ШАГ 4. Ответственные за этап
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Ответственные за этап
--
-- Раньше этап вёл целый отдел, и «главного» в нём не было. Теперь при
-- продлении задачи называется ответственный — один или несколько.
-- Отдельная таблица, а не поле в этапе: ответственных бывает больше одного.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS task_stage_leads (
    stage_detail_id INT NOT NULL,
    employee_id     INT NOT NULL,
    PRIMARY KEY (stage_detail_id, employee_id),
    CONSTRAINT fk_stage_lead_stage FOREIGN KEY (stage_detail_id)
        REFERENCES task_stage_details (id) ON DELETE CASCADE,
    CONSTRAINT fk_stage_lead_employee FOREIGN KEY (employee_id)
        REFERENCES employees (id) ON DELETE CASCADE,
    INDEX ix_stage_lead_employee (employee_id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci;


-- ---------------------------------------------------------------------
-- 2. Заполнение для уже существующих этапов
--
-- Записи можно не заполнять: для этапов без назначенных ответственных
-- действует прежнее правило — этапом распоряжается руководитель ведущего
-- отдела, а в отделе без руководителей (договорной) любой его сотрудник.
-- Если хочется зафиксировать ответственных сразу, следующий запрос
-- проставит руководителей отдела каждому незавершённому этапу.
--
-- Раскомментируйте, если это нужно:
--
-- INSERT IGNORE INTO task_stage_leads (stage_detail_id, employee_id)
-- SELECT sd.id, e.id
-- FROM task_stage_details sd
-- JOIN employees e ON e.department = sd.department_name
-- WHERE sd.is_completed = 0
--   AND LOWER(COALESCE(e.status, '')) = 'руководитель отдела';


-- ---------------------------------------------------------------------
-- 3. Проверка: в каких отделах нет руководителей
--
-- В таких отделах ответственным может быть назначен любой сотрудник, и
-- внутри своего этапа он получает полные права. Так задуман договорной
-- отдел; если в списке окажется отдел, где руководитель должен быть, —
-- проставьте ему маркер status = 'руководитель отдела'.
-- ---------------------------------------------------------------------
SELECT department,
       COUNT(*) AS people,
       SUM(LOWER(COALESCE(status, '')) = 'руководитель отдела') AS heads
FROM employees
WHERE department IS NOT NULL AND department <> ''
GROUP BY department
HAVING heads = 0
ORDER BY department;


-- ---------------------------------------------------------------------
-- 4. Журнал задачи
--
-- Изменений в базе не требует: записи ложатся в существующую таблицу
-- task_change_log, просто теперь её пишут не только правки задачи, но и
-- файлы, состав этапов и завершение этапов.
-- ---------------------------------------------------------------------
SELECT field, COUNT(*) AS records
FROM task_change_log
GROUP BY field
ORDER BY records DESC;


-- =====================================================================
--  ШАГ 5. Завершение задачи через подтверждение директора
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Заявки на завершение
--
-- Раньше руководитель отдела отправлял задачу в архив сам. Теперь это
-- заявка: она уходит всем директорам, и завершившим в tasks.who_archive
-- записывается тот директор, который подтвердил, а не тот, кто попросил.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS task_completion_requests (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    task_id      INT          NOT NULL,
    requested_by INT          NULL,
    requested_at DATETIME     NULL,
    status       VARCHAR(16)  NOT NULL DEFAULT 'pending',
    decided_by   INT          NULL,
    decided_at   DATETIME     NULL,
    comment      VARCHAR(500) NULL,
    CONSTRAINT fk_completion_task FOREIGN KEY (task_id)
        REFERENCES tasks (id) ON DELETE CASCADE,
    CONSTRAINT fk_completion_requested_by FOREIGN KEY (requested_by)
        REFERENCES employees (id) ON DELETE SET NULL,
    CONSTRAINT fk_completion_decided_by FOREIGN KEY (decided_by)
        REFERENCES employees (id) ON DELETE SET NULL,
    -- Висящую заявку ищут при каждом открытии задачи
    INDEX ix_completion_task_status (task_id, status)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci;


-- ---------------------------------------------------------------------
-- 2. Директора должны быть отмечены маркером
--
-- Заявка уходит тем, у кого status = 'директор'. Если маркер не проставлен,
-- подтверждать завершение будет некому. Шаг 2 это уже делал — проверяем.
-- ---------------------------------------------------------------------
SELECT id, last_name, first_name, position, status
FROM employees
WHERE LOWER(COALESCE(status, '')) = 'директор';

-- Если список пуст, а директора в штате есть — проставьте маркер:
--   UPDATE employees SET status = 'директор' WHERE id IN (...);


-- ---------------------------------------------------------------------
-- 3. Уже архивные задачи
--
-- Их трогать не нужно: заявки заводятся только для новых завершений.
-- Запрос показывает, кто числится завершившим сейчас.
-- ---------------------------------------------------------------------
SELECT t.id, t.name, t.archived_at,
       CONCAT(COALESCE(e.last_name, ''), ' ', COALESCE(e.first_name, '')) AS who
FROM tasks t
LEFT JOIN employees e ON e.id = t.who_archive
WHERE t.archived_at IS NOT NULL
ORDER BY t.archived_at DESC
LIMIT 20;


-- ---------------------------------------------------------------------
-- Проверка, что таблица создана
-- ---------------------------------------------------------------------
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'kovcheg' AND table_name = 'task_completion_requests';



-- ---------------------------------------------------------------------
-- 6. Снимок позиции оборудования
--
-- Показывать оборудование картинкой нагляднее, чем строкой: на складе
-- одинаковых названий много. Сам файл лежит в хранилище (file_storage),
-- в базе — только путь к нему.
--
-- Раньше здесь стоял подготовленный запрос с переменной. Если соединение
-- с сервером обрывалось между командами, переменная терялась вместе с
-- сеансом — поэтому теперь две обычные команды.
-- ---------------------------------------------------------------------
-- Сначала посмотрите, есть ли столбец. Пустой ответ — выполняйте ALTER ниже.
SHOW COLUMNS FROM equipment_items LIKE 'photo_path';

-- Если столбца нет — добавьте. Если есть, MySQL ответит «Duplicate column
-- name» и ничего не сделает: это не поломка.
ALTER TABLE equipment_items ADD COLUMN photo_path VARCHAR(500) NULL AFTER unit;

-- =====================================================================
--  ИТОГ. Сводная проверка
-- =====================================================================

-- Все новые таблицы на месте?
SELECT table_name,
       CASE table_name
           WHEN 'equipment_items'           THEN 'позиции оборудования'
           WHEN 'equipment_issues'          THEN 'выдачи и возвраты'
           WHEN 'equipment_log'             THEN 'журнал строки оборудования'
           WHEN 'task_stage_leads'          THEN 'ответственные за этап'
           WHEN 'task_completion_requests'  THEN 'заявки на завершение задачи'
       END AS purpose
FROM information_schema.tables
WHERE table_schema = 'kovcheg'
  AND table_name IN ('equipment_items', 'equipment_issues', 'equipment_log',
                     'task_stage_leads', 'task_completion_requests')
ORDER BY table_name;
-- Ожидается пять строк. Если строк меньше — соответствующий шаг не прошёл.


-- ---------------------------------------------------------------------
-- Телефоны с приложением Kovapp (уведомления в приложении для Android)
-- ---------------------------------------------------------------------
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


-- Маркеры прав проставлены? Без них администратор останется видимым
-- в списках, а подтверждать завершение задач будет некому.
SELECT COALESCE(NULLIF(status, ''), '(пусто)') AS marker, COUNT(*) AS people
FROM employees
GROUP BY marker
ORDER BY people DESC;

-- Отделы и их роль в новых разделах
SELECT department,
       COUNT(*) AS people,
       SUM(LOWER(COALESCE(status, '')) = 'руководитель отдела') AS heads,
       CASE department
           WHEN 'Бухгалтерия'      THEN 'правит оборудование и количество'
           WHEN 'Склад'            THEN 'выдаёт оборудование и принимает возврат'
           WHEN 'Договорной отдел' THEN 'без руководителей: ответственным может быть любой'
           ELSE ''
       END AS note
FROM employees
WHERE department IS NOT NULL AND department <> ''
GROUP BY department
ORDER BY department;

-- Готово. Перезапустите сервер.
