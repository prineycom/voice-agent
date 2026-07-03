$lib = Get-Content -Raw -Encoding UTF8 'E:\voice-agent-repo\infra\desktop\tts\voices\library.json' | ConvertFrom-Json
Write-Output 'Available voices (switch_voice.ps1 -Name <id>):'
$lib.PSObject.Properties | ForEach-Object { '{0,-12} {1,-13} {2}' -f $_.Name, $_.Value.engine, $_.Value.label }
