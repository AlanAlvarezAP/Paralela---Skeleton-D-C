# Espera a que termine run_experiments.sh en n1 y avisa.
# uso: powershell -ExecutionPolicy Bypass -File wait_done.ps1 -N1 <ip> [-DownloadTo carpeta]

param(
    [Parameter(Mandatory = $true)][string]$N1,
    [string]$Key = "$HOME\.ssh\scw_cluster",
    [int]$Every = 60,
    [string]$DownloadTo = ""
)

$remote = 'd=$(cat ~/RUN_DONE 2>/dev/null); n=$(cat ~/DONE_RUNS 2>/dev/null); ' +
          't=$(grep -o ''[0-9]* runs'' ~/EXPECTED_RUNS 2>/dev/null | cut -d'' '' -f1); ' +
          'p=$(grep -E ''^### (extra|hb|full|smoke|tau)'' ~/compare.out 2>/dev/null | tail -1); ' +
          'echo $d^|$n^|$t^|$p'
$remote = $remote.Replace('^|', '\|')

function Show-Done([string]$title, [string]$msg) {
    1..3 | ForEach-Object { [console]::Beep(880, 300); Start-Sleep -Milliseconds 150 }
    try {
        Add-Type -AssemblyName System.Windows.Forms, System.Drawing
        $ni = New-Object System.Windows.Forms.NotifyIcon
        $ni.Icon = [System.Drawing.SystemIcons]::Information
        $ni.Visible = $true
        $ni.ShowBalloonTip(15000, $title, $msg, [System.Windows.Forms.ToolTipIcon]::Info)
        [System.Windows.Forms.MessageBox]::Show($msg, $title) | Out-Null
        $ni.Dispose()
    } catch {
        Write-Host "(no se pudo mostrar la notificacion: $_)"
    }
}

Write-Host "Esperando a que termine run_experiments.sh en $N1 (revisa cada $Every s, Ctrl+C para salir)"
$fails = 0
while ($true) {
    $line = ssh -i $Key -o BatchMode=yes -o ConnectTimeout=15 "root@$N1" $remote 2>$null
    $now = Get-Date -Format "HH:mm:ss"
    if ($LASTEXITCODE -ne 0 -or -not $line) {
        $fails++
        Write-Host "$now  sin respuesta de $N1 ($fails seguidas)"
        if ($fails -eq 5) { Show-Done "Cluster: sin respuesta" "n1 ($N1) no responde hace 5 intentos. Revisa la conexion o la instancia." }
        Start-Sleep -Seconds $Every
        continue
    }
    $fails = 0
    $done, $n, $t, $phase = ([string]$line).Split('|', 4)
    if ($done -ne "") {
        $estado = if ($done -eq "0") { "todo ok" } elseif ($done -eq "1") { "hubo PROBLEMS (revisar compare.out)" } else { "fallo (codigo $done)" }
        Write-Host "$now  TERMINO: RUN_DONE=$done -> $estado"
        $msg = "Los experimentos terminaron en $N1`nRUN_DONE = $done ($estado)"
        if ($DownloadTo) {
            New-Item -ItemType Directory -Force $DownloadTo | Out-Null
            $tar = Join-Path $DownloadTo "resultados_hb.tar.gz"
            if (Test-Path $tar) {
                $msg += "`nNo se descargo: ya existe $tar"
            } else {
                scp -i $Key "root@${N1}:~/resultados_hb.tar.gz" $tar
                if ($LASTEXITCODE -eq 0) {
                    $out = Join-Path $DownloadTo "resultados"
                    New-Item -ItemType Directory -Force $out | Out-Null
                    tar -xzf $tar -C $out
                    $msg += "`nResultados en $out"
                } else {
                    $msg += "`nFallo la descarga (scp)"
                }
            }
        }
        Write-Host $msg
        Show-Done "Cluster: termino" $msg
        break
    }
    $prog = if ($t) { "$n / $t corridas" } else { "corriendo" }
    Write-Host "$now  $prog   $phase"
    Start-Sleep -Seconds $Every
}
