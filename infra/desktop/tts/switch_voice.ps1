param([Parameter(Mandatory=$true)][string]$Name, [switch]$DryRun)
$ErrorActionPreference = 'Stop'
$tts  = 'E:\voice-agent-repo\infra\desktop\tts'
$nssm = 'E:\voice-agent\tools\nssm.exe'
$lib = Get-Content -Raw -Encoding UTF8 "$tts\voices\library.json" | ConvertFrom-Json
$v = $lib.$Name
if (-not $v) {
    Write-Output "Unknown voice '$Name'. Available:"
    $lib.PSObject.Properties.Name | ForEach-Object { "  - $_" }
    exit 1
}
# TTS_LANGUAGE=Auto keeps the agent bilingual (RU/EN); Qwen3-TTS detects the
# language per utterance. Force a language here only if you want to pin one.
$lines = @('TTS_HOST=0.0.0.0','TTS_PORT=8002','TTS_LANGUAGE=Auto','TTS_CHUNK_SIZE=8',
           "# Voice: $Name ($($v.label))","TTS_ENGINE=$($v.engine)","TTS_MODEL=$($v.model)")
switch ($v.engine) {
    'voice_clone'  { $lines += "TTS_REF_AUDIO=$($v.ref_audio)"; $lines += "TTS_REF_TEXT=$($v.ref_text)" }
    'voice_design' { $lines += "TTS_INSTRUCT=$($v.instruct)" }
    'custom_voice' { $lines += "TTS_SPEAKER=$($v.speaker)" }
}
$content = ($lines -join "`n") + "`n"
Write-Output "--- .env for '$Name' ---"; Write-Output $content
if ($DryRun) { Write-Output '(dry run)'; exit 0 }
[System.IO.File]::WriteAllText("$tts\.env", $content, (New-Object System.Text.UTF8Encoding($false)))
Write-Output 'restarting voice-agent-tts ...'
& $nssm restart voice-agent-tts | Out-Null
Write-Output 'Done. Check: Invoke-RestMethod http://localhost:8002/health'
