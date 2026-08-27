# Installs (or removes) the guardian as a Windows scheduled task.
#
# The guardian keeps ARC answering. This keeps the GUARDIAN running -- because a
# supervisor that only lives until the next reboot is not a supervisor, it is a
# thing that was watching until the moment you actually needed it.
#
#   .\install-guardian.ps1            install and start it
#   .\install-guardian.ps1 -Remove    take it away again
#
# Runs at logon rather than at startup, deliberately: at startup it would run as
# SYSTEM, in a different environment, with no access to the user profile the
# Google sign-ins and the Chrome app window live in. ARC is a user's program.

param([switch]$Remove)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$task = "ARC Guardian"

if ($Remove) {
    foreach ($n in @("ARC Guardian", "ARC Guardian (private)")) {
        try {
            Unregister-ScheduledTask -TaskName $n -Confirm:$false
            "Removed '$n'."
        } catch { "'$n' was not installed." }
    }
    "Nothing is watching ARC now."
    Get-Process python -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*guardian.py*" } |
        ForEach-Object { Stop-Process -Id $_.Id -Force }
    return
}

$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { throw "python is not on PATH -- the guardian needs it to run." }

# pythonw, when it exists, so a week of watching does not leave a console window
# sitting on the desktop.
$pyw = Join-Path (Split-Path -Parent $py) "pythonw.exe"
if (Test-Path $pyw) { $py = $pyw }

# One guardian per Bella. The private instance needs its own, and it needs its
# own IDENTITY -- a guardian that relaunches run.py with no arguments would
# bring the private one back as the SHARED one, pointed at the private data
# directory. Worse than the outage it was fixing.
$private = Join-Path (Split-Path -Parent $here) "bella-private"
$jobs = @(
  @{ name = "ARC Guardian";           args = "guardian.py --port 8420" }
)
if (Test-Path $private) {
  $jobs += @{ name = "ARC Guardian (private)"
              args  = "guardian.py --port 8421 --data `"$private`" --variant private" }
}

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
# RestartCount/Interval covers the guardian itself dying -- the one failure it
# cannot report, because reporting is what it stopped doing.
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

foreach ($j in $jobs) {
    $action = New-ScheduledTaskAction -Execute $py -Argument $j.args -WorkingDirectory $here
    Register-ScheduledTask -TaskName $j.name -Action $action -Trigger $trigger `
        -Settings $settings -Description "Keeps ARC answering; logs what it had to do." -Force | Out-Null
    Start-ScheduledTask -TaskName $j.name
}
Start-Sleep -Seconds 4
foreach ($j in $jobs) {
    "Installed '" + $j.name + "' -- state: " + (Get-ScheduledTask -TaskName $j.name).State
}
""
"Each one checks its own ARC every minute. What it had to do is written beside"
"that instance's data, so the private one's log is in bella-private:"
"  guardian.log            (empty means nothing went wrong)"
"  guardian-status.json    (how things are right now)"
