#!/usr/bin/env python3
"""Проверка чистого Java-ядра приложения без Android SDK и Gradle.

Собирает все классы app/src/main/java, которые не зависят от Android
(нет import android./androidx./com.google.), вместе с тестами app/src/test/java
и прогоняет их. В обычной сборке те же тесты запускает `gradlew test`.

    python tools/offline-check/run_jvm_tests.py [ИмяТеста ...]
"""
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MOBILE = os.path.abspath(os.path.join(HERE, '..', '..'))
MAIN = os.path.join(MOBILE, 'app', 'src', 'main', 'java')
TEST = os.path.join(MOBILE, 'app', 'src', 'test', 'java')
OUT = os.path.join(MOBILE, 'build', 'offline-check', 'classes')
PLATFORM = re.compile(r'^\s*import\s+(static\s+)?(android|androidx|com\.google)\.|\bBuildConfig\.|\bR\.drawable', re.M)


def javac_cmd():
    exe = shutil.which('javac')
    return [exe] if exe else ['java', '-m', 'jdk.compiler/com.sun.tools.javac.Main']


def sources(root, pure_only):
    out = []
    for dirpath, _, files in os.walk(root):
        for f in files:
            if not f.endswith('.java'):
                continue
            p = os.path.join(dirpath, f)
            if pure_only and PLATFORM.search(open(p, encoding='utf-8').read()):
                continue
            out.append(p)
    return sorted(out)


def main():
    wanted = set(sys.argv[1:])
    main_src = sources(MAIN, pure_only=True)
    test_src = sources(TEST, pure_only=False)
    shim = sources(os.path.join(HERE, 'junit-shim'), pure_only=False) + [os.path.join(HERE, 'TestRunner.java')]
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)
    cmd = javac_cmd() + ['-encoding', 'UTF-8', '-Xlint:-options', '-source', '8', '-target', '8', '-d', OUT] + main_src + test_src + shim
    print('Компиляция: %d классов ядра, %d тестов' % (len(main_src), len(test_src)))
    r = subprocess.run(cmd)
    if r.returncode:
        sys.exit('Компиляция не удалась')
    classes = []
    for p in test_src:
        rel = os.path.relpath(p, TEST)[:-5].replace(os.sep, '.')
        if rel.endswith('Test') and (not wanted or rel.split('.')[-1] in wanted):
            classes.append(rel)
    fixtures = os.path.join(MOBILE, 'tests', 'fixtures')
    # большие случайные образцы в архив не входят — создаём их (детерминированно, без ffmpeg)
    blob_list = os.path.join(fixtures, 'blobs.json')
    if os.path.isfile(blob_list) and not all(os.path.isfile(os.path.join(fixtures, b)) for b in json.load(open(blob_list))):
        subprocess.check_call([sys.executable, os.path.join(MOBILE, 'tests', 'make_fixtures.py'), '--blobs'])
    # тестовый сервер для сквозной проверки канала уведомлений (DirectChannelTest)
    import socket, time, urllib.request
    sock = socket.socket(); sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]; sock.close()
    mock = subprocess.Popen([sys.executable, os.path.join(MOBILE, 'tests', 'mock', 'mock_server.py'), '--port', str(port)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen('http://127.0.0.1:%d/app/index.html' % port, timeout=1)
                break
            except Exception:
                time.sleep(0.1)
        run = ['java', '-Dkovapp.fixtures=' + fixtures, '-Dkovapp.mock=http://127.0.0.1:%d' % port, '-cp', OUT, 'TestRunner'] + classes
        code = subprocess.run(run).returncode
    finally:
        mock.terminate()
    sys.exit(code)


if __name__ == '__main__':
    main()
