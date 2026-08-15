' Runs the launcher with no console window flashing up.
' The path is taken from this script's own folder rather than hardcoded, so a
' clone works wherever it lands on a second machine.
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")

folder = fso.GetParentFolderName(WScript.ScriptFullName)
sh.Run "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & _
       folder & "\launch-arc.ps1""", 0, False
