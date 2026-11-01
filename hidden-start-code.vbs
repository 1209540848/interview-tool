' interview-tool - 笔试版启动器（静默，无控制台闪窗）
' 手动模式（2026-09-23 切）：只有 Alt+F1 开始录 → Alt+F2 结束 → 转写提问 → DeepSeek 作答，
' 自动 VAD/门控/作废全不启动（面试官连续说话时不会再把在途答案打断）。
' 想回自动模式：双击 hidden-start-code-auto.vbs
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
Set ws = CreateObject("Wscript.Shell")
py = "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe"
If Not fso.FileExists(py) Then py = "python"
ws.CurrentDirectory = base
ws.Run """" & py & """ """ & base & "\run_code.py"" --chameleon --manual", 0, False
