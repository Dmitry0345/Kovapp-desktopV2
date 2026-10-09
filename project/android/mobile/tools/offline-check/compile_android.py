"""Проверочная компиляция всего Java-кода приложения (обе сборки) против
заглушек Android API — без Android SDK. Ловит ошибки типов, сигнатур,
необработанные исключения и опечатки. Запуск: python3 compile_android.py"""
import os, subprocess, sys, tempfile, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
SRC = os.path.join(ROOT, 'app', 'src')
JAVAC = ['java', '-m', 'jdk.compiler/com.sun.tools.javac.Main']

def sources(*dirs):
    out = []
    for d in dirs:
        for base, _, files in os.walk(d):
            out += [os.path.join(base, f) for f in files if f.endswith('.java')]
    return out

def run(flavor):
    tmp = tempfile.mkdtemp(prefix='kovapp-' + flavor + '-')
    stubs = os.path.join(tmp, 'stubs')
    subprocess.check_call([sys.executable, os.path.join(HERE, 'make_stubs.py'), stubs, flavor], stdout=subprocess.DEVNULL)
    files = sources(os.path.join(SRC, 'main', 'java'), stubs)      # одна сборка: весь код — в main
    lst = os.path.join(tmp, 'files.txt')
    with open(lst, 'w') as f:
        f.write('\n'.join(files))
    cmd = JAVAC + ['-source', '11', '-target', '11', '-encoding', 'UTF-8', '-nowarn', '-Xlint:-options',
                   '-Xmaxerrs', '200', '-d', os.path.join(tmp, 'out'), '@' + lst]
    r = subprocess.run(cmd, capture_output=True, text=True)
    shutil.rmtree(tmp, ignore_errors=True)
    msg = (r.stdout + r.stderr).strip()
    return r.returncode, msg

if __name__ == '__main__':
    ok = True
    for flavor in ('apk',):
        code, msg = run(flavor)
        print('[%s] %s' % (flavor, 'OK' if code == 0 else 'ОШИБКИ'))
        if code != 0:
            ok = False
            print(msg)
    sys.exit(0 if ok else 1)
