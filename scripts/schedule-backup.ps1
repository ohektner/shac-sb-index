# Backup trigger for the scheduled GitHub Actions jobs, run from Owen's PC every 15 minutes
# by a Windows scheduled task.
#
# Why: GitHub skips some scheduled runs (on 2026-11-10 it ran 2 of the first 10 slots).
# This checks each job's latest slot and starts the job by hand if GitHub hasn't run it.
# See src/shac/snapshots/backup_trigger.py for the rules, and docs/DECISIONS.md.
#
# It uses the runner copy of this repo (kept on main) and the GitHub CLI's saved login.
# A log of each run is appended to %LOCALAPPDATA%\shac-sb-index\schedule-backup.log.
#
# Register the task once (PowerShell, as Owen):
#   $action = New-ScheduledTaskAction -Execute "powershell.exe" `
#       -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$env:USERPROFILE\shac-sb-index-runner\scripts\schedule-backup.ps1`""
#   $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 15)
#   $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
#   Register-ScheduledTask -TaskName "shac-sb-index schedule backup" -Action $action `
#       -Trigger $trigger -Settings $settings

param(
    [string]$Runner = "$env:USERPROFILE\shac-sb-index-runner",
    [string]$Uv = "$env:USERPROFILE\.local\bin\uv.exe",
    [string]$Gh = "C:\Program Files\GitHub CLI\gh.exe"
)

$LogDir = Join-Path $env:LOCALAPPDATA "shac-sb-index"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir "schedule-backup.log"

function Write-Log([string]$Message) {
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    Add-Content -Path $LogFile -Value "$stamp $Message"
}

$exitCode = 0
try {
    # Keep the runner on the latest main so schedule changes are picked up.
    & git -C $Runner checkout -q main 2>&1 | Out-Null
    & git -C $Runner pull -q --ff-only origin main 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Log "warning: runner pull failed; using current copy" }

    Push-Location $Runner
    try {
        $output = & $Uv run --frozen python -m shac.snapshots.backup_trigger `
            --repo . --gh $Gh 2>&1
        $exitCode = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    Write-Log "exit $exitCode; $($output -join ' | ')"
} catch {
    Write-Log "FAILED: $($_.Exception.Message)"
    $exitCode = 1
}
exit $exitCode
