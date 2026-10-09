# Все варианты проверки и разделы — без Docker: .\run.cmd list
param([string]$Run = '.\run.cmd', [string]$Port = '18000')
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here
$only = '$env:ONLY = "слова"; ' + $Run + ' web   только тесты, в названии которых есть эти слова; убрать: Remove-Item Env:ONLY'
foreach ($line in Get-Content (Join-Path $here 'list.txt') -Encoding UTF8) {
    Write-Host ($line.Replace('{RUN}', $Run).Replace('{PORT}', $Port).Replace('{ONLY}', $only))
}
Write-Host ''
Write-Host 'РАЗДЕЛЫ (для web, android, external и других вариантов)'
foreach ($line in Get-Content (Join-Path $root 'config\sections.yaml') -Encoding UTF8) {
    if ($line -notmatch '^\s*-\s*\{key:\s*([\w-]+),.*?title:\s*"([^"]*)"') { continue }
    $key = $Matches[1]; $title = $Matches[2]; $mark = ''
    if ($line -match 'targets:\s*\[([^\]]*)\]') {
        $t = (($Matches[1] -split ',') | ForEach-Object { $_.Trim() }) -join ','
        if ($t -eq 'apk') { $mark = '  [только мобильная]' }
        elseif ($t -eq 'device') { $mark = '  [только устройство]' }
        elseif ($t -ne 'web,apk,device') { $mark = '  [только сайт]' }
    }
    if ($line -match 'manual:\s*true') { $mark += '  [отдельной командой: ' + $Run + ' ' + $key + ']' }
    Write-Host ('  {0,-17} {1}{2}' -f $key, $title, $mark)
}
