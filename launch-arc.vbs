' Runs the launcher with no console window flashing up.
Set sh = CreateObject("WScript.Shell")
sh.Run "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""C:\dev\arc-voice-assistant\arc\launch-arc.ps1""", 0, False
