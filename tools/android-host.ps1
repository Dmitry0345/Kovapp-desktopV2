# Android Studio на этом компьютере для тестов в Docker: SDK, эмулятор, режим adb.
#   start   — найти SDK, запустить эмулятор (если не запущен) и дождаться загрузки Android
#   network — adb слушает все сетевые интерфейсы (если контейнер не видит его на localhost)
#   stop    — вернуть adb в обычный режим
param([string]$Action = "start", [int]$Slot = 1, [string]$Apk = "", [int]$Port = 18000, [string]$Target = "emulator")
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot

# настройки из .env модуля (ANDROID_SDK, DEVICE_AVD, PROJECT_ANDROID_DIR — все необязательные)
$Cfg = @{}
$EnvFile = Join-Path $Root ".env"
if (Test-Path $EnvFile) {
    foreach ($line in Get-Content $EnvFile -Encoding UTF8) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            $Cfg[$Matches[1]] = $Matches[2].Trim().Trim('"').Trim("'")
        }
    }
}

function Find-Sdk {
    $cands = New-Object System.Collections.Generic.List[string]
    foreach ($v in @($Cfg['ANDROID_SDK'], $env:ANDROID_HOME, $env:ANDROID_SDK_ROOT)) { if ($v) { $cands.Add($v) } }
    # путь, записанный Android Studio в проект
    $proj = $Cfg['PROJECT_ANDROID_DIR']
    if (-not $proj) { $proj = $Cfg['PROJECT_DIR'] }
    if (-not $proj) { $proj = "./project/android" }
    if (-not [System.IO.Path]::IsPathRooted($proj)) { $proj = Join-Path $Root $proj }
    $lp = Join-Path $proj "mobile\local.properties"
    if (Test-Path $lp) {
        foreach ($line in Get-Content $lp) {
            if ($line -match '^\s*sdk\.dir\s*=\s*(.+)$') { $cands.Add(($Matches[1] -replace '\\:', ':' -replace '\\\\', '\')) }
        }
    }
    # путь из настроек Android Studio (Settings → Android SDK)
    $asDirs = Get-ChildItem (Join-Path $env:APPDATA "Google") -Directory -Filter "AndroidStudio*" -ErrorAction SilentlyContinue |
              Sort-Object Name -Descending
    foreach ($d in $asDirs) {
        foreach ($f in @("options\other.xml", "options\android.sdk.path.xml")) {
            $x = Join-Path $d.FullName $f
            if (Test-Path $x) {
                $m = Select-String -Path $x -Pattern 'android\.sdk\.path"\s+value="([^"]+)"' -ErrorAction SilentlyContinue | Select-Object -First 1
                if ($m) { $cands.Add($m.Matches[0].Groups[1].Value) }
            }
        }
    }
    $cands.Add((Join-Path $env:LOCALAPPDATA "Android\Sdk"))
    foreach ($drv in (Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue)) { $cands.Add((Join-Path $drv.Root "Android\Sdk")) }
    foreach ($c in $cands) {
        if ($c -and (Test-Path (Join-Path $c "platform-tools\adb.exe"))) { return $c }
    }
    return $null
}

$Sdk = Find-Sdk
if (-not $Sdk) {
    Write-Host "Не найден Android SDK. Установите Android Studio (SDK ставится при первом запуске)"
    Write-Host "или укажите путь в .env: ANDROID_SDK=F:\Android\Sdk"
    exit 1
}
$Adb = Join-Path $Sdk "platform-tools\adb.exe"
$Emu = Join-Path $Sdk "emulator\emulator.exe"
$PidFile = Join-Path $Root "logs\.adb-network.pid"

function Get-Devices { (& $Adb devices 2>$null) | Where-Object { $_ -match "\sdevice$" } }

# Эмулятор для тестов — на отдельных портах 5600/5601: adb этого ПК (и Android Studio) ищет эмуляторы
# только на 5554-5585, а о себе эмулятор сообщает на свободный порт 5039. Так к нему подключается
# один adb — из контейнера Appium, и никто не рвёт у него связь.
# второй телефон (run.cmd device-pair) — порты 5602/5603
$ConsolePort = 5600 + 2 * ([Math]::Max(1, $Slot) - 1)

function Test-Port([int]$port) {
    $c = New-Object System.Net.Sockets.TcpClient
    try { $c.Connect("127.0.0.1", $port); return $true } catch { return $false } finally { $c.Close() }
}

function Close-TestEmulator([int]$Port = $ConsolePort) {
    if (-not (Test-Port $Port)) { return }
    # вежливо: команда kill в консоль эмулятора (ключ доступа — в профиле пользователя)
    try {
        $token = (Get-Content (Join-Path $env:USERPROFILE ".emulator_console_auth_token") -ErrorAction Stop | Select-Object -First 1)
        $c = New-Object System.Net.Sockets.TcpClient("127.0.0.1", $Port)
        $w = New-Object System.IO.StreamWriter($c.GetStream())
        $w.NewLine = "`r`n"
        $w.AutoFlush = $true
        Start-Sleep -Milliseconds 300
        $w.WriteLine("auth $token")
        Start-Sleep -Milliseconds 300
        $w.WriteLine("kill")
        Start-Sleep -Milliseconds 500
        $c.Close()
    } catch { }
    $deadline = (Get-Date).AddSeconds(20)
    while ((Test-Port $Port) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 1 }
    # не закрылся — завершаем процессы только тестового эмулятора на этих портах
    if (Test-Port $Port) {
        $pattern = "-ports\s+$Port,$($Port + 1)"
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -and $_.CommandLine -match $pattern } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    }
    Write-Host "Эмулятор для тестов закрыт"
}

function Stop-Helpers {
    foreach ($f in @($PidFile, (Join-Path $Root "logs\.adb-relay.pid"))) {
        if (Test-Path $f) {
            Stop-Process -Id ([int](Get-Content $f)) -Force -ErrorAction SilentlyContinue
            Remove-Item $f -Force
        }
    }
}

# настоящий телефон по USB: серийный номер первого разрешившего отладку; нет — подсказка, что включить
function Find-Phone {
    & $Adb start-server 2>$null | Out-Null
    $phones = @(); $unauth = @()
    foreach ($l in (& $Adb devices 2>$null | Select-Object -Skip 1)) {
        $parts = ($l.Trim() -split '\s+')
        if ($parts.Count -lt 2) { continue }
        if ($parts[0] -like 'emulator-*' -or $parts[0] -match ':') { continue }   # эмуляторы и подключения по сети
        if ($parts[1] -eq 'device') { $phones += $parts[0] } elseif ($parts[1] -eq 'unauthorized') { $unauth += $parts[0] }
    }
    if ($phones.Count -gt 0) {
        if ($phones.Count -gt 1) { Write-Host "Подключено несколько телефонов - беру первый: $($phones[0])" }
        return $phones[0]
    }
    if ($unauth.Count -gt 0) {
        Write-Host "Телефон подключён, но отладка не разрешена: на экране телефона отметьте"
        Write-Host "«Всегда разрешать с этого компьютера», нажмите «Разрешить» и запустите команду снова."
    } else {
        Write-Host "Телефон не найден. Проверьте по шагам:"
        Write-Host "  1) кабель передаёт данные, а не только зарядку (при подключении телефон спрашивает режим USB);"
        Write-Host "  2) режим разработчика: Настройки -> О телефоне -> 7 раз нажать «Номер сборки»;"
        Write-Host "  3) «Отладка по USB»: Настройки -> Система -> Для разработчиков;"
        Write-Host "  4) Xiaomi: там же включите «Установка через USB»."
    }
    return $null
}
$PhoneFile = Join-Path $PSScriptRoot "..\logs\phone-serial.txt"

switch ($Action) {
    "start" {
        Write-Host "Android SDK: $Sdk"
        if (Test-Port $ConsolePort) {
            Write-Host "Эмулятор для тестов уже запущен"
            exit 0
        }
        # эмулятор, запущенный из Android Studio на обычных портах, перезапускаем на портах для тестов
        & $Adb start-server 2>$null | Out-Null
        $running = @((& $Adb devices 2>$null) | Where-Object { $_ -match '^emulator-\d+\s+device$' })
        foreach ($line in $running) {
            $serial = ($line -split '\s+')[0]
            Write-Host "Закрываю $serial — запущу эмулятор заново на отдельном порту для тестов..."
            & $Adb -s $serial emu kill 2>$null | Out-Null
        }
        if ($running.Count -gt 0) { Start-Sleep -Seconds 10 }
        # эмулятор: KOV_AVD (задаёт run.cmd device-both) → DEVICE_AVD из .env → первый созданный,
        # кроме эмулятора без сервисов Google (DEVICE_AVD_NOGMS)
        $avd = $env:KOV_AVD
        if (-not $avd) { $avd = $Cfg['DEVICE_AVD'] }
        if ($Slot -ge 2) {
            # второй телефон: другой эмулятор (один и тот же дважды запустить нельзя)
            $avd = $env:KOV_AVD2
            if (-not $avd) { $avd = $Cfg['DEVICE_AVD2'] }
            if (-not $avd) { $avd = $Cfg['DEVICE_AVD_NOGMS'] }
            if (-not $avd) {
                Write-Host "Для второго телефона укажите в .env DEVICE_AVD_NOGMS (или DEVICE_AVD2) — имя второго эмулятора"
                exit 1
            }
        }
        if (-not $avd -and (Test-Path $Emu)) {
            $avd = & $Emu -list-avds 2>$null | Where-Object { $_ -match '^[\w.\-]+$' -and $_ -ne $Cfg['DEVICE_AVD_NOGMS'] } |
                   Select-Object -First 1
        }
        if (-not $avd) {
            Write-Host "Нет эмулятора: создайте его один раз в Android Studio (Device Manager → +) и запустите команду снова"
            exit 1
        }
        # ключ adb этого компьютера должен существовать до запуска эмулятора: эмулятор доверяет ему сразу,
        # и окна «Allow USB debugging?» не будет
        $keyDir = Join-Path $env:USERPROFILE ".android"
        $key = Join-Path $keyDir "adbkey"
        if (-not (Test-Path $key)) {
            New-Item -ItemType Directory -Force -Path $keyDir | Out-Null
            & $Adb keygen $key 2>$null | Out-Null
            Write-Host "Создан ключ adb этого компьютера: $key"
        }
        $memory = $Cfg['DEVICE_MEMORY_MB']
        if (-not $memory) { $memory = "3072" }
        if ($Slot -ge 2) { $memory = $Cfg['DEVICE2_MEMORY_MB']; if (-not $memory) { $memory = "2048" } }
        Write-Host "Запускаю эмулятор $avd на порту $ConsolePort (холодный запуск, память $memory МБ)..."
        $env:ANDROID_ADB_SERVER_PORT = "5039"
        Start-Process -FilePath $Emu -ArgumentList @("-avd", $avd, "-ports", "$ConsolePort,$($ConsolePort + 1)",
            "-no-snapshot", "-no-boot-anim", "-no-audio", "-memory", $memory) | Out-Null
        Remove-Item Env:ANDROID_ADB_SERVER_PORT
        $deadline = (Get-Date).AddMinutes(3)
        while (-not (Test-Port $ConsolePort) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 2 }
        if (-not (Test-Port $ConsolePort)) {
            Write-Host "Эмулятор не запустился за 3 минуты — откройте его в Android Studio вручную, закройте и повторите"
            exit 1
        }
        Write-Host "Эмулятор запущен; загрузку Android дождётся контейнер Appium"
        exit 0
    }
    "relay" {
        # запасной путь: контейнер не видит порт эмулятора — ретранслятор 0.0.0.0:15555 → 127.0.0.1:5555
        $relay = Join-Path $PSScriptRoot "adb-relay.ps1"
        $p = Start-Process -FilePath "powershell" -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$relay`"") -WindowStyle Hidden -PassThru
        New-Item -ItemType Directory -Force -Path (Split-Path $PidFile) | Out-Null
        Set-Content -Path (Join-Path $Root "logs\.adb-relay.pid") -Value $p.Id
        Start-Sleep -Seconds 2
        exit 0
    }
    "network" {
        # сетевой режим: сервер adb слушает все интерфейсы — на время прогона
        & $Adb kill-server 2>$null | Out-Null
        $p = Start-Process -FilePath $Adb -ArgumentList @("-a", "-P", "5037", "nodaemon", "server") -WindowStyle Hidden -PassThru
        New-Item -ItemType Directory -Force -Path (Split-Path $PidFile) | Out-Null
        Set-Content -Path $PidFile -Value $p.Id
        $deadline = (Get-Date).AddSeconds(30)
        while (-not (Get-Devices) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 1 }
        exit 0
    }
    "stop" {
        Stop-Helpers
        if ($Cfg['DEVICE_KEEP_EMULATOR'] -ne '1') { Close-TestEmulator 5600; Close-TestEmulator 5602 }
        exit 0
    }
    "stop-all" {
        Stop-Helpers
        Close-TestEmulator 5600
        Close-TestEmulator 5602
        exit 0
    }
    "phone" {
        # телефон по USB: поставить приложение начисто, связать с сервером этого ПК через кабель, открыть
        $serial = Find-Phone
        if (-not $serial) { exit 1 }
        if (-not (Test-Path $Apk)) { Write-Host "Нет файла приложения: $Apk"; exit 1 }
        $model = (& $Adb -s $serial shell getprop ro.product.model 2>$null | Out-String).Trim()
        Write-Host "Телефон: $model ($serial) - ставлю приложение начисто..."
        $had = (& $Adb -s $serial shell pm path ru.kovcheg.kovapp 2>$null | Out-String).Trim()
        if ($had) {
            # отладочная и рабочая сборки подписаны разными ключами — поверх друг друга не ставятся
            Write-Host "На телефоне уже был Kovapp - он заменяется отладочной сборкой; рабочий потом установите заново."
        }
        & $Adb -s $serial uninstall ru.kovcheg.kovapp 2>$null | Out-Null
        $res = (& $Adb -s $serial install "$Apk" 2>&1 | Out-String)
        if ($LASTEXITCODE -ne 0) { Write-Host "Установка не удалась: $res"; exit 1 }
        # приложение отладочной сборки ходит на localhost:8000 телефона — по кабелю это сервер на этом ПК
        & $Adb -s $serial reverse tcp:8000 tcp:$Port 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { Write-Host "Не удалось связать телефон с сервером (adb reverse)"; exit 1 }
        & $Adb -s $serial shell am start -n ru.kovcheg.kovapp/.MainActivity 2>&1 | Out-Null
        Set-Content -Path $PhoneFile -Value $serial -Encoding ascii
        exit 0
    }
    "phone-stop" {
        if (Test-Path $PhoneFile) {
            $serial = (Get-Content $PhoneFile -Raw).Trim()
            & $Adb -s $serial reverse --remove tcp:8000 2>$null | Out-Null
            Remove-Item $PhoneFile -ErrorAction SilentlyContinue
        }
        exit 0
    }
    "video-log" {
        # журнал сжатия видео (метка KovVideo) — в отдельном окне
        $serial = ""
        if ($Target -eq "phone") {
            if (Test-Path $PhoneFile) { $serial = (Get-Content $PhoneFile -Raw).Trim() }
            if (-not $serial) { $serial = Find-Phone; if (-not $serial) { exit 1 } }
        } else {
            $serial = "127.0.0.1:" + ($ConsolePort + 1)
            & $Adb connect $serial 2>&1 | Out-Null
        }
        # журнал приложения: сжатие видео (KovVideo), консоль страницы (KovappJS: ошибки, обрывы связи) и аварии
        $hint = "Журнал приложения: KovVideo - сжатие видео, KovappJS - консоль страницы, остальное - аварии. Закрыть - крестик окна."
        $cmd = "chcp 65001 >nul & echo $hint & echo. & `"$Adb`" -s $serial logcat -v time -b main -b crash -s KovVideo:I KovappJS:D chromium:E AndroidRuntime:E DEBUG:F libc:F"
        Start-Process cmd.exe -ArgumentList "/k", $cmd
        exit 0
    }
    "login-check" {
        # вход каждой роли на сервер этого ПК — тем же путём, что у браузера
        $pass = $Cfg['FULL_ACCOUNT_PASSWORD']
        if (-not $pass) { $pass = "Autotest-2026!" }
        $roles = Join-Path $PSScriptRoot "..\config\roles.yaml"
        $logins = Select-String -Path $roles -Pattern '^\s+login:\s*(\S+)' | ForEach-Object { $_.Matches[0].Groups[1].Value }
        $adminLogin = $Cfg['FULL_ADMIN_LOGIN']; if (-not $adminLogin) { $adminLogin = "admin" }
        $adminPass = $Cfg['FULL_ADMIN_PASSWORD']; if (-not $adminPass) { $adminPass = "Admin-Autotest-2026" }
        $bad = 0
        foreach ($l in $logins) {
            $p = if ($l -eq "admin") { $adminPass } else { $pass }   # у встроенного администратора свой пароль
            if ($l -eq "admin") { $l = $adminLogin }
            try {
                Invoke-RestMethod -Method Post -Uri "http://localhost:$Port/login" -Body @{ username = $l; password = $p } `
                    -ContentType "application/x-www-form-urlencoded" -TimeoutSec 30 | Out-Null
                Write-Host "  вход есть:   $l"
            } catch {
                $bad++
                $code = $null
                if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
                $why = if ($code -eq 401) { "сервер не принял пароль" } elseif ($code) { "ответ сервера $code" } else { "сервер не отвечает" }
                Write-Host "  НЕ ПУСКАЕТ:  $l - $why"
            }
        }
        Write-Host ""
        Write-Host "Проверялся пароль из .env (FULL_ACCOUNT_PASSWORD, по умолчанию Autotest-2026!): $pass"
        Write-Host "Сервер: http://localhost:$Port"
        if ($bad -gt 0) { exit 1 } else { exit 0 }
    }
    "notify" {
        # проверочное уведомление с ПК: директор -> выбранной роли (по умолчанию монтажнику)
        $pass = $Cfg['FULL_ACCOUNT_PASSWORD']; if (-not $pass) { $pass = "Autotest-2026!" }
        $to = $Target
        if (-not $to -or $to -eq "emulator" -or $to -eq "all") { $to = "at_worker_mont" }
        if ($to -notlike "at_*") { $to = "at_" + $to }
        $base = "http://localhost:$Port"
        try {
            $me = Invoke-RestMethod -Method Post -Uri "$base/login" -Body @{ username = "at_director"; password = $pass } -ContentType "application/x-www-form-urlencoded"
            $him = Invoke-RestMethod -Method Post -Uri "$base/login" -Body @{ username = $to; password = $pass } -ContentType "application/x-www-form-urlencoded"
            $text = "Проверка уведомлений с ПК: " + (Get-Date -Format "HH:mm:ss")
            $json = @{ user_ids = @([int]$him.user_id); message = $text; target_section = "notifications" } | ConvertTo-Json
            Invoke-RestMethod -Method Post -Uri "$base/notifications" -Headers @{ Authorization = ("Bearer " + $me.access_token) } `
                -Body ([Text.Encoding]::UTF8.GetBytes($json)) -ContentType "application/json; charset=utf-8" | Out-Null
            Write-Host "Отправлено уведомление для ${to}: «$text»"
        } catch {
            Write-Host "Не отправилось: $($_.Exception.Message). Запущено ли демо (run.cmd demo / demo phone)?"
            exit 1
        }
        exit 0
    }
    default { Write-Host "Команды: start, relay, network, stop, stop-all, phone, phone-stop, video-log, login-check, notify"; exit 2 }
}
