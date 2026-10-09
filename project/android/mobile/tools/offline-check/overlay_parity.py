"""Надстройка в APK (Java, web/Overlay.java) и на ПК (tools/devserver.py)
должна давать побайтно одинаковый результат для всех страниц и правок."""
import json, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
MOBILE = os.path.abspath(os.path.join(HERE, '..', '..'))
ROOT = os.path.dirname(MOBILE)
WEB = os.path.join(ROOT, 'web_app')
OVERLAY = os.path.join(MOBILE, 'overlay')
JAVAC = ['java', '-m', 'jdk.compiler/com.sun.tools.javac.Main']
sys.path.insert(0, os.path.join(MOBILE, 'tools'))
import devserver  # noqa: E402

def main():
    targets = []
    for base, _, files in os.walk(WEB):
        for f in files:
            if f.endswith('.html'):
                targets.append(os.path.relpath(os.path.join(base, f), WEB).replace(os.sep, '/'))
    patches = json.load(open(os.path.join(OVERLAY, 'web-patches.json'), encoding='utf-8'))['patches']
    targets += sorted({p['file'] for p in patches})
    tmp = tempfile.mkdtemp()
    core = os.path.join(MOBILE, 'app', 'src', 'main', 'java', 'ru', 'kovcheg', 'kovapp')
    srcs = [os.path.join(core, 'web', 'Overlay.java'), os.path.join(core, 'core', 'Json.java'), os.path.join(HERE, 'parity', 'OverlayDump.java')]
    subprocess.check_call(JAVAC + ['-encoding', 'UTF-8', '-d', os.path.join(tmp, 'cls')] + srcs)
    out = subprocess.run(['java', '-cp', os.path.join(tmp, 'cls'), 'OverlayDump', OVERLAY, WEB, os.path.join(tmp, 'java')] + targets,
                         capture_output=True, text=True, check=True).stdout
    ov = devserver.load_overlay(bridge=False, patches=True)
    same = 0
    diff = []
    for path in targets:
        py = ov.transform(path, open(os.path.join(WEB, path), 'rb').read())
        jv = open(os.path.join(tmp, 'java', path), 'rb').read()
        if py == jv:
            same += 1
        else:
            diff.append(path)
        orig = open(os.path.join(WEB, path), 'rb').read()
        if path.endswith('.html'):
            # вне вставленных тегов страница не меняется
            stripped = py.decode('utf-8')
            for s in ov.head_scripts + ov.tail_scripts:
                stripped = stripped.replace('<script src="%s"></script>' % s, '')
            ok = stripped.encode('utf-8') == orig or path in {p['file'] for p in patches}
            if not ok:
                diff.append(path + ' (изменено вне вставки)')
    shutil.rmtree(tmp, ignore_errors=True)
    warn = [l for l in out.splitlines() if l.startswith('WARN')] + ['WARN ' + w for w in ov.warnings]
    print('Надстройка: %d файлов, совпадают побайтно: %d; расхождений: %d; предупреждений: %d' % (len(targets), same, len(diff), len(warn)))
    for d in diff + warn:
        print('  ', d)
    return 1 if diff or warn else 0

if __name__ == '__main__':
    sys.exit(main())
