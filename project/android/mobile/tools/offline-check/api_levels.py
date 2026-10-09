"""Проверка уровней API (minSdk 24): новые функции Android должны вызываться
только под проверкой Build.VERSION.SDK_INT или в методе с @TargetApi.
Упрощённый аналог проверки NewApi из Android Lint — работает без SDK."""
import os, re, sys

MIN = 24
SYMBOLS = {
    r'new NotificationChannel\(': 26, r'createNotificationChannels\(': 26, r'new Notification\.Builder\(\w+, \w+\)': 26,
    r'startForegroundService\(': 26, r'FOREGROUND_SERVICE_TYPE_REMOTE_MESSAGING': 34,
    r'startForeground\([^,()]+, [^,()]+\([^()]*\), ': 29,
    r'MediaStore\.Downloads': 29, r'RELATIVE_PATH': 29, r'IS_PENDING': 29,
    r'ImageDecoder\.': 28, r'WEBP_LOSSY': 30, r'isHardwareAccelerated\(': 29,
    r'getSupportedPerformancePoints\(': 29, r'new MediaCodecInfo\.VideoCapabilities\.PerformancePoint\(': 29,
    r'SOC_MANUFACTURER': 31, r'loadThumbnail\(': 29,
    r'WindowInsets\.Type': 30, r'setDecorFitsSystemWindows': 30, r'getInsetsController': 30, r'WindowInsets\.CONSUMED': 30,
    r'getOnBackInvokedDispatcher': 33, r'OnBackInvokedDispatcher\.PRIORITY': 33,
    r'ACTION_APP_NOTIFICATION_SETTINGS': 26, r'EXTRA_APP_PACKAGE': 26, r'SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR': 26,
    r'Manifest\.permission\.POST_NOTIFICATIONS': 33, r'setSystemBarsAppearance': 30,
}
GUARD = re.compile(r'SDK_INT\s*>=\s*(\d+)')
NEG_GUARD = re.compile(r'SDK_INT\s*<\s*(\d+)\)\s*(return|\{\s*[^}]*return)')

def check(path):
    lines = open(path, encoding='utf-8').read().split('\n')
    problems = []
    for i, line in enumerate(lines):
        if line.strip().startswith(('import ', '//', '*')):
            continue
        for pat, api in SYMBOLS.items():
            if api <= MIN or not re.search(pat, line):
                continue
            ok = False
            depth = 0
            # назад до начала метода: ищем охранное условие или @TargetApi
            same = re.search(r'SDK_INT\s*<\s*(\d+)\s*\|\|', line)
            if same and int(same.group(1)) >= api:
                continue
            for j in range(i, -1, -1):
                t = lines[j]
                blk = re.search(r'if \(Build\.VERSION\.SDK_INT < (\d+)\) \{\s*$', t)
                if blk and int(blk.group(1)) >= api and j < i:
                    # многострочный блок «старые версии — выходим»
                    body = []
                    for k in range(j + 1, i):
                        body.append(lines[k])
                        if lines[k].strip() == '}':
                            break
                    if any('return' in b for b in body):
                        ok = True; break
                m = GUARD.search(t)
                if m and int(m.group(1)) >= api:
                    ok = True; break
                m = NEG_GUARD.search(t)
                if m and int(m.group(1)) >= api:
                    ok = True; break
                ta = re.search(r'@TargetApi\((\d+)\)', t)
                if ta and int(ta.group(1)) >= api:
                    ok = True; break
                if re.match(r'\s{4}(public|private|protected|static|@Override|void|[A-Z]\w*<?.*>?\s+\w+\()', t) and j < i and ('(' in t and ')' in t and '{' in t) and not t.strip().startswith(('if', 'for', 'while', 'switch', 'try', 'catch')):
                    # объявление метода без охраны выше
                    prev = lines[j - 1] if j > 0 else ''
                    ta = re.search(r'@TargetApi\((\d+)\)', prev)
                    ok = bool(ta and int(ta.group(1)) >= api)
                    break
            if not ok and 'POST_NOTIFICATIONS' in pat and ('checkSelfPermission' in line or 'shouldShowRequestPermissionRationale' in line):
                # константа встраивается при сборке; вызовы под проверкой ниже
                pass
            if not ok:
                problems.append('%s:%d  API %d без проверки версии: %s' % (os.path.relpath(path), i + 1, api, line.strip()[:110]))
    return problems

if __name__ == '__main__':
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', 'app', 'src')
    found = []
    for base, _, files in os.walk(root):
        if os.sep + 'test' + os.sep in base:
            continue
        for f in files:
            if f.endswith('.java'):
                found += check(os.path.join(base, f))
    print('\n'.join(found) if found else 'уровни API: нарушений не найдено')
    sys.exit(1 if found else 0)
