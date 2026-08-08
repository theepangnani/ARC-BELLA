' Launch ARC / Bella as an app, with no console window.
'  - If the core isn't running, start it hidden (it opens its own window).
'  - If it's already running, just open a fresh app window pointed at it.
' Double-click this, or use the "Bella" desktop shortcut. stop-arc.vbs shuts it down.

Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")

folder = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = folder

' Fail loudly rather than silently if the launcher was moved away from run.py.
If Not fso.FileExists(folder & "\run.py") Then
    MsgBox "run.py is not in this folder." & vbCrLf & vbCrLf & _
           "Keep start-arc.vbs in the same folder as run.py.", vbExclamation, "ARC"
    WScript.Quit
End If

' Is a core already running? Starting a second copy is what leads to two
' servers on different ports and a lot of confusion.
isRunning = False
Set svc = GetObject("winmgmts:{impersonationLevel=impersonate}!\\.\root\cimv2")
Set procs = svc.ExecQuery("SELECT CommandLine FROM Win32_Process " & _
                          "WHERE Name = 'python.exe' OR Name = 'pythonw.exe'")
For Each p In procs
    If Not IsNull(p.CommandLine) Then
        If InStr(LCase(p.CommandLine), "run.py") > 0 Then isRunning = True
    End If
Next

If isRunning Then
    ' Already up — just open the app window (chromeless, keeps voice working).
    brs = FindBrowser()
    If brs <> "" Then
        sh.Run """" & brs & """ --app=http://localhost:8420 " & _
               "--user-data-dir=""" & folder & "\.arc-window"" " & _
               "--window-size=1180,820 --no-first-run --no-default-browser-check", 1, False
    Else
        sh.Run "http://localhost:8420", 1, False
    End If
Else
    ' Not running — start the core hidden; run.py opens the window once it's up.
    On Error Resume Next
    sh.Run "python run.py", 0, False
    If Err.Number <> 0 Then
        MsgBox "Could not start Python." & vbCrLf & vbCrLf & _
               "Open PowerShell and check that typing 'python' works.", _
               vbExclamation, "ARC"
    End If
    On Error GoTo 0
End If

' Return the first Chrome or Edge found, or "" if neither is installed.
Function FindBrowser()
    Dim candidates, i
    candidates = Array( _
        "C:\Program Files\Google\Chrome\Application\chrome.exe", _
        "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe", _
        sh.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Google\Chrome\Application\chrome.exe", _
        "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", _
        "C:\Program Files\Microsoft\Edge\Application\msedge.exe")
    FindBrowser = ""
    For i = 0 To UBound(candidates)
        If fso.FileExists(candidates(i)) Then
            FindBrowser = candidates(i)
            Exit Function
        End If
    Next
End Function
