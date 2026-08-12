# Instalador de UN PEGADO de Horizun PBI MCP (Windows, sin administrador).
#
#   irm https://raw.githubusercontent.com/HorizunGroup/horizun-pbi-mcp/main/scripts/instalar.ps1 | iex
#
# Que hace: comprueba e instala los prerequisitos a NIVEL USUARIO (Python real,
# Git, Node opcional), esquiva el alias de Python de la Microsoft Store, ajusta
# la politica de ejecucion del usuario, registra el plugin en Claude Code y
# deja dicho el unico paso restante. Es idempotente: correrlo dos veces no
# rompe nada. No pide ni usa permisos de administrador.
#
# Nota de codificacion: este script usa solo ASCII a proposito - PowerShell 5.1
# lee UTF-8 sin BOM con la codepage OEM y destroza acentos (leccion del equipo).

$ErrorActionPreference = 'Continue'
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

$script:Pendientes = @()

function Paso($m)  { Write-Host ""; Write-Host ("== " + $m) -ForegroundColor Cyan }
function Ok($m)    { Write-Host ("  [OK] " + $m) -ForegroundColor Green }
function Aviso($m) { Write-Host ("  [PENDIENTE] " + $m) -ForegroundColor Yellow; $script:Pendientes += $m }
function Fallo($m) { Write-Host ("  [ERROR] " + $m) -ForegroundColor Red }

function Refresh-Path {
    $maquina = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $usuario = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = ($maquina, $usuario -join ';')
}

function Tiene($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }

function PythonReal {
    # El alias de la Store (WindowsApps) NO cuenta como Python.
    if (Tiene 'py') {
        try {
            $exe = (& py -3 -c "import sys;print(sys.executable)" 2>$null | Select-Object -First 1)
            if ($exe) { return $exe.Trim() }
        } catch {}
    }
    foreach ($c in @(Get-Command python -All -ErrorAction SilentlyContinue)) {
        if ($c.Source -and $c.Source -notmatch 'WindowsApps') { return $c.Source }
    }
    return $null
}

function InstalarConWinget($id, $nombre) {
    if (-not (Tiene 'winget')) {
        Aviso ("winget no esta disponible; instala " + $nombre + " a mano (busca '" + $id + "').")
        return $false
    }
    Write-Host ("  instalando " + $nombre + " (nivel usuario)...")
    # Un fallo de red aqui es transitorio con frecuencia (carrera DNS IPv6
    # medida por el equipo): dos intentos antes de rendirse.
    foreach ($intento in 1, 2) {
        & winget install -e --id $id --scope user --silent --accept-source-agreements --accept-package-agreements | Out-Null
        $codigo = $LASTEXITCODE
        # 0 = instalado; -1978335189 (0x8A15002B) = ya estaba instalado.
        if ($codigo -eq 0 -or $codigo -eq -1978335189) { Refresh-Path; return $true }
        if ($intento -eq 1) { Start-Sleep -Seconds 4 }
    }
    Aviso ($nombre + " no se pudo instalar con winget (codigo " + $codigo + "). Si tu equipo bloquea winget, pide a TI: " + $id + " a nivel usuario.")
    return $false
}

Write-Host ""
Write-Host "Horizun PBI MCP - instalador de un pegado (sin administrador)" -ForegroundColor White
Write-Host "-------------------------------------------------------------"

# --- 1. Politica de ejecucion del usuario ------------------------------------
Paso "Politica de ejecucion de PowerShell (solo tu usuario)"
try {
    $actual = Get-ExecutionPolicy -Scope CurrentUser
    if ($actual -in @('Restricted', 'Undefined', 'AllSigned')) {
        Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force
        Ok "Ajustada a RemoteSigned (antes: $actual)."
    } else {
        Ok "Ya estaba en $actual."
    }
} catch { Aviso "No se pudo ajustar la politica de ejecucion: $($_.Exception.Message)" }

# --- 2. Python real (no el alias de la Store) --------------------------------
Paso "Python 3.10+ real"
$py = PythonReal
if (-not $py) {
    InstalarConWinget 'Python.Python.3.12' 'Python 3.12' | Out-Null
    $py = PythonReal
}
if ($py) {
    $ver = (& $py -c "import sys;print('.'.join(map(str,sys.version_info[:2])))" 2>$null)
    Ok ("Python " + $ver + " en " + $py)
    $soloAlias = @(Get-Command python -All -ErrorAction SilentlyContinue |
                   Where-Object { $_.Source -match 'WindowsApps' })
    if ($soloAlias.Count -gt 0) {
        Write-Host "  (el alias 'python' de la Microsoft Store sigue en el PATH; el plugin ya lo esquiva solo)"
    }
} else {
    Aviso "Sin Python real. Instala Python 3.12 (winget install -e --id Python.Python.3.12 --scope user) y vuelve a pegar este comando."
}

# --- 3. Git (lo exige Claude Code para sesiones locales) ---------------------
Paso "Git"
if (Tiene 'git') { Ok ("git " + ((& git --version) -replace 'git version ', '')) }
else {
    if (InstalarConWinget 'Git.Git' 'Git') {
        if (Tiene 'git') { Ok "Git instalado." } else { Aviso "Git instalado pero el PATH aun no lo ve: cierra y reabre la terminal." }
    }
}

# --- 4. Node LTS (OPCIONAL: enciende el validador PBIR oficial) --------------
Paso "Node.js LTS (opcional, para el validador oficial de Microsoft)"
if (Tiene 'node') { Ok ("node " + (& node --version)) }
else {
    # El MSI de Node suele ser por-maquina: sin admin puede fallar, y NO es
    # bloqueante - el MCP funciona sin validador y lo dice honestamente.
    if (-not (InstalarConWinget 'OpenJS.NodeJS.LTS' 'Node.js LTS')) {
        Aviso "Sin Node el MCP funciona igual; el validador PBIR queda apagado hasta instalarlo."
    } elseif (Tiene 'node') { Ok ("node " + (& node --version)) }
}

# --- 5. Claude Code ----------------------------------------------------------
Paso "Claude Code"
if (Tiene 'claude') {
    Ok ("claude " + ((& claude --version 2>$null | Select-Object -First 1)))
} elseif (Tiene 'npm') {
    Write-Host "  instalando Claude Code con npm (nivel usuario)..."
    & npm install -g "@anthropic-ai/claude-code" | Out-Null
    Refresh-Path
    if (Tiene 'claude') { Ok "Claude Code instalado." }
    else { Aviso "npm termino pero 'claude' no aparece en el PATH: cierra y reabre la terminal, y repega este comando." }
} else {
    Aviso "Claude Code no esta y no hay npm para instalarlo. Instalalo desde la documentacion oficial de Anthropic y repega este comando."
}

# --- 6. Registrar el plugin en Claude Code -----------------------------------
Paso "Plugin horizun-pbi-mcp en Claude Code"
if (Tiene 'claude') {
    & claude plugin marketplace add HorizunGroup/horizun-pbi-mcp 2>&1 | Out-Null
    & claude plugin install horizun-pbi-mcp@horizun 2>&1 | Out-Null
    Ok "Plugin registrado (si ya estaba, no pasa nada: es idempotente)."
} else {
    Aviso "Sin Claude Code no se puede registrar el plugin todavia."
}

# --- 7. Veredicto ------------------------------------------------------------
Write-Host ""
Write-Host "-------------------------------------------------------------"
if ($script:Pendientes.Count -eq 0) {
    Write-Host "LISTO. Un solo paso restante:" -ForegroundColor Green
    Write-Host "  1. Abre (o reinicia) Claude Code: la primera sesion prepara el runtime SOLA."
    Write-Host "  2. Escribe 'pbi_install_status' si quieres ver el avance."
    Write-Host "  3. Cuando diga 'Runtime listo', reinicia Claude Code una vez: apareceran las tools pbi_*."
} else {
    Write-Host ("QUEDARON " + $script:Pendientes.Count + " PENDIENTE(S):") -ForegroundColor Yellow
    $script:Pendientes | ForEach-Object { Write-Host ("  - " + $_) }
    Write-Host "  Resuelvelos (o pide a TI lo marcado) y vuelve a pegar este mismo comando: es seguro repetirlo."
}
Write-Host "-------------------------------------------------------------"
