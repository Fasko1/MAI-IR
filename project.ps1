# Пояснение к файлу: Единая точка запуска проекта из PowerShell.
# Как читать: Параметры → поиск Python → передача флагов → выбор команды. Push-Location гарантирует работу путей относительно корня; finally возвращает исходный каталог.
# Команды запуска и структура проекта: README.md.

param(
    [Parameter(Position=0)]
    [ValidateSet('status','web','tests','rebuild','export','report','collect','upgrade')]
    [string]$Action = 'status',
    [switch]$SkipProcessing,
    [switch]$SkipPdf,
    [switch]$Legacy,
    [switch]$Background,
    [switch]$Status,
    [switch]$Stop,
    [switch]$Watch,
    [switch]$Summary,
    [int]$MaxNew = 0
)
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    $pythonPath = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $pythonPath)) { $pythonPath = (Get-Command python -ErrorAction Stop).Source }
    $env:PYTHONUTF8 = '1'
    $pipelineArguments = @()
    if ($SkipProcessing) { $pipelineArguments += '--skip-processing' }
    if ($SkipPdf) { $pipelineArguments += '--skip-pdf' }
    if ($Legacy) { $pipelineArguments += '--legacy' }
    switch ($Action) {
        'web' { & $pythonPath lab08/web.py }
        'upgrade' {
            $upgradeArguments = @()
            if ($SkipPdf) { $upgradeArguments += '--skip-pdf' }
            & $pythonPath -m tools.project.upgrade @upgradeArguments
        }
        'tests' { & $pythonPath -m tools.project.pipeline --tests-only }
        'rebuild' { & $pythonPath -m tools.project.pipeline @pipelineArguments }
        'export' { & $pythonPath -m tools.project.pipeline --export @pipelineArguments }
        'report' { & $pythonPath -m tools.project.report }
        'status' { & $pythonPath tools/project_status.py }
        'collect' {
            $collectionArguments = @('-MaxNew', $MaxNew)
            if ($Background) { $collectionArguments += '-Background' }
            if ($Status) { $collectionArguments += '-Status' }
            if ($Stop) { $collectionArguments += '-Stop' }
            if ($Watch) { $collectionArguments += '-Watch' }
            if ($Summary) { $collectionArguments += '-Summary' }
            & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'tools/collection.ps1') @collectionArguments
        }
    }
    $result = $LASTEXITCODE
} finally { Pop-Location }
exit $result
