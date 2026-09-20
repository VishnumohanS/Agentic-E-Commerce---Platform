$projectRoot = $PSScriptRoot

Start-Process powershell.exe `
    -WorkingDirectory $projectRoot `
    -ArgumentList '-NoExit', '-Command', 'python main.py'

Start-Process powershell.exe `
    -WorkingDirectory $projectRoot `
    -ArgumentList '-NoExit', '-Command', 'python buyer_agent\main.py'