#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Строгая проверка XML-ресурсов и манифестов приложения — как при сборке APK.

Разбирает каждый XML-файл по правилам XML (двойной дефис в комментарии,
незакрытые теги, неэкранированные & и <) и проверяет строки на правила
сборщика ресурсов Android (aapt2): апостроф без обратной косой черты,
несколько подстановок %s без номеров.

    python mobile/tools/offline-check/check_resources.py
"""
import glob
import os
import re
import sys
import xml.dom.minidom
import xml.parsers.expat

HERE = os.path.dirname(os.path.abspath(__file__))
MOBILE = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(MOBILE, "app", "src")


def comments_ok(text, path, problems):
    # XML запрещает «--» внутри комментария; expat пропускает это не всегда
    for m in re.finditer(r"<!--(.*?)-->", text, re.S):
        body = m.group(1)
        if "--" in body or body.endswith("-"):
            line = text[:m.start()].count("\n") + 1
            problems.append("%s:%d: в комментарии «--» — XML это запрещает" % (path, line))


def strings_ok(dom, path, problems):
    for node in dom.getElementsByTagName("string"):
        if node.getAttribute("translatable") == "false" and not node.firstChild:
            continue
        raw = "".join(c.toxml() for c in node.childNodes)
        quoted = raw.startswith('"') and raw.endswith('"')
        if not quoted and re.search(r"(?<!\\)'", raw):
            problems.append("%s: строка %s — апостроф без \\ (aapt2 не соберёт)" % (path, node.getAttribute("name")))
        subs = re.findall(r"%(?!\d+\$)[sdf]", raw)
        if len(subs) > 1 and node.getAttribute("formatted") != "false":
            problems.append("%s: строка %s — несколько %%s без номеров (%%1$s, %%2$s)" % (path, node.getAttribute("name")))


def main():
    files = sorted(glob.glob(os.path.join(SRC, "*", "res", "**", "*.xml"), recursive=True)
                   + glob.glob(os.path.join(SRC, "*", "AndroidManifest.xml")))
    problems = []
    for path in files:
        rel = os.path.relpath(path, MOBILE)
        text = open(path, encoding="utf-8").read()
        comments_ok(text, rel, problems)
        try:
            dom = xml.dom.minidom.parseString(text.encode("utf-8"))
        except xml.parsers.expat.ExpatError as e:
            problems.append("%s:%d: XML не разбирается — %s" % (rel, e.lineno, e))
            continue
        if os.path.basename(os.path.dirname(path)).startswith("values"):
            strings_ok(dom, rel, problems)
    for p in problems:
        print("  ОШИБКА " + p)
    print("XML-ресурсы: файлов %d, ошибок %d" % (len(files), len(problems)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
