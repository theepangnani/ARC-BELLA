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
    try {
        Unregister-ScheduledTask -TaskName $task -Confirm:$false
        "Removed '$task'. Nothing is watching ARC now."
    } catch { "'$task' was not installed." }
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

$action  = New-ScheduledTaskAction -Execute $py -Argument "guardian.py" -WorkingDirectory $here
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
# RestartCount/Interval covers the guardian itself dying -- the one failure it
# cannot report, because reporting is what it stopped doing.
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger `
    -Settings $settings -Description "Keeps ARC answering; logs what it had to do." -Force | Out-Null

Start-ScheduledTask -TaskName $task
Start-Sleep -Seconds 3
$s = (Get-ScheduledTask -TaskName $task).State
"Installed '$task' -- state: $s"
"It checks ARC every minute. Anything it had to do is written to:"
"  " + (Join-Path $here "guardian.log") + "   (empty means nothing went wrong)"
"  " + (Join-Path $here "guardian-status.json") + "   (how things are right now)"
