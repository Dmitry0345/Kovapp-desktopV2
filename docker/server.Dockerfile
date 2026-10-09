# ===== сервер Kovapp для полного прогона =====
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/Moscow \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# LibreOffice не ставится: документы Office показывают устройства пользователей
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg curl media-types tzdata fonts-dejavu-core fonts-liberation nginx mariadb-client zstd \
 && rm -rf /var/lib/apt/lists/*

COPY server-requirements.txt /opt/autotest/server-requirements.txt
RUN pip install --no-cache-dir -r /opt/autotest/server-requirements.txt

COPY server-entrypoint.sh seed.py mail_sink.py nginx-kovapp.conf /opt/autotest/
RUN sed -i 's/\r$//' /opt/autotest/server-entrypoint.sh && chmod +x /opt/autotest/server-entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/opt/autotest/server-entrypoint.sh"]
