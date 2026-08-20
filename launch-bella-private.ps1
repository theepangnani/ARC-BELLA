# Bella (PRIVATE) launcher — a second Bella that is yours alone.
#
# It runs the SAME code as the shared Bella, but on its own port (8421), with
# its own Google sign-in and its own completely separate data (reminders, notes,
# price alerts, Google sign-in, app window) kept in a folder OUTSIDE the code
# — so nothing it knows ever mixes with the shared instance your test users use.
#
# Shortcut it, or just run:  powershell -ExecutionPolicy Bypass -File launch-bella-private.ps1

$ErrorActionPreference = 'Stop'
# Both derived from this script's own location, so a clone works wherever it
# lands on a second machine. The data folder sits beside the repo, not inside
# it — it holds a signed-in browser profile and live sessions, and must never
# be committed.
$root = $PSScriptRoot
$data = Join-Path (Split-Path $PSScriptRoot -Parent) 'bella-private'
$port = 8421

# Pick a REAL Python — never the Microsoft Store alias in WindowsApps, which is
# a stub that can't run the server (it just opens the Store). We prefer pythonw
# (no console window), fall back to python, and always include the known-good
# pythoncore install even if it isn't on PATH.
function Find-Python {
  # Prefer the CONCRETE versioned install first: the Python-manager "bin" shim
  # and the Store alias both misbehave when Start-Process launches them detached
  # (the shim's real child gets orphaned and dies), so a double-click leaves
  # nothing running. The real pythoncore exe has no such indirection.
  $cands = @()
  # Sort by PARSED version (newest first), not by string — a string sort puts
  # pythoncore-3.9 ahead of pythoncore-3.13 because '9' > '1'.
  $verKey = { $m=[regex]::Match($_.FullName,'pythoncore-(\d+)\.(\d+)'); if($m.Success){[int]$m.Groups[1].Value*1000+[int]$m.Groups[2].Value}else{0} }
  $cands += (Get-ChildItem "$env:LOCALAPPDATA\Python\pythoncore-*\pythonw.exe" -ErrorAction SilentlyContinue | Sort-Object $verKey -Descending | ForEach-Object FullName)
  $cands += (Get-ChildItem "$env:LOCALAPPDATA\Python\pythoncore-*\python.exe"  -ErrorAction SilentlyContinue | Sort-Object $verKey -Descending | ForEach-Object FullName)
  $cands += (Get-Command pythonw.exe -All -ErrorAction SilentlyContinue | ForEach-Object Source)
  $cands += (Get-Command python.exe  -All -ErrorAction SilentlyContinue | ForEach-Object Source)
  foreach ($c in $cands) {
    if ($c -and (Test-Path $c) -and $c -notmatch '\\WindowsApps\\' -and $c -notmatch '\\Python\\bin\\') { return $c }
  }
  return 'python'   # last resort — let PATH decide
}

New-Item -ItemType Directory -Force -Path $data | Out-Null

# Optional: load private overrides (ARC_ALLOWED_EMAILS, ARC_NTFY_TOPIC, …) from
# bella-private\arc.env.
$envFile = Join-Path $data 'arc.env'
if (Test-Path $envFile) {
  Get-Content $envFile | ForEach-Object {
    if ($_ -match '^\s*([^#=]+)=(.*)$') {
      Set-Item -Path ('Env:' + $matches[1].Trim()) -Value $matches[2].Trim()
    }
  }
}

# There is no passphrase any more. Which of the two modes applies is set in
# bella-private\arc.env, so say which one is actually in force rather than
# assuming — a wrong hint here is worse than none.
if ($env:ARC_AUTH_MODE -eq 'open') {
  Write-Host "  Sign-in: none — opens straight in (this PC only)." -ForegroundColor DarkGray
  Write-Host "  To link Google for calendar/mail, run once:" -ForegroundColor DarkGray
  Write-Host "    `$env:ARC_DATA_DIR='$data'; python gauth.py" -ForegroundColor DarkGray
} else {
  Write-Host "  Sign-in: Google. If it is refused, add" -ForegroundColor DarkGray
  Write-Host "  http://localhost:$port/oauth/callback to your Google OAuth client." -ForegroundColor DarkGray
}

$env:ARC_PORT        = "$port"
$env:ARC_DATA_DIR    = $data
$env:ARC_APP_VARIANT = 'private'   # light-blue-on-black logo + its own app name

# run.py starts the server (or, if 8421 is already up, just opens a fresh
# private window) and pops Bella's own app window on this port.
$py = Find-Python
Start-Process -FilePath $py -ArgumentList 'run.py' -WorkingDirectory $root -WindowStyle Hidden

Write-Host ''
Write-Host "  Your private Bella is starting at  http://localhost:$port" -ForegroundColor Cyan
Write-Host "  Data folder (yours only):          $data"
Write-Host "  The shared Bella on 8420 is untouched."
Write-Host ''
