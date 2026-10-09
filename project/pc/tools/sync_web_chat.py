# -*- coding: utf-8 -*-
"""Обновить мессенджер ПК-версии из веб-версии.

Мессенджер на компьютере — те же файлы, что у сайта (chat.js, chat.css,
разметка, эмодзи, стикеры, цвета темы, общие стили страницы и значки). После правки сайта:

    python tools/sync_web_chat.py <папка web_app>

Файлы ПК-версии в web_chat/pc/ (мост к Python и стили страницы) не трогаются.
"""

import hashlib
import json
import os
import shutil
import sys
import time

FILES = ["chat/chat-markup.html", "chat/chat.css", "chat/chat.js", "chat/emoji-index.js", "chat/media-panel.js",
         "css/theme.css", "css/common.css", "css/sections/messenger.css", "js/core/icons.js"]
DIRS = ["stickers"]


def sync(src, dst):
    for rel in FILES:
        full = os.path.join(src, rel)
        if not os.path.isfile(full):
            raise SystemExit("Нет файла веб-версии: " + full)
    manifest = {"source": os.path.basename(os.path.abspath(src)), "synced": time.strftime("%Y-%m-%d %H:%M"), "files": {}}
    for rel in FILES:
        out = os.path.join(dst, rel)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        shutil.copy2(os.path.join(src, rel), out)
        with open(out, "rb") as f:
            manifest["files"][rel] = hashlib.sha256(f.read()).hexdigest()[:16]
    for rel in DIRS:
        target = os.path.join(dst, rel)
        if os.path.isdir(target):
            shutil.rmtree(target)
        shutil.copytree(os.path.join(src, rel), target)
    with open(os.path.join(dst, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    return manifest


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    m = sync(sys.argv[1], os.path.join(here, "web_chat"))
    print("Обновлено файлов: %d (+ стикеры) → web_chat/" % len(m["files"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
