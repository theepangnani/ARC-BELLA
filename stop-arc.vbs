' Stops ARC.
'
' Asks Windows directly for any Python process whose command line mentions
' run.py, and ends those. Nothing else is touched.
'
' Earlier attempts at this used batch files that read port numbers or shelled
' out to PowerShell. Both were defeated by quoting and output-format
' differences between Windows versions. This asks the system the question
' directly instead.

Set svc = GetObject("winmgmts:{impersonationLevel=impersonate}!\\.\root\cimv2")
Set procs = svc.ExecQuery("SELECT ProcessId, Name, CommandLine FROM Win32_Process " & _
                          "WHERE Name = 'python.exe' OR Name = 'pythonw.exe'")

killed = 0
For Each p In procs
    If Not IsNull(p.CommandLine) Then
        If InStr(LCase(p.CommandLine), "run.py") > 0 Then
            On Error Resume Next
            p.Terminate()
            If Err.Number = 0 Then killed = killed + 1
            Err.Clear
            On Error Goto 0
        End If
    End If
Next

If killed = 0 Then
    MsgBox "ARC was not running.", vbInformation, "ARC"
ElseIf killed = 1 Then
    MsgBox "ARC stopped.", vbInformation, "ARC"
Else
    MsgBox killed & " copies of ARC were running. All stopped.", vbInformation, "ARC"
End If
