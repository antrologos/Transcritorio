# Transcritorio - coleta de diagnostico.
# NAO altera nada: so LE arquivos e escreve um .txt na Area de Trabalho.
$ErrorActionPreference = "SilentlyContinue"
$app = "$env:LOCALAPPDATA\Transcritorio"
$out = "$([Environment]::GetFolderPath('Desktop'))\transcritorio-diagnostico.txt"

$projs = @()
if (Test-Path "$app\recent_projects.json") {
  $projs = (Get-Content "$app\recent_projects.json" -Raw | ConvertFrom-Json).recent | Where-Object { Test-Path $_ }
}
if (-not $projs) { Write-Host "Nao achei projeto nenhum. Abra o Transcritorio uma vez e rode de novo."; exit }

"=== Transcritorio - diagnostico ===" | Set-Content $out -Encoding utf8
"Gerado em: $(Get-Date)"              | Add-Content $out -Encoding utf8

foreach ($p in $projs | Select-Object -First 2) {
  ""                                       | Add-Content $out -Encoding utf8
  "########## PROJETO ##########"          | Add-Content $out -Encoding utf8
  "--- o que foi gerado, em ordem (a hora e o FIM de cada etapa) ---" | Add-Content $out -Encoding utf8
  Get-ChildItem "$p\Transcricoes" -Recurse -File |
    Where-Object { $_.Extension -in ".json",".wav",".md",".csv",".jsonl",".rttm" } |
    Sort-Object LastWriteTime |
    Select-Object @{n='Quando';e={$_.LastWriteTime.ToString('dd/MM HH:mm:ss')}},
                  @{n='MB';e={[math]::Round($_.Length/1MB,1)}},
                  @{n='Etapa';e={Split-Path (Split-Path $_.FullName -Parent) -Leaf}},
                  @{n='Arquivo';e={$_.Name}} |
    Format-Table -AutoSize | Out-String -Width 120 | Add-Content $out -Encoding utf8

  "--- etapas registradas (so os campos uteis) ---" | Add-Content $out -Encoding utf8
  Get-Content "$p\Transcricoes\00_manifest\jobs.jsonl" | ForEach-Object {
    $j = $_ | ConvertFrom-Json
    "{0,-14} {1,-8} {2,-9} {3,-6} {4,-22} {5}" -f $j.stage, $j.status, $j.device,
      $(if ($j.elapsed_s) { "$($j.elapsed_s)s" } else { "-" }), $j.started_at,
      $(if ($j.error) { "ERRO: " + $j.error.Substring(0,[Math]::Min(80,$j.error.Length)) } else { $j.backend })
  } | Add-Content $out -Encoding utf8

  "--- duracao dos audios ---" | Add-Content $out -Encoding utf8
  Import-Csv "$p\Transcricoes\00_manifest\manifest.csv" |
    Select-Object interview_id, duration_sec, source_ext, source_audio_channels |
    Format-Table -AutoSize | Out-String | Add-Content $out -Encoding utf8
}

"########## MODELOS: quando cada um terminou de baixar ##########" | Add-Content $out -Encoding utf8
Get-ChildItem "$app\models" -Recurse -File | Where-Object { $_.Length -gt 10MB } |
  Sort-Object LastWriteTime |
  Select-Object @{n='Quando';e={$_.LastWriteTime.ToString('dd/MM HH:mm:ss')}},
                @{n='MB';e={[math]::Round($_.Length/1MB,0)}}, Name |
  Format-Table -AutoSize | Out-String -Width 120 | Add-Content $out -Encoding utf8

"########## MAQUINA ##########" | Add-Content $out -Encoding utf8
"RAM total (GB): $([math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB,1))" | Add-Content $out -Encoding utf8
Get-CimInstance Win32_LogicalDisk | Where-Object DriveType -eq 3 |
  Select-Object DeviceID, @{n='LivreGB';e={[math]::Round($_.FreeSpace/1GB,1)}},
                @{n='TotalGB';e={[math]::Round($_.Size/1GB,1)}} |
  Format-Table -AutoSize | Out-String | Add-Content $out -Encoding utf8
"Disco fisico:" | Add-Content $out -Encoding utf8
Get-PhysicalDisk | Select-Object FriendlyName, MediaType | Format-Table -AutoSize | Out-String | Add-Content $out -Encoding utf8
"Antivirus:" | Add-Content $out -Encoding utf8
Get-CimInstance -Namespace root\SecurityCenter2 -Class AntiVirusProduct | Select-Object displayName |
  Format-Table -AutoSize | Out-String | Add-Content $out -Encoding utf8
"Plano de energia: $((powercfg /getactivescheme) -join ' ')" | Add-Content $out -Encoding utf8

"########## ULTIMAS LINHAS DO LOG DE DOWNLOAD ##########" | Add-Content $out -Encoding utf8
Get-Content "$app\download_diagnostic.log" -Tail 30 | Add-Content $out -Encoding utf8

Write-Host ""
Write-Host "Pronto! O arquivo esta na sua Area de Trabalho:"
Write-Host "   transcritorio-diagnostico.txt"
Write-Host "Da uma olhada nele antes de mandar (lista os nomes dos seus arquivos)."
