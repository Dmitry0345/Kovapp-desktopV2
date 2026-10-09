#!/bin/sh
# ===== настройки MariaDB по мощности машины: запускается перед каждым стартом базы =====
set -eu
OUT="${KOVAPP_OUT:-/etc/mysql/mariadb.conf.d/61-kovapp-auto.cnf}"
CORES="${KOVAPP_CORES:-$(nproc)}"
RAM_MB="${KOVAPP_RAM_MB:-$(awk '/^MemTotal:/ {print int($2 / 1024)}' /proc/meminfo)}"
SAFETY="${KOVAPP_SAFETY:-0.8}"
POOL_MIN="${KOVAPP_DB_POOL_MIN:-100}"

eval "$(awk -v c="$CORES" -v r="$RAM_MB" -v s="$SAFETY" -v pmin="$POOL_MIN" 'BEGIN {
    reserve = r * 0.2; if (reserve < 768) reserve = 768; if (reserve > 2048) reserve = 2048
    usable = r - reserve; if (usable < 256) usable = 256
    t = c * 32; if (usable / 4 < t) t = usable / 4; threads = int(s * t); if (threads < 16) threads = 16
    h = c * 4; if (usable / 32 < h) h = usable / 32; heavy = int(s * h); if (heavy < 2) heavy = 2
    pool = threads + heavy + 16; if (pool < pmin) pool = pmin
    over = int(pool / 5); if (over < 20) over = 20
    slots = int((usable - 400) / 200); if (slots < 2) slots = 2
    readers = slots - 1; if (readers > c) readers = c; if (readers < 1) readers = 1
    uploaders = slots - readers; if (uploaders > 2) uploaders = 2; if (uploaders < 1) uploaders = 1
    maxconn = 2 * (pool + over) + (readers + uploaders) * 2 * 50 + 2 * 40 + 40
    bp = r * 0.15; if (bp > r * 0.5) bp = r * 0.5; bp = int(bp / 128) * 128; if (bp < 128) bp = 128
    lg = int(bp / 4); if (lg < 48) lg = 48; if (lg > 1024) lg = 1024
    cache = 8 + c * 8; if (cache > 100) cache = 100
    tables = r; if (tables < 1024) tables = 1024; if (tables > 8000) tables = 8000
    tmp = int(r * 0.01); if (tmp < 16) tmp = 16; if (tmp > 64) tmp = 64
    printf "BP=%d\nLOG=%d\nMAXCONN=%d\nCACHE=%d\nTABLES=%d\nTMP=%d\nPOOL=%d\nOVER=%d\n", bp, lg, maxconn, cache, tables, int(tmp), pool, over
}')"

cat > "$OUT" <<CNF
# ===== создано kovapp-db-autotune.sh: ядер $CORES, памяти $RAM_MB МБ; не правьте вручную =====
[mysqld]
innodb_buffer_pool_size = ${BP}M
innodb_log_file_size = ${LOG}M
max_connections = $MAXCONN
thread_handling = pool-of-threads
thread_cache_size = $CACHE
table_open_cache = $TABLES
tmp_table_size = ${TMP}M
max_heap_table_size = ${TMP}M
CNF

if [ "${1:-}" = "--apply-now" ]; then
    mariadb -e "SET GLOBAL innodb_buffer_pool_size = ${BP} * 1024 * 1024; SET GLOBAL max_connections = $MAXCONN;
        SET GLOBAL thread_cache_size = $CACHE; SET GLOBAL table_open_cache = $TABLES;
        SET GLOBAL tmp_table_size = ${TMP} * 1024 * 1024; SET GLOBAL max_heap_table_size = ${TMP} * 1024 * 1024;"
fi
echo "kovapp-db-autotune: ядер $CORES, памяти $RAM_MB МБ → кэш ${BP}M, соединений $MAXCONN (пул API $POOL + $OVER, процессы чтения и приёма загрузок по 40 + 10 и переписка 30 + 10 на каждую из двух баз)"
