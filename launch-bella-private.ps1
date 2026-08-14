# Bella (PRIVATE) launcher — a second Bella that is yours alone.
#
# It runs the SAME code as the shared Bella, but on its own port (8421), with
# its own passphrase and its own completely separate data (reminders, notes,
# price alerts, Google sign-in, app window) kept in a folder OUTSIDE the code
# — so nothing it knows ever mixes with the shared instance your test users use.
#
# Shortcut it, or just run:  powershell -ExecutionPolicy Bypass -File launch-bella-private.ps1

$ErrorActionPreference = 'Stop'
$root = 'C:\dev\arc-voice-assistant\arc'
$data = 'C:\dev\arc-voice-assistant\bella-private'   # private data lives here (not in the repo)
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
  $cands += (Get-ChildItem "$env:LOCALAPPDATA\Python\pythoncore-*\pythonw.exe" -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | ForEach-Object FullName)
  $cands += (Get-ChildItem "$env:LOCALAPPDATA\Python\pythoncore-*\python.exe"  -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | ForEach-Object FullName)
  $cands += (Get-Command pythonw.exe -All -ErrorAction SilentlyContinue | ForEach-Object Source)
  $cands += (Get-Command python.exe  -All -ErrorAction SilentlyContinue | ForEach-Object Source)
  foreach ($c in $cands) {
    if ($c -and (Test-Path $c) -and $c -notmatch '\\WindowsApps\\' -and $c -notmatch '\\Python\\bin\\') { return $c }
  }
  return 'python'   # last resort — let PATH decide
}

New-Item -ItemType Directory -Force -Path $data | Out-Null

# Optional: load private overrides (ARC_PASSWORD, ARC_NTFY_TOPIC, …) from
# bella-private\arc.env if you want them saved instead of typed each launch.
$envFile = Join-Path $data 'arc.env'
if (Test-Path $envFile) {
  Get-Content $envFile | ForEach-Object {
    if ($_ -match '^\s*([^#=]+)=(.*)$') {
      Set-Item -Path ('Env:' + $matches[1].Trim()) -Value $matches[2].Trim()
    }
  }
}

# No passphrase configured? Ask once. It is used only for this launch and never
# written anywhere unless you choose to put it in arc.env yourself.
if (-not $env:ARC_PASSWORD) {
  $sec  = Read-Host 'Set a passphrase for your PRIVATE Bella' -AsSecureString
  $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
  $env:ARC_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
  [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
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
