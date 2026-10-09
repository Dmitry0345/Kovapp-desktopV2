# ===== проверка ключей Firebase: код отправки сервера + cryptography (без базы и сервера) =====
FROM python:3.12-slim
RUN pip install --no-cache-dir "cryptography>=42"
COPY keycheck.py keyfiles.py /opt/kovapp-check/
ENTRYPOINT ["python", "/opt/kovapp-check/keycheck.py"]
