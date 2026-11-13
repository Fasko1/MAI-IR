# Пояснение к файлу: Оболочка управления расширенным сбором корпуса.
# Как читать: Статус/сводка читают состояние; Stop создаёт сигнал остановки; Background запускает скрытый процесс; обычный режим ведёт сбор в терминале.
# Команды запуска и структура проекта: README.md.

param(
    [switch]$Background,
    [switch]$Status,
    [switch]$Stop,
    [switch]$Watch,
    [switch]$Summary,
    [int]$MaxNew = 0
)
$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
$statePath = Join-Path $projectPath 'data\expansion'
$pythonPath = Join-Path $projectPath '.venv\Scripts\python.exe'
$collectorPath = Join-Path $projectPath 'tools\collect_corpus.py'
$stopPath = Join-Path $statePath 'STOP'
if ($Watch) {
    $collectorLog = Get-ChildItem -LiteralPath $statePath -Filter 'collector-*.out.log' | Sort-Object Name -Descending | Select-Object -First 1
    if (-not $collectorLog) { throw 'Collector log not found.' }
    Get-Content -Encoding UTF8 -LiteralPath $collectorLog.FullName -Tail 25 -Wait
    return
}
if ($Status -or $Summary) {
    $statusPath = Join-Path $statePath 'status.json'
    if (Test-Path -LiteralPath $statusPath) {
        if ($Summary) {
            $corpusProgress = Get-Content -Raw -Encoding UTF8 -LiteralPath $statusPath | ConvertFrom-Json
            Write-Output ("{0}; updated: {1}; unique text: {2:N3} GB" -f $corpusProgress.state,$corpusProgress.updated_at,($corpusProgress.unique_text_bytes / 1000000000))
            if ($corpusProgress.source_metrics | Where-Object { $null -ne $_.recent_mb_per_hour }) {
                $recentRate = ($corpusProgress.source_metrics | Measure-Object recent_mb_per_hour -Sum).Sum
                Write-Output ("Recent collection rate: {0:N1} MB/hour (up to 5 minutes)" -f $recentRate)
            }
            $corpusProgress.sources | Sort-Object text_bytes -Descending | Select-Object source,unique_documents,@{Name='Text_MB';Expression={[math]::Round($_.text_bytes / 1000000,2)}} | Format-Table -AutoSize
        } else { Get-Content -Raw -Encoding UTF8 -LiteralPath $statusPath }
    } else {
        Write-Output 'No status yet. The first run hashes existing documents before reporting.'
    }
    return
}
New-Item -ItemType Directory -Path $statePath -Force | Out-Null
if ($Stop) {
    New-Item -ItemType File -Path $stopPath -Force | Out-Null
    Write-Output 'Stop requested. The collector will finish its current cycle and save progress.'
    return
}
if ($MaxNew -lt 0) { throw 'MaxNew must be nonnegative.' }
$collectorLockPath = Join-Path $statePath 'collector.lock'
if (Test-Path -LiteralPath $collectorLockPath) {
    $collectorLockHandle = $null
    try {
        $collectorLockHandle = [System.IO.File]::Open($collectorLockPath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::ReadWrite)
        $collectorLockHandle.Lock(0, 1)
        $collectorLockHandle.Unlock(0, 1)
    } catch [System.IO.IOException] {
        Write-Output 'Collector or corpus maintenance is already running. Use -Watch or -Summary.'
        return
    } finally {
        if ($null -ne $collectorLockHandle) { $collectorLockHandle.Dispose() }
    }
}
if (Test-Path -LiteralPath $stopPath) { Remove-Item -LiteralPath $stopPath }
if ($Background) {
    $logStamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    $outPath = Join-Path $statePath "collector-$logStamp.out.log"
    $errPath = Join-Path $statePath "collector-$logStamp.err.log"
    $collectorProcess = Start-Process -FilePath $pythonPath -ArgumentList @('-u', ('"' + $collectorPath + '"'), '--max-new', $MaxNew) -WorkingDirectory $projectPath -WindowStyle Hidden -RedirectStandardOutput $outPath -RedirectStandardError $errPath -PassThru
    Write-Output "Collector launched, PID $($collectorProcess.Id). Check status/logs to confirm it is running."
    Write-Output $outPath
    Write-Output $errPath
} else {
    & $pythonPath -u $collectorPath --max-new $MaxNew
    exit $LASTEXITCODE
}
