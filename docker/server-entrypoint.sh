#!/bin/sh
set -e

# ===== копия проекта =====
SOURCE=/project
if [ ! -f "$SOURCE/server/server.py" ]; then
  for candidate in /project/*/ /project/*/*/; do
    if [ -f "${candidate}server/server.py" ]; then SOURCE="${candidate%/}"; break; fi
  done
fi
if [ ! -f "$SOURCE/server/server.py" ]; then
  echo "В папке веб-версии не найден server/server.py — положите проект Kovapp в project/web или укажите PROJECT_WEB_DIR в .env"
  exit 2
fi
echo "Проект: $SOURCE"
rm -rf /opt/kovcheg
mkdir -p /opt/kovcheg /shared/mail /shared/server-logs /shared/upload-sessions /shared/push-ca /shared/backups
cp -a "$SOURCE"/. /opt/kovcheg/
rm -rf /opt/kovcheg/.git /opt/kovcheg/file_storage /opt/kovcheg/logs
mkdir -p /opt/kovcheg/file_storage /opt/kovcheg/logs
rm -f /shared/mail/*.eml /shared/server-logs/* 2>/dev/null || true
rm -rf /shared/upload-sessions/* 2>/dev/null || true
python -c "import certifi, shutil; shutil.copy(certifi.where(), '/shared/push-ca/bundle.pem')"
export REQUESTS_CA_BUNDLE=/shared/push-ca/bundle.pem
if [ -f /opt/kovcheg/server/requirements-v2.txt ]; then
  pip install --no-cache-dir -q -r /opt/kovcheg/server/requirements-v2.txt || echo "requirements-v2.txt не доустановлен — работаем с тем, что есть"
fi

# ===== режим сервера: split — API и переписка в двух процессах за nginx, как на рабочем сервере =====
KOV_ROLE="${FULL_SERVER_ROLE:-split}"
if [ "$KOV_ROLE" = "split" ]; then
  KOV_BIND=127.0.0.1; KOV_PORT=8002; KOV_GZIP=0
else
  KOV_ROLE=all; KOV_BIND=0.0.0.0; KOV_PORT=8000; KOV_GZIP=1
fi

# ===== настройки сервера =====
{
  if [ -f /opt/autotest/config/server.env ]; then
    tr -d '\r' < /opt/autotest/config/server.env
    echo
  fi
  cat <<ENVEOF
JWT_SECRET_KEY=${AUTOTEST_JWT_SECRET:-autotest-jwt-secret-change-me-0123456789abcdef}
SERVER_BIND_HOST=${KOV_BIND}
SERVER_PORT=${KOV_PORT}
KOVAPP_ROLE=${KOV_ROLE}
RT_PORT=8001
APP_GZIP=${KOV_GZIP}
ADMIN_FALLBACK_ENABLED=0
DB_TYPE=mysql
DB_DRIVER=pymysql
DB_USER=${DB_USER:-root}
DB_PASSWORD=${DB_PASSWORD}
DB_HOST=${DB_HOST:-db}
DB_PORT=${DB_PORT:-3306}
DB_NAME=kovcheg
INIT_ADMIN_LOGIN=${FULL_ADMIN_LOGIN:-admin}
INIT_ADMIN_PASSWORD=${FULL_ADMIN_PASSWORD}
MAIL_USERNAME=autotest
MAIL_PASSWORD=autotest
MAIL_FROM=noreply@kovapp-autotest.ru
MAIL_SERVER=127.0.0.1
MAIL_PORT=1025
MAIL_STARTTLS=False
MAIL_SSL_TLS=False
RESET_REQUEST_COOLDOWN_SEC=0
BACKUP_ENABLED=1
BACKUP_ROOT=/shared/backups
BACKUP_HOUR=4
BACKUP_AUTO_TUNE_DURABILITY=0
BINLOG_STREAM_ENABLED=0
DB_COMPRESS_DELAY_S=5
WATCHDOG_ENABLED=0
LOG_DIR=/shared/server-logs
WEB_APP_DIR=/opt/kovcheg/web_app
FILE_STORAGE_ROOT=/opt/kovcheg/file_storage
UPLOAD_SESSIONS_DIR=/shared/upload-sessions
PROJECT_ACCESS_MODE=${FULL_PROJECT_ACCESS_MODE:-open}
FILE_ACCESS_STRICT=${FULL_FILE_ACCESS_STRICT:-1}
FILE_ACCESS_CACHE_SECONDS=60
ENVEOF
  # доставка через Google: ключ сервисного аккаунта Firebase из папки keys (если он там есть)
  # сначала файл с именем google_key (с расширением .json или без), иначе — любой ключ по содержимому
  for key in /keys/google_key.json /keys/google_key /keys/*.json /keys/*; do
    if [ -f "$key" ] && grep -q '"private_key"' "$key" && grep -q '"client_email"' "$key"; then
      echo "FCM_SERVICE_ACCOUNT_FILE=$key"
      break
    fi
  done
} > /opt/kovcheg/.env

# ===== почта, база, сервер =====
python /opt/autotest/mail_sink.py &
python /opt/autotest/seed.py
cd /opt/kovcheg
if [ "$KOV_ROLE" = "split" ]; then
  rm -f /etc/nginx/sites-enabled/default
  cp /opt/autotest/nginx-kovapp.conf /etc/nginx/conf.d/kovapp.conf
  nginx
fi
exec python server/server.py
