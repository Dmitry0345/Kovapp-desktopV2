# -*- coding: utf-8 -*-
"""Сквозная проверка API версии 2 на живом сервере.

Запуск (с любой машины, где есть python и requests):
    python smoke_v2.py http://83.171.90.230:54545 ЛОГИН ПАРОЛЬ

Скрипт создаёт тестовый проект, кладёт в него картинку, проверяет миниатюру,
скачивание, настройки пользователя и push-конфигурацию, затем всё удаляет.
Учётная запись должна быть директором, администратором или руководителем отдела.
"""

import io
import sys
import time

import requests

try:
    from PIL import Image
except Exception:
    Image = None


def main():
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    host, login, password = sys.argv[1].rstrip("/"), sys.argv[2], sys.argv[3]
    s = requests.Session()
    failures = 0

    def check(name, ok, extra=""):
        nonlocal failures
        print(("  OK   " if ok else "  FAIL ") + name + (f" — {extra}" if extra else ""))
        if not ok:
            failures += 1

    r = s.post(f"{host}/login", data={"username": login, "password": password}, timeout=30)
    check("POST /login", r.status_code == 200, r.text[:200])
    token = r.json()["access_token"]
    user_id = r.json()["user_id"]
    s.headers["Authorization"] = f"Bearer {token}"

    r = s.get(f"{host}/projects/heads", timeout=30)
    check("GET /projects/heads", r.status_code == 200, f"руководителей отделов: {len(r.json()) if r.ok else r.text[:100]}")

    r = s.get(f"{host}/projects/responsible-candidates", timeout=30)
    check("GET /projects/responsible-candidates", r.status_code == 200)
    candidates = r.json() if r.ok else []

    payload = {"name": f"Тестовый проект {int(time.time())}", "organization": "Тест-Организация",
               "deadline": "2030-01-31", "description": "smoke-test"}
    if candidates:
        payload["responsible_id"] = candidates[0]["id"]
    r = s.post(f"{host}/projects", json=payload, timeout=30)
    check("POST /projects", r.status_code == 201, r.text[:200])
    if not r.ok:
        return 1
    project = r.json()
    pid = project["id"]
    check("  can_edit у создателя", project["can_edit"] is True)

    r = s.get(f"{host}/projects", params={"sort": "deadline_desc"}, timeout=30)
    check("GET /projects", r.status_code == 200 and any(p["id"] == pid for p in r.json()))

    r = s.get(f"{host}/projects/organizations", timeout=30)
    check("GET /projects/organizations", r.status_code == 200 and "Тест-Организация" in r.json())

    r = s.put(f"{host}/projects/{pid}", json={"name": payload["name"] + " (изм.)", "deadline": None}, timeout=30)
    check("PUT /projects/{id}", r.status_code == 200 and r.json()["deadline"] is None, r.text[:150])

    r = s.get(f"{host}/projects/{pid}/tasks", params={"state": "all"}, timeout=30)
    check("GET /projects/{id}/tasks", r.status_code == 200 and isinstance(r.json(), list))

    r = s.get(f"{host}/projects/{pid}/photos", timeout=30)
    check("GET /projects/{id}/photos", r.status_code == 200)

    # Задача с привязкой к проекту
    r = s.post(f"{host}/tasks", json={"name": f"Задача проекта {pid}", "project_id": pid,
                                       "deadline": "2030-02-01", "stages": []}, timeout=30)
    check("POST /tasks (project_id)", r.status_code == 201 and r.json().get("project", {}).get("id") == pid, r.text[:200])
    task_id = r.json()["id"] if r.ok else None
    if task_id:
        r = s.get(f"{host}/projects/{pid}/tasks", timeout=30)
        check("  задача видна в проекте", any(t["id"] == task_id for t in r.json()))
        r = s.put(f"{host}/tasks/{task_id}", json={"project_id": None}, timeout=30)
        check("PUT /tasks/{id} снять проект", r.status_code == 200 and r.json().get("project") is None, r.text[:150])

    # Файл раздела
    if Image is not None:
        buf = io.BytesIO()
        Image.new("RGB", (1200, 900), (90, 130, 170)).save(buf, "JPEG", quality=90)
        data = buf.getvalue()
    else:
        data = b"hello smoke test " * 100
    fname = "smoke.jpg" if Image is not None else "smoke.txt"
    r = s.post(f"{host}/projects/{pid}/files", data={"section": "contracts"},
               files=[("files", (fname, data))], timeout=120)
    check("POST /projects/{id}/files", r.status_code == 200 and len(r.json()) == 1, r.text[:200])
    if r.ok:
        f = r.json()[0]
        check("  владелец файла записан", f.get("uploaded_by_id") == user_id)
        r = s.get(f"{host}/projects/{pid}/files", params={"section": "contracts"}, timeout=30)
        check("GET /projects/{id}/files", r.status_code == 200 and any(x["id"] == f["id"] for x in r.json()))
        if Image is not None:
            r = s.get(f"{host}/projects/files/{f['id']}/thumb", timeout=30)
            check("GET /projects/files/{id}/thumb", r.status_code == 200 and r.headers.get("content-type") == "image/jpeg")
            r = s.get(f"{host}/projects/files/{f['id']}/thumb", params={"token": token}, headers={"Authorization": ""}, timeout=30)
            check("  миниатюра по ?token=", r.status_code == 200)
        r = s.get(f"{host}/projects/files/{f['id']}/download", timeout=30)
        check("GET /projects/files/{id}/download", r.status_code == 200 and len(r.content) > 0)
        r = s.delete(f"{host}/projects/files/{f['id']}", timeout=30)
        check("DELETE /projects/files/{id}", r.status_code == 200)

    r = s.put(f"{host}/preferences/projects.list", json={"value": {"sort": "deadline_asc", "head_id": None}}, timeout=30)
    check("PUT /preferences/{key}", r.status_code == 200)
    r = s.get(f"{host}/preferences/projects.list", timeout=30)
    check("GET /preferences/{key}", r.status_code == 200 and r.json()["value"]["sort"] == "deadline_asc")

    r = s.get(f"{host}/push/config", timeout=30)
    check("GET /push/config", r.status_code == 200, f"enabled={r.json().get('enabled') if r.ok else r.text[:80]}")

    if task_id:
        r = s.delete(f"{host}/tasks/{task_id}", timeout=30)
        check("DELETE /tasks/{id}", r.status_code in (200, 403), "403 допустимо: удалять могут не все роли")
    r = s.delete(f"{host}/projects/{pid}", timeout=30)
    check("DELETE /projects/{id}", r.status_code == 200, r.text[:150])

    print("\nИтог:", "все проверки пройдены" if not failures else f"ошибок: {failures}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
