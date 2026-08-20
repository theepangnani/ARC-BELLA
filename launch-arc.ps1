# ARC / Bella launcher.
# One click: start the server if it isn't running, then open the app window.
# Never starts a second server (which would grab a different port and pop a
# duplicate window) — if the core is already up, it just opens a fresh window.

$ErrorActionPreference = 'SilentlyContinue'
# Derived from this script's own location, never hardcoded, so a clone works
# wherever it lands on a second machine.
$root = $PSScriptRoot
$port = 8420

function PortUp($p) {
  $c = New-Object System.Net.Sockets.TcpClient
  try { $c.Connect('127.0.0.1', $p); $c.Close(); return $true } catch { return $false }
}

# Pick a REAL Python — never the Microsoft Store alias in WindowsApps (a stub
# that can't run the server). Prefer pythonw (no console), then python, and
# always include the pythoncore install even if it isn't on PATH.
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
  return 'python'
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
  $py = Find-Python
  Start-Process -FilePath $py -ArgumentList 'run.py' -WorkingDirectory $root -WindowStyle Hidden
}
