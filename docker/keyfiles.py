"""Файлы Firebase в папке keys: google_key (ключ сервера) и google-services (для приложения).

Имена — как у вас; расширение .json может быть или не быть. Если имя другое,
файл узнаётся по содержимому.
"""
import json
from pathlib import Path


def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def find(folder: str = "/keys") -> dict:
    """{'server': путь к ключу сервера | None, 'app': путь к google-services | None}"""
    root = Path(folder)
    found = {"server": None, "app": None}
    if not root.is_dir():
        return found
    files = [p for p in sorted(root.iterdir()) if p.is_file() and p.suffix.lower() in ("", ".json")]
    # сначала по имени, затем по содержимому
    for p in files:
        stem = p.stem.lower() if p.suffix else p.name.lower()
        if stem == "google_key" and found["server"] is None:
            found["server"] = str(p)
        if stem == "google-services" and found["app"] is None:
            found["app"] = str(p)
    for p in files:
        data = _load(p)
        if not isinstance(data, dict):
            continue
        if found["server"] is None and data.get("private_key") and data.get("client_email"):
            found["server"] = str(p)
        if found["app"] is None and data.get("project_info"):
            found["app"] = str(p)
    return found


if __name__ == "__main__":
    import sys
    kind = sys.argv[1] if len(sys.argv) > 1 else "server"
    path = find(sys.argv[2] if len(sys.argv) > 2 else "/keys").get(kind)
    if path:
        print(path)
