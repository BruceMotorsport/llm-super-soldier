# Super-Soldier watchdog - restarts the stack if a port dies.
# Invoked every 5 minutes by the SuperSoldier-Watchdog scheduled task.
# Exits quietly (0) when everything is healthy, so it costs nothing.
$ErrorActionPreference = "Continue"
Set-Location -LiteralPath $PSScriptRoot

function Test-Port($p) {
    return ($null -ne (Get-NetTCPConnection -LocalPort $p -State Listen `
                       -ErrorAction SilentlyContinue | Select-Object -First 1))
}

$dead = @(8082, 8085, 8086) | Where-Object { -not (Test-Port $_) }

if ($dead.Count -eq 0) {
    exit 0     # all healthy - nothing to do
}

$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
if (-not (Test-Path "logs")) { New-Item -ItemType Directory -Force -Path "logs" | Out-Null }
Add-Content -LiteralPath "logs\watchdog.txt" -Value "$stamp ports down: $($dead -join ',') - restarting"

# Stop whatever is half-alive so the ports are free
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'llm_super_soldier|or_server|oc_server' } |
    ForEach-Object {
        Add-Content -LiteralPath "logs\watchdog.txt" -Value "  stopping pid $($_.ProcessId)"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
Start-Sleep -Seconds 3

# Hand over to the rebuild script - it knows how to start and verify
$py = $null
foreach ($c in @("python", "python3")) {
    if (Get-Command $c -ErrorAction SilentlyContinue) { $py = $c; break }
}
if ($py) {
    & $py "$PSScriptRoot\rebuild.py" --no-autostart 2>&1 |
        Out-File -FilePath "logs\watchdog.txt" -Append -Encoding utf8
    Add-Content -LiteralPath "logs\watchdog.txt" -Value "$stamp rebuild issued"
} else {
    Add-Content -LiteralPath "logs\watchdog.txt" -Value "$stamp ERROR: no python on PATH"
}
