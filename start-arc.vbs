' Starts ARC with no window at all.
' Double-click this instead of typing "python run.py".
' Use stop-arc.vbs to shut it down.

Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")

folder = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = folder

' Fail loudly rather than silently. Without these checks a missing file or a
' missing Python just means "nothing happens", which is impossible to debug.
If Not fso.FileExists(folder & "\run.py") Then
    MsgBox "run.py is not in this folder." & vbCrLf & vbCrLf & _
           "Put start-arc.vbs in the same folder as run.py.", vbExclamation, "ARC"
    WScript.Quit
End If

' Is it already running? Starting a second copy is what leads to two servers
' on different ports and a lot of confusion.
Set svc = GetObject("winmgmts:{impersonationLevel=impersonate}!\\.\root\cimv2")
Set running = svc.ExecQuery("SELECT CommandLine FROM Win32_Process " & _
                            "WHERE Name = 'python.exe' OR Name = 'pythonw.exe'")
For Each p In running
    If Not IsNull(p.CommandLine) Then
        If InStr(LCase(p.CommandLine), "run.py") > 0 Then
            MsgBox "ARC is already running." & vbCrLf & vbCrLf & _
                   "Open localhost:8420 in Chrome, or run stop-arc first.", _
                   vbInformation, "ARC"
            WScript.Quit
        End If
    End If
Next

' 0 = hidden window, False = don't wait for it to finish
On Error Resume Next
sh.Run "python run.py", 0, False
If Err.Number <> 0 Then
    MsgBox "Could not start Python." & vbCrLf & vbCrLf & _
           "Open PowerShell and check that typing 'python' works.", _
           vbExclamation, "ARC"
End If
On Error GoTo 0
