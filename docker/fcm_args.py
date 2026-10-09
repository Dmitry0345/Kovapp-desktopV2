"""Параметры сборки из google-services.json: -Pkovapp.fcm…=… (пусто, если файла нет)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

PACKAGE = "ru.kovcheg.kovapp"


def main(folder: str) -> int:
    import keyfiles
    path = keyfiles.find(folder).get("app")
    if not path:
        return 0
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    info = data.get("project_info") or {}
    clients = data.get("client") or []
    client = next((c for c in clients if (c.get("client_info", {}).get("android_client_info", {})
                                          .get("package_name") == PACKAGE)), clients[0] if clients else {})
    values = {
        "fcmProjectId": info.get("project_id", ""),
        "fcmSenderId": info.get("project_number", ""),
        "fcmAppId": client.get("client_info", {}).get("mobilesdk_app_id", ""),
        "fcmApiKey": ((client.get("api_key") or [{}])[0]).get("current_key", ""),
    }
    if all(values.values()):
        print(" ".join("-Pkovapp.%s=%s" % kv for kv in values.items()))
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "/keys"))
