# ARC / Bella launcher.
# One click: start the server if it isn't running, then open the app window.
# Never starts a second server (which would grab a different port and pop a
# duplicate window) — if the core is already up, it just opens a fresh window.

$ErrorActionPreference = 'SilentlyContinue'
$root = 'C:\dev\arc-voice-assistant\arc'
$port = 8420

function PortUp($p) {
  $c = New-Object System.Net.Sockets.TcpClient
  try { $c.Connect('127.0.0.1', $p); $c.Close(); return $true } catch { return $false }
}

Set-Location $root

$browser = @(
  'C:\Program Files\Google\Chrome\Application\chrome.exe',
  'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
  "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe",
  'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
  'C:\Program Files\Microsoft\Edge\Application\msedge.exe'
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (PortUp $port) {
  # Already running — just open a window pointed at it.
  if ($browser) {
    Start-Process $browser -ArgumentList `
      "--app=http://localhost:$port", "--user-data-dir=$root\.arc-window",
      "--window-size=1180,820", "--no-first-run", "--no-default-browser-check"
  } else {
    Start-Process "http://localhost:$port"
  }
} else {
  # Start the core; run.py opens the app window itself once it's listening.
  $py = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
  if (-not $py) { $py = (Get-Command python -ErrorAction SilentlyContinue).Source }
  Start-Process -FilePath $py -ArgumentList 'run.py' -WorkingDirectory $root -WindowStyle Hidden
}
