# Daily Torvik snapshot, run from Owen's PC by a Windows scheduled task.
#
# Why here and not GitHub Actions: barttorvik.com answers GitHub's servers with a browser
# check (HTTP 403), but serves a normal home connection fine. See docs/DECISIONS.md.
#
# What it does:
# 1. Updates the runner copy of this repo (kept on main, used only by this task) and the
#    local copy of the private data repo.
# 2. Runs the same snapshot job the Actions workflow would run.
# 3. Pushes the raw files to the private repo and the status rows to this repo's main.
#
# A log of each run is appended to %LOCALAPPDATA%\shac-sb-index\torvik-task.log.

param(
    [string]$Runner = "$env:USERPROFILE\shac-sb-index-runner",
    [string]$DataRepo = "$env:USERPROFILE\shac-sb-index-data",
    [string]$Uv = "$env:USERPROFILE\.local\bin\uv.exe"
)

$LogDir = Join-Path $env:LOCALAPPDATA "shac-sb-index"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir "torvik-task.log"

function Write-Log([string]$Message) {
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    Add-Content -Path $LogFile -Value "$stamp $Message"
}

# Run git and stop the script if it fails.
function Invoke-Git([string]$Repo, [string[]]$GitArgs) {
    $output = & git -C $Repo @GitArgs 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "git $($GitArgs -join ' ') failed in ${Repo}: $output"
    }
    return $output
}

# Same, but try up to 5 times. Pulls can collide with the 15-minute backup task or a status
# push touching the same ref ("cannot lock ref"); that clears itself within seconds.
function Invoke-GitWithRetry([string]$Repo, [string[]]$GitArgs) {
    for ($attempt = 1; $attempt -le 5; $attempt++) {
        $output = & git -C $Repo @GitArgs 2>&1
        if ($LASTEXITCODE -eq 0) { return $output }
        if ($attempt -eq 5) {
            throw "git $($GitArgs -join ' ') failed in ${Repo} after 5 tries: $output"
        }
        Start-Sleep -Seconds (5 * $attempt)
    }
}

# Push, pulling and rebasing between tries in case someone else pushed first.
function Push-WithRetry([string]$Repo) {
    for ($attempt = 1; $attempt -le 5; $attempt++) {
        & git -C $Repo push -q origin HEAD:main 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { return }
        Start-Sleep -Seconds (5 * $attempt)
        & git -C $Repo pull -q --rebase origin main 2>&1 | Out-Null
    }
    throw "push failed after 5 tries in $Repo"
}

$exitCode = 0
try {
    Write-Log "start"
    Invoke-Git $Runner @("checkout", "-q", "main") | Out-Null
    Invoke-GitWithRetry $Runner @("pull", "-q", "--ff-only", "origin", "main") | Out-Null
    Invoke-GitWithRetry $DataRepo @("pull", "-q", "--rebase", "origin", "main") | Out-Null

    Push-Location $Runner
    try {
        $jobOutput = & $Uv run --frozen python -m shac.snapshots.torvik `
            --data-dir $DataRepo --log data/status/torvik_snapshot_log.csv 2>&1
        $jobExit = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    Write-Log "job exit $jobExit; $($jobOutput -join ' | ')"
    if ($jobExit -ne 0) { $exitCode = 1 }

    # Raw files to the private repo (only if the job saved something).
    Invoke-Git $DataRepo @("add", "torvik") | Out-Null
    & git -C $DataRepo diff --cached --quiet
    if ($LASTEXITCODE -ne 0) {
        $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
        Invoke-Git $DataRepo @("commit", "-q", "-m", "torvik snapshot $stamp (local run)") | Out-Null
        Push-WithRetry $DataRepo
        Write-Log "data repo pushed"
    }

    # Status rows to the public repo.
    Invoke-Git $Runner @("add", "data/status/torvik_snapshot_log.csv") | Out-Null
    & git -C $Runner diff --cached --quiet
    if ($LASTEXITCODE -ne 0) {
        $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
        Invoke-Git $Runner @("commit", "-q", "-m", "status: torvik snapshot $stamp (local run)") | Out-Null
        Push-WithRetry $Runner
        Write-Log "status log pushed"
    }
} catch {
    Write-Log "FAILED: $($_.Exception.Message)"
    $exitCode = 1
}
Write-Log "end (exit $exitCode)"
exit $exitCode
