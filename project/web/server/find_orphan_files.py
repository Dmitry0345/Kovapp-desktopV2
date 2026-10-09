import argparse
import csv
import os
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

# ===== окружение =====

APP_ROOT = Path(__file__).resolve().parent.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
os.chdir(APP_ROOT)
load_dotenv(APP_ROOT / ".env")

DERIVED = (".thumb.jpg", ".preview.pdf")


# ===== разбор файлов =====

def is_temporary(name: str) -> bool:
    lowered = name.lower()
    return name.startswith(".") or lowered.startswith("temp_") or lowered.endswith((".uploading", ".deleting"))


def human(size: int) -> str:
    value = float(size)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if value < 1024 or unit == "ГБ":
            return ("%d %s" % (value, unit)) if unit == "Б" else ("%.1f %s" % (value, unit))
        value /= 1024
    return "%d Б" % size


def scan(root: Path, db, chat_db, file_access):
    orphans, temporary, checked = [], [], 0
    verdicts = {}
    derived = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if is_temporary(path.name):
            temporary.append((rel, path.stat().st_size, "временный файл"))
            continue
        if rel.lower().endswith(DERIVED):
            derived.append((rel, path))
            continue
        checked += 1
        owner = file_access.resolve_owner(db, chat_db, rel)
        verdicts[rel] = owner.kind != "none"
        if owner.kind == "none":
            orphans.append((rel, path.stat().st_size, "нет записи в базе"))
    for rel, path in derived:
        source = file_access.source_of(rel)
        alive = verdicts.get(source)
        if alive is None:
            alive = verdicts.get(source[:-3] if source.lower().endswith(".gz") else source + ".gz")
        if alive is None:
            alive = (root / source).is_file() and file_access.resolve_owner(db, chat_db, source).kind != "none"
        if not alive:
            orphans.append((rel, path.stat().st_size, "миниатюра или превью файла без записи"))
    return checked, orphans, temporary


# ===== действия =====

def move_files(root: Path, items, target: Path) -> int:
    moved = 0
    for rel, _, _ in items:
        source = root / rel
        destination = target / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        moved += 1
    return moved


def delete_files(root: Path, items) -> int:
    removed = 0
    for rel, _, _ in items:
        try:
            (root / rel).unlink()
            removed += 1
        except FileNotFoundError:
            continue
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Поиск файлов-сирот: файлов хранилища, запись о которых в базе уже удалена")
    parser.add_argument("--csv", help="сохранить список найденного в CSV-файл")
    parser.add_argument("--move", help="перенести найденные файлы в указанную папку, сохранив пути")
    parser.add_argument("--delete", action="store_true", help="удалить найденные файлы")
    parser.add_argument("--with-temporary", action="store_true",
                        help="включить в перенос и удаление временные файлы прерванных загрузок")
    parser.add_argument("--yes", action="store_true", help="подтверждение переноса или удаления")
    args = parser.parse_args()

    if args.move and args.delete:
        print("Выберите что-то одно: --move или --delete")
        return 2

    from server.database.init_database import SessionLocal
    from server.database.init_database_messenger import SessionLocalChat
    from server.server_app import file_access
    from server.server_app.config import FILE_STORAGE_ROOT

    root = Path(FILE_STORAGE_ROOT).resolve()
    if not root.is_dir():
        print("Папка хранилища не найдена:", root)
        return 2

    db, chat_db = SessionLocal(), SessionLocalChat()
    try:
        checked, orphans, temporary = scan(root, db, chat_db, file_access)
    finally:
        db.close()
        chat_db.close()

    print()
    print("Хранилище:", root)
    print("Проверено файлов:", checked)
    print("Файлов-сирот:", len(orphans), "—", human(sum(s for _, s, _ in orphans)))
    print("Временных файлов:", len(temporary), "—", human(sum(s for _, s, _ in temporary)))
    for rel, size, why in orphans[:200]:
        print("  %-80s %10s  %s" % (rel, human(size), why))
    if len(orphans) > 200:
        print("  … и ещё", len(orphans) - 200, "— полный список сохраните ключом --csv")

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle, delimiter=";")
            writer.writerow(["Путь", "Размер, байт", "Причина"])
            writer.writerows(orphans + temporary)
        print("Список сохранён:", args.csv)

    items = orphans + (temporary if args.with_temporary else [])
    if not (args.move or args.delete) or not items:
        return 0
    if not args.yes:
        print("Для переноса или удаления добавьте ключ --yes")
        return 1
    if args.move:
        print("Перенесено:", move_files(root, items, Path(args.move).resolve()))
    else:
        print("Удалено:", delete_files(root, items))
    return 0


if __name__ == "__main__":
    sys.exit(main())
