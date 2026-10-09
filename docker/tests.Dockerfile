# ===== тесты: Python, Playwright, два Chromium и Google Chrome =====
FROM mcr.microsoft.com/playwright/python:v1.56.0-noble

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/Moscow \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core tzdata mariadb-client zstd \
 && rm -rf /var/lib/apt/lists/*

COPY tests-requirements.txt /tmp/tests-requirements.txt
RUN pip install --no-cache-dir --break-system-packages -r /tmp/tests-requirements.txt 2>/dev/null \
 || pip install --no-cache-dir -r /tmp/tests-requirements.txt

RUN playwright install --with-deps chrome || echo "Google Chrome не установлен: видео H.264 проверяться не будет"


WORKDIR /autotest
ENTRYPOINT ["python3", "main.py"]
