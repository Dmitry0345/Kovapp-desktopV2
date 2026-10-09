"""Отправка частями (в том числе во время сжатия видео): надёжность и защита — на настоящем ChunkStore."""
import hashlib
import os
import tempfile
import time
import zlib

from server.server_app.chunked import ChunkStore, ChunkError, LEAF

results = []


def check(name, ok, extra=""):
    results.append(ok)
    print(("  ok   " if ok else "  FAIL ") + name + (("  — %s" % extra) if extra and not ok else ""))


def err(fn):
    try:
        fn()
    except ChunkError as e:
        return e.status
    return None


def tree_of(data):
    leaves = hashlib.sha256()
    for at in range(0, len(data), LEAF):
        leaves.update(hashlib.sha256(data[at:at + LEAF]).digest())
    leaves.update(len(data).to_bytes(8, "big"))
    return leaves.hexdigest()


def main():
    root = tempfile.mkdtemp(prefix="chunks-")
    st = ChunkStore(root, max_total=12 * 1024 * 1024, chunk_max=1024 * 1024, ttl=3600, max_active=3)
    data = os.urandom(2 * 1024 * 1024 + 12345)
    parts = [data[i:i + 1024 * 1024] for i in range(0, len(data), 1024 * 1024)]

    s = st.create(1, "video.mp4", None, "video")            # потоковая сессия: размер ещё неизвестен
    sid = s["id"]
    check("номер сессии случайный, 32 шестнадцатеричных знака", len(sid) == 32 and all(c in "0123456789abcdef" for c in sid))
    off = 0
    for p in parts:
        r = st.append(1, sid, off, p, zlib.crc32(p) & 0xFFFFFFFF)
        off += len(p)
    check("части по порядку с CRC приняты", r["received"] == len(data), r)
    r = st.append(1, sid, 0, parts[0], zlib.crc32(parts[0]) & 0xFFFFFFFF)
    check("повтор уже принятой части ничего не портит", r["received"] == len(data), r)
    check("пропуск части отклоняется (409)", err(lambda: st.append(1, sid, len(data) + 10, b"x")) == 409)
    check("отрицательное смещение отклоняется (400)", err(lambda: st.append(1, sid, -5, b"abc")) == 400)
    check("испорченная часть отклоняется по CRC (400)", err(lambda: st.append(1, sid, len(data), b"abc", 12345)) == 400)
    check("слишком большая часть отклоняется (413)", err(lambda: st.append(1, sid, len(data), b"x" * (1024 * 1024 + 1))) == 413)
    check("чужая сессия не видна (404)", err(lambda: st.status(2, sid)) == 404 and err(lambda: st.append(2, sid, len(data), b"x")) == 404
          and err(lambda: st.complete(2, sid, len(data))) == 404)
    check("обход папок через номер сессии невозможен (404)", err(lambda: st.status(1, "../../etc")) == 404 and err(lambda: st.status(1, "0" * 32)) == 404)
    check("завершение с неверным размером отклоняется (409)", err(lambda: st.complete(1, sid, len(data) + 1)) == 409)
    done = st.complete(1, sid, len(data))
    check("при завершении сервер даёт дерево хешей и SHA-256 ровно присланного", done.get("tree") == tree_of(data)
          and done.get("sha256") == hashlib.sha256(data).hexdigest())
    check("после завершения дописать нельзя (409)", err(lambda: st.append(1, sid, len(data), b"x")) == 409)
    got = st.take(1, sid)
    check("файл выдаётся для прикрепления", got is not None)
    check("второй раз тот же файл не выдаётся (409)", err(lambda: st.take(1, sid)) == 409)

    s2 = st.create(1, "a.bin", 100)
    check("данных больше заявленного — отклоняется (400)", err(lambda: st.append(1, s2["id"], 0, b"x" * 150)) == 400)
    st.abort(1, s2["id"])
    check("отменённая сессия удаляется", err(lambda: st.status(1, s2["id"])) == 404)

    big = st.create(1, "big.bin", None)
    chunk = b"z" * (1024 * 1024)
    off = 0
    try:
        for _ in range(13):
            st.append(1, big["id"], off, chunk)
            off += len(chunk)
        over = None
    except ChunkError as e:
        over = e.status
    check("общий размер ограничен (413)", over == 413, over)
    st.abort(1, big["id"])

    opened = [st.create(1, "p%d" % i, None)["id"] for i in range(2)]
    check("незавершённых загрузок не больше заданного (429)", err(lambda: [st.create(1, "x%d" % i, None) for i in range(3)]) == 429)
    check("у другого пользователя свой предел", st.create(2, "y", None)["id"] is not None)

    st.ttl = 0.01
    time.sleep(0.05)
    removed = st.cleanup()
    check("брошенные сессии убираются по сроку", removed >= 1 and all(err(lambda o=o: st.status(1, o)) == 404 for o in opened), removed)

    bad = results.count(False)
    print("Итого: %d проверок, ошибок: %d" % (len(results), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
