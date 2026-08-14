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
$py = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = (Get-Command python).Source }
Start-Process -FilePath $py -ArgumentList 'run.py' -WorkingDirectory $root -WindowStyle Hidden

Write-Host ''
Write-Host "  Your private Bella is starting at  http://localhost:$port" -ForegroundColor Cyan
Write-Host "  Data folder (yours only):          $data"
Write-Host "  The shared Bella on 8420 is untouched."
Write-Host ''
