# Instalador de Horizun PBI MCP (Windows, sin administrador).
#
# Uso local, sobre este mismo repositorio:
#
#   powershell -NoProfile -File scripts/instalar.ps1
#   powershell -NoProfile -File scripts/instalar.ps1 -DryRun
#
# El camino publicado descarga ESTE archivo desde una release fija, comprueba su
# SHA-256 y solo entonces lo ejecuta: el bloque esta en scripts/one_paste.ps1 y
# el hash en scripts/downloads_manifest.json. Aqui no se repite ninguno de los
# dos - un hash escrito en dos sitios es un hash que acabara desincronizado, y
# el hash de un archivo no puede vivir dentro de ese archivo.
#
# Que hace: comprueba e instala los prerequisitos a NIVEL USUARIO (Python real,
# Git, Node opcional), esquiva el alias de Python de la Microsoft Store, ajusta
# la politica de ejecucion del usuario, registra el marketplace personal que
# lee ChatGPT Desktop y el plugin de Claude Code, y deja dicho el unico paso
# restante. Es idempotente: correrlo dos veces no rompe nada. No pide ni usa
# permisos de administrador.
#
# -DryRun: diagnostica y publica el PLAN sin tocar nada. Ningun efecto puede
# escaparse porque todos pasan por la funcion Efecto, que en seco registra la
# accion y devuelve el valor simulado sin ejecutar el bloque. Lo unico que si
# se ejecuta en seco son las sondas de diagnostico de la lista blanca de abajo,
# que no instalan, no escriben y terminan solas.
#
# Nota de codificacion: este script usa solo ASCII a proposito - PowerShell 5.1
# lee UTF-8 sin BOM con la codepage OEM y destroza acentos (leccion del equipo).

param(
    # Diagnostica y describe el plan sin ejecutar NINGUN efecto: no descarga, no
    # instala, no registra plugins, no toca la politica de ejecucion, no escribe
    # ni borra archivos y no arranca Claude ni Codex.
    [switch]$DryRun,

    # INSTALL-007. Prohibe el reintento sin `--scope user`. Por defecto el
    # reintento existe -y sin el, el camino del PC vacio se rompe: winget
    # responde 0x8A150044 cuando un manifiesto ajeno no esta etiquetado como
    # 'user', aunque su instalador por defecto SI instale en el perfil-, pero
    # deja de ser silencioso: se anuncia antes y se comprueba despues donde
    # aterrizo. Con esta bandera, un equipo que exija user-scope estricto
    # prefiere fallar a instalar fuera del perfil.
    [switch]$SoloUserScope
)

$ErrorActionPreference = 'Continue'
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

$script:Seco       = [bool]$DryRun
$script:Pendientes = @()
$script:FallosVerificacion = @()
$script:Plan       = @()
$script:Detectado  = @()
$script:Faltante   = @()
$script:Clientes   = @()

function Paso($m)  { Write-Host ""; Write-Host ("== " + $m) -ForegroundColor Cyan }
function Ok($m)    { Write-Host ("  [OK] " + $m) -ForegroundColor Green; $script:Detectado += $m }
function Aviso($m) { Write-Host ("  [PENDIENTE] " + $m) -ForegroundColor Yellow; $script:Pendientes += $m }
function Fallo($m) { Write-Host ("  [ERROR] " + $m) -ForegroundColor Red }
function FalloVerificacion($m) {
    Fallo $m
    $script:FallosVerificacion += $m
}
function Falta($m) { $script:Faltante += $m }

function Efecto {
    <#
      .SYNOPSIS
      Puerta UNICA de todo efecto observable del instalador.

      .DESCRIPTION
      Instalar, registrar, descargar, arrancar un cliente o cambiar la politica
      de ejecucion pasa por aqui y por ningun otro sitio. En modo -DryRun se
      anota la accion en el plan y se devuelve $Simulado SIN ejecutar $Accion.

      Que la puerta sea unica es lo que hace demostrable el "cero efectos": la
      prueba no tiene que adivinar por donde podria escaparse un efecto, solo
      comprobar que nada llama a un ejecutable fuera de la lista blanca de
      sondas. Un efecto nuevo que no pase por Efecto es un fallo de revision,
      y tests/test_instalador_dryrun.py lo caza.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Categoria,
        [Parameter(Mandatory = $true)][string]$Descripcion,
        [Parameter(Mandatory = $true)][scriptblock]$Accion,
        [object[]]$Argumentos = @(),
        $Simulado = $null
    )
    if ($script:Seco) {
        $script:Plan += [pscustomobject]@{ Categoria = $Categoria; Accion = $Descripcion }
        Write-Host ("  [PLAN] " + $Descripcion) -ForegroundColor DarkCyan
        return $Simulado
    }
    return (& $Accion @Argumentos)
}

function Refresh-Path {
    # Lectura del PATH de maquina y usuario y escritura del PATH DE ESTE
    # PROCESO. No es una variable persistente: muere con el proceso.
    $maquina = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $usuario = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = ($maquina, $usuario -join ';')
}

function Tiene($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }

function SumarAlPathDeSesion($carpeta) {
    # El instalador nativo deja claude en ~\.local\bin. Esa carpeta entra en el
    # PATH del USUARIO, pero este proceso ya arranco con el PATH viejo: sin
    # esto, el paso siguiente no encuentra 'claude' y habria que pedirle a la
    # persona que cierre y reabra la terminal, que es justo lo que este
    # instalador existe para evitar.
    if ((Test-Path -LiteralPath $carpeta) -and ($env:Path -notlike ("*" + $carpeta + "*"))) {
        $env:Path = $carpeta + ";" + $env:Path
    }
}

function DetectarClaudeCode {
    # Antes esto descargaba un script de Anthropic y lo pasaba por
    # Invoke-Expression. Ejecutar lo que devuelva una URL es confiar en que
    # nadie cambio esos bytes desde la ultima vez que alguien los miro, y no
    # hay version fija ni SHA-256 publicado con el que comprobarlo. HTTPS dice
    # QUIEN te lo da, no QUE te da.
    #
    # Claude Code es un requisito EXTERNO y opcional de este instalador: sin el
    # se instala igual el MCP, solo queda sin registrar en ese cliente. Asi que
    # se detecta y, si falta, se dice como instalarlo desde la fuente oficial y
    # se sigue. Instalarlo es decision de quien usa la maquina, no nuestra.
    Refresh-Path
    SumarAlPathDeSesion (Join-Path $env:USERPROFILE ".local\bin")
    return (Tiene 'claude')
}

function RegistrarMarketplaceChatGPT {
    # ChatGPT Desktop descubre automaticamente este marketplace personal. No
    # se pisa un JSON ilegible: un archivo roto es evidencia diagnostica, no
    # permiso para reemplazarlo. Si hay que cambiar uno valido, se respalda y
    # se publica con File.Replace en el mismo volumen.
    $destino = Join-Path $env:USERPROFILE '.agents\plugins\marketplace.json'
    $carpeta = Split-Path -Parent $destino
    $existia = Test-Path -LiteralPath $destino
    if ($existia) {
        $crudo = [IO.File]::ReadAllText($destino)
        try { $doc = $crudo | ConvertFrom-Json }
        catch { throw ("El marketplace de ChatGPT no contiene JSON valido: " + $destino + ". Se conserva intacto.") }
        if ($null -eq $doc -or $doc -is [Array]) {
            throw ("El marketplace de ChatGPT no contiene un objeto JSON: " + $destino + ". Se conserva intacto.")
        }
    } else {
        $doc = [pscustomobject]@{
            name = 'personal'
            interface = [pscustomobject]@{ displayName = 'Personal' }
            plugins = @()
        }
    }

    if (-not ($doc.PSObject.Properties.Name -contains 'plugins')) {
        $doc | Add-Member -NotePropertyName plugins -NotePropertyValue @()
    }
    if ($null -eq $doc.plugins) { $doc.plugins = @() }
    $actuales = @($doc.plugins | Where-Object { $_.name -eq 'horizun-pbi-mcp' })
    if ($actuales.Count -eq 1) {
        $a = $actuales[0]
        if ($a.source.source -eq 'url' -and
            $a.source.url -eq 'https://github.com/HorizunGroup/horizun-pbi-mcp.git' -and
            $a.source.ref -eq 'v2.2.0' -and
            $a.policy.installation -eq 'AVAILABLE' -and
            $a.policy.authentication -eq 'ON_INSTALL') {
            return [pscustomobject]@{ Path = $destino; Changed = $false; Backup = $null }
        }
    }

    $entrada = [pscustomobject]@{
        name = 'horizun-pbi-mcp'
        source = [pscustomobject]@{
            source = 'url'
            url = 'https://github.com/HorizunGroup/horizun-pbi-mcp.git'
            ref = 'v2.2.0'
        }
        policy = [pscustomobject]@{
            installation = 'AVAILABLE'
            authentication = 'ON_INSTALL'
        }
        category = 'Developer Tools'
    }
    $otros = @($doc.plugins | Where-Object { $_.name -ne 'horizun-pbi-mcp' })
    $doc.plugins = @($otros) + @($entrada)

    [IO.Directory]::CreateDirectory($carpeta) | Out-Null
    $tmp = $destino + '.tmp-' + [guid]::NewGuid().ToString('N')
    $utf8 = New-Object Text.UTF8Encoding($false)
    $texto = ($doc | ConvertTo-Json -Depth 20) + "`n"
    [IO.File]::WriteAllText($tmp, $texto, $utf8)
    $respaldo = $null
    try {
        # Releer lo escrito antes de publicarlo: nunca se deja un JSON que no
        # pueda volver a parsearse.
        [void]([IO.File]::ReadAllText($tmp) | ConvertFrom-Json)
        if ($existia) {
            $respaldo = $destino + '.bak-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
            [IO.File]::Replace($tmp, $destino, $respaldo, $true)
        } else {
            [IO.File]::Move($tmp, $destino)
        }
    } finally {
        if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
    }
    return [pscustomobject]@{ Path = $destino; Changed = $true; Backup = $respaldo }
}

function PythonRealPorRuta {
    # Descubrimiento por SISTEMA DE ARCHIVOS: cero ejecuciones.
    #
    # Existe porque `py -3` NO es una sonda inocente. En Windows moderno `py`
    # es el Python Install Manager, y preguntarle por un interprete que no
    # tiene lo hace DESCARGARLO E INSTALARLO. Medido en esta suite: con un
    # LOCALAPPDATA limpio, un solo `py -3 -c "import sys;print(sys.executable)"`
    # dejo pythoncore-3.14-64-3.14.7.zip, su .job y last_welcome.txt en el
    # cache. Es decir: en el PC vacio -el unico caso donde -DryRun de verdad
    # importa- la sonda instalaba Python. Aqui se resuelve mirando disco, que
    # es lo que ya hace launch.cmd.
    #
    # El alias de la Store (WindowsApps) NO cuenta como Python.
    foreach ($c in @(Get-Command python -All -ErrorAction SilentlyContinue)) {
        if ($c.Source -and $c.Source -notmatch 'WindowsApps') { return $c.Source }
    }
    $raices = @((Join-Path $env:LOCALAPPDATA 'Python'),
                (Join-Path $env:LOCALAPPDATA 'Programs\Python'),
                (Join-Path $env:ProgramFiles 'Python'))
    foreach ($raiz in $raices) {
        if ($raiz -and (Test-Path -LiteralPath $raiz)) {
            $hallado = Get-ChildItem -LiteralPath $raiz -Recurse -Depth 2 `
                                     -Filter 'python.exe' -ErrorAction SilentlyContinue |
                       Select-Object -First 1
            if ($hallado) { return $hallado.FullName }
        }
    }
    return $null
}

function PythonReal {
    # En seco no se le pregunta a `py`: ver PythonRealPorRuta.
    if ($script:Seco) { return (PythonRealPorRuta) }
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

function WingetIntento($id, $conScope) {
    # Devuelve el codigo de salida de winget. 0 = instalado;
    # -1978335189 (0x8A15002B) = ya estaba instalado, que para nosotros es exito.
    $etiqueta = if ($conScope) { " --scope user" } else { " (sin --scope)" }
    return (Efecto -Categoria 'winget' `
                   -Descripcion ("winget install -e --id " + $id + $etiqueta) `
                   -Simulado 'SECO' `
                   -Argumentos @($id, $conScope) `
                   -Accion {
                        param($idPaq, $usarScope)
                        if ($usarScope) {
                            & winget install -e --id $idPaq --scope user --silent --accept-source-agreements --accept-package-agreements | Out-Null
                        } else {
                            & winget install -e --id $idPaq --silent --accept-source-agreements --accept-package-agreements | Out-Null
                        }
                        return $LASTEXITCODE
                   })
}

#: Version minima que el validador PBIR oficial de Microsoft acepta. Es la
#: misma que exige scripts/plugin_bootstrap.py; si divergieran, el instalador
#: diria "Ok" de un Node que el bootstrap va a rechazar despues.
$script:NodeMinimo = 20

function EstadoDeNode {
    # Devuelve el texto a mostrar y si SIRVE. Antes se imprimia `node --version`
    # como Ok sin compararlo con nada: un Node 18 salia en verde y el validador
    # quedaba apagado sin que el instalador lo dijera.
    $crudo = ''
    try { $crudo = (& node --version 2>&1 | Out-String).Trim() } catch { $crudo = '' }
    $mayor = 0
    if ($crudo -match 'v?(\d+)\.') { $mayor = [int]$Matches[1] }
    return @{ Texto = $crudo; Mayor = $mayor; Sirve = ($mayor -ge $script:NodeMinimo) }
}

function ReportarNode {
    $n = EstadoDeNode
    if ($n.Sirve) {
        Ok ("node " + $n.Texto)
    } else {
        Aviso ("node " + $n.Texto + " es anterior a la version " + $script:NodeMinimo +
               " que exige el validador PBIR oficial. El MCP funciona igual; el " +
               "validador queda apagado hasta actualizar Node.")
    }
}

function ComprobarDondeAterrizo($nombre) {
    # Se instalo SIN --scope: hay que decir donde acabo, no suponerlo. Si cayo
    # fuera del perfil no es un fallo -sigue sin haber elevacion- pero es una
    # diferencia con lo que se prometio y tiene que constar.
    $cmd = Get-Command $nombre -ErrorAction SilentlyContinue
    if (-not $cmd -or -not $cmd.Source) {
        Aviso ("No se pudo comprobar donde quedo instalado " + $nombre + ".")
        return
    }
    if ($cmd.Source.StartsWith($env:USERPROFILE, [StringComparison]::OrdinalIgnoreCase)) {
        Ok ($nombre + " quedo en tu perfil: " + $cmd.Source)
    } else {
        Aviso ($nombre + " quedo FUERA de tu perfil: " + $cmd.Source +
               ". No se pidio administrador en ningun momento, pero no es una " +
               "instalacion de usuario. Para desinstalarlo: winget uninstall " + $nombre)
    }
}

function InstalarConWinget($id, $nombre) {
    if (-not (Tiene 'winget')) {
        Aviso ("winget no esta disponible; instala " + $nombre + " a mano (busca '" + $id + "').")
        return $false
    }
    if ($script:Seco) {
        # En seco no se finge un exito: se declara la accion prevista y se
        # devuelve "no instalado", que es la verdad de lo que quedo en disco.
        WingetIntento $id $true | Out-Null
        return $false
    }
    Write-Host ("  instalando " + $nombre + " (nivel usuario)...")
    $exitos = @(0, -1978335189)
    # Un fallo de red aqui es transitorio con frecuencia (carrera DNS IPv6
    # medida por el equipo): dos intentos antes de rendirse.
    foreach ($intento in 1, 2) {
        $codigo = WingetIntento $id $true
        if ($exitos -contains $codigo) { Refresh-Path; return $true }

        # SEGUNDA OPORTUNIDAD SIN --scope. No todos los paquetes publican un
        # instalador marcado como 'user', y cuando no lo hacen winget responde
        # "No applicable installer found" (0x8A150044) y se planta, aunque el
        # instalador por defecto SI instale en el perfil del usuario. Depender
        # de como este etiquetado un manifiesto ajeno es apostar a un dato que
        # Microsoft puede cambiar sin avisar: se prueban las dos formas. Sigue
        # sin haber elevacion: si algo exigiera administrador, winget falla y
        # se reporta como pendiente, nunca se pide UAC.
        if ($SoloUserScope) {
            Aviso ($nombre + " no publica un instalador etiquetado como 'user' " +
                   "(codigo " + $codigo + ") y -SoloUserScope prohibe reintentar " +
                   "sin --scope. No se instalo nada.")
            return $false
        }
        # Se ANUNCIA antes de hacerlo. Que el reintento sea razonable no lo hace
        # invisible: quien pego esto leyo "nivel usuario" y tiene derecho a
        # saber que se esta probando la otra forma, y a poder prohibirlo.
        Aviso ($nombre + " no acepto --scope user (codigo " + $codigo + "). Se " +
               "reintenta con el instalador por defecto, que normalmente instala " +
               "en tu perfil y NUNCA pide administrador. Para prohibirlo, vuelve " +
               "a ejecutar con -SoloUserScope.")
        $codigo = WingetIntento $id $false
        if ($exitos -contains $codigo) {
            Refresh-Path
            ComprobarDondeAterrizo $nombre
            return $true
        }

        if ($intento -eq 1) { Start-Sleep -Seconds 4 }
    }
    Aviso ($nombre + " no se pudo instalar con winget (codigo " + $codigo + "). Si tu equipo bloquea winget, pide a TI: " + $id + " a nivel usuario.")
    return $false
}

Write-Host ""
if ($script:Seco) {
    Write-Host "Horizun PBI MCP - EJECUCION EN SECO (-DryRun): no se toca nada" -ForegroundColor White
    Write-Host "-------------------------------------------------------------"
    Write-Host "Se diagnostica la maquina y se publica el plan. Ni una descarga," -ForegroundColor DarkGray
    Write-Host "ni una instalacion, ni un registro, ni un archivo escrito."       -ForegroundColor DarkGray
} else {
    Write-Host "Horizun PBI MCP - instalador de un pegado (sin administrador)" -ForegroundColor White
    Write-Host "-------------------------------------------------------------"
}

# --- 1. Politica de ejecucion del usuario ------------------------------------
Paso "Politica de ejecucion de PowerShell (solo tu usuario)"
# INSTALL-007. Este cambio es PERMANENTE: se queda tras la instalacion y el
# instalador no lo revierte, porque revertirlo dejaria a Claude sin poder
# ejecutar sus propios guiones. Se declara aqui y se documenta como deshacerlo
# en docs/RUNBOOK_INSTALACION.md.
$permisivas = @('RemoteSigned', 'Unrestricted', 'Bypass')
$actual = Get-ExecutionPolicy -Scope CurrentUser
if ($actual -in @('Restricted', 'Undefined', 'AllSigned')) {
    try {
        Efecto -Categoria 'execution-policy' `
               -Descripcion ("Set-ExecutionPolicy -Scope CurrentUser RemoteSigned (actual: " + $actual + ")") `
               -Accion { Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force } | Out-Null
    } catch {
        # No se decide por la excepcion: Set-ExecutionPolicy puede ESCRIBIR el
        # ajuste y lanzar igualmente (pasa cuando un ambito mas especifico, como el
        # -ExecutionPolicy de la propia linea de comandos, manda sobre CurrentUser).
        # Fiarse del error daba un PENDIENTE falso y una instalacion correcta se
        # despedia en amarillo. Se comprueba releyendo, abajo.
        $script:policyError = $_.Exception.Message
    }
}
$efectiva = Get-ExecutionPolicy
$deUsuario = Get-ExecutionPolicy -Scope CurrentUser
if ($efectiva -in $permisivas -or $deUsuario -in $permisivas) {
    Ok ("Los scripts pueden ejecutarse (efectiva: " + $efectiva + "; tu usuario: " + $deUsuario + ").")
} elseif ($script:Seco) {
    # En seco la politica no se cambio a proposito, asi que sigue bloqueando:
    # eso es el estado real de la maquina, no un pendiente que resolver a mano.
    Falta "Politica de ejecucion permisiva para el usuario"
    Write-Host ("  [DIAGNOSTICO] La politica sigue en " + $efectiva + "; el plan la ajustaria.") -ForegroundColor Yellow
} else {
    $detalle = if ($script:policyError) { " (" + $script:policyError + ")" } else { "" }
    Aviso ("La politica de ejecucion sigue bloqueando scripts (efectiva: " + $efectiva + ")" + $detalle + ". Ejecuta: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned")
}

# --- 2. Python real (no el alias de la Store) --------------------------------
Paso "Python 3.10+ real"
$py = PythonReal
if (-not $py) {
    Falta "Python 3.10+ real"
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
} elseif ($script:Seco) {
    Write-Host "  [DIAGNOSTICO] Sin Python real; el plan lo instalaria con winget." -ForegroundColor Yellow
} else {
    Aviso "Sin Python real. Instala Python 3.12 (winget install -e --id Python.Python.3.12 --scope user) y vuelve a pegar este comando."
}

# --- 3. Git (lo exige Claude Code para sesiones locales) ---------------------
Paso "Git"
if (Tiene 'git') { Ok ("git " + ((& git --version) -replace 'git version ', '')) }
else {
    Falta "Git"
    if (InstalarConWinget 'Git.Git' 'Git') {
        if (Tiene 'git') { Ok "Git instalado." } else { Aviso "Git instalado pero el PATH aun no lo ve: cierra y reabre la terminal." }
    } elseif ($script:Seco) {
        Write-Host "  [DIAGNOSTICO] Sin Git; el plan lo instalaria con winget." -ForegroundColor Yellow
    }
}

# --- 4. Node LTS (OPCIONAL: enciende el validador PBIR oficial) --------------
Paso "Node.js LTS (opcional, para el validador oficial de Microsoft)"
if (Tiene 'node') { ReportarNode }
else {
    Falta "Node.js LTS (opcional)"
    # El MSI de Node suele ser por-maquina: sin admin puede fallar, y NO es
    # bloqueante - el MCP funciona sin validador y lo dice honestamente.
    if (-not (InstalarConWinget 'OpenJS.NodeJS.LTS' 'Node.js LTS')) {
        if ($script:Seco) {
            Write-Host "  [DIAGNOSTICO] Sin Node el MCP funciona igual; el validador PBIR queda apagado." -ForegroundColor Yellow
        } else {
            Aviso "Sin Node el MCP funciona igual; el validador PBIR queda apagado hasta instalarlo."
        }
    } elseif (Tiene 'node') { ReportarNode }
}

# --- 5. ChatGPT Desktop ------------------------------------------------------
Paso "Marketplace personal de ChatGPT Desktop"
$registroChatGPT = $null
try {
    $registroChatGPT = Efecto -Categoria 'plugin' `
        -Descripcion "registrar horizun-pbi-mcp en ~/.agents/plugins/marketplace.json" `
        -Simulado ([pscustomobject]@{ Path = '~/.agents/plugins/marketplace.json'; Changed = $true; Backup = $null }) `
        -Accion { RegistrarMarketplaceChatGPT }
    if ($script:Seco) {
        $script:Clientes += "ChatGPT Desktop: se registraria el marketplace personal"
    } elseif ($registroChatGPT.Changed) {
        Ok ("Marketplace de ChatGPT registrado en " + $registroChatGPT.Path)
        if ($registroChatGPT.Backup) { Write-Host ("  Respaldo: " + $registroChatGPT.Backup) }
        $script:Clientes += "ChatGPT Desktop: marketplace registrado"
    } else {
        Ok ("Marketplace de ChatGPT ya estaba registrado en " + $registroChatGPT.Path)
        $script:Clientes += "ChatGPT Desktop: marketplace ya registrado"
    }
} catch {
    FalloVerificacion $_.Exception.Message
    $script:Clientes += "ChatGPT Desktop: registro fallido"
}

# --- 6. Claude Code ----------------------------------------------------------
Paso "Claude Code"
SumarAlPathDeSesion (Join-Path $env:USERPROFILE ".local\bin")
$hayClaude = DetectarClaudeCode
if ($hayClaude) {
    # Arrancar el cliente es un efecto: en seco se declara detectado y no se
    # pregunta la version, porque preguntarla es ejecutar Claude.
    $verClaude = Efecto -Categoria 'cliente' `
                        -Descripcion "claude --version (consultar la version del cliente)" `
                        -Simulado '(version no consultada en seco)' `
                        -Accion { (& claude --version 2>$null | Select-Object -First 1) }
    Ok ("claude " + $verClaude)
    $script:Clientes += "Claude Code: detectado, se registraria el plugin"
} else {
    $script:Clientes += "Claude Code: NO detectado, no habria donde registrar el plugin"
    Falta "Claude Code (requisito externo y opcional)"
    Aviso ("Claude Code no esta instalado. Es un requisito EXTERNO y opcional: " +
           "el MCP queda instalado igual, pero sin registrar en Claude Code. " +
           "Instalalo desde la documentacion oficial de Anthropic " +
           "(https://docs.anthropic.com/en/docs/claude-code) y vuelve a pegar " +
           "este comando para que se registre el plugin.")
}
# Codex y ChatGPT Desktop comparten el mismo marketplace universal/local.
$script:Clientes += "Codex: cubierto por el marketplace personal de ChatGPT"

# --- 7. Registrar el plugin en Claude Code -----------------------------------
Paso "Plugin horizun-pbi-mcp en Claude Code"
if (Tiene 'claude') {
    Efecto -Categoria 'plugin' `
           -Descripcion "claude plugin marketplace add HorizunGroup/horizun-pbi-mcp" `
           -Accion { & claude plugin marketplace add HorizunGroup/horizun-pbi-mcp 2>&1 | Out-Null } | Out-Null
    Efecto -Categoria 'plugin' `
           -Descripcion "claude plugin install horizun-pbi-mcp@horizun" `
           -Accion { & claude plugin install horizun-pbi-mcp@horizun 2>&1 | Out-Null } | Out-Null
    # No basta con que los comandos no fallen: se COMPRUEBA releyendo la lista.
    # Antes se anunciaba "registrado" pase lo que pase, asi que una instalacion
    # muerta se despedia en verde y la persona lo descubria mucho despues.
    $lista = Efecto -Categoria 'plugin' `
                    -Descripcion "claude plugin list (verificar el registro)" `
                    -Simulado '' `
                    -Accion { (& claude plugin list 2>&1 | Out-String) }
    if ($script:Seco) {
        Write-Host "  [DIAGNOSTICO] En seco no se registra ni se verifica nada." -ForegroundColor Yellow
    } else {
        # INSTALL-004. Antes esto era `$lista -match 'horizun-pbi-mcp'`: una
        # coincidencia de subcadena sobre TODA la salida. Un plugin
        # deshabilitado, una version vieja o una linea de error que mencionara
        # el nombre satisfacian el match igual que un plugin sano, y el
        # instalador se despedia en verde sobre una configuracion muerta.
        #
        # Claude imprime el estado en las lineas SIGUIENTES al nombre. Juzgar
        # solo la linea que contiene `horizun-pbi-mcp` aprueba por accidente un
        # bloque cuyo `Status:` dice `disabled`. Se aisla el registro completo,
        # hasta el siguiente plugin, y solo se acepta un estado enabled
        # explicito. Si el formato cambia, no reconocer no es aprobar.
        $lineas = @($lista -split "`r?`n")
        $indice = -1
        for ($i = 0; $i -lt $lineas.Count; $i++) {
            if ($lineas[$i] -match 'horizun-pbi-mcp') { $indice = $i; break }
        }
        if ($indice -lt 0) {
            FalloVerificacion "El plugin NO aparece en 'claude plugin list'. Reintenta con: claude plugin marketplace add HorizunGroup/horizun-pbi-mcp ; claude plugin install horizun-pbi-mcp@horizun"
        } else {
            $fin = $lineas.Count - 1
            for ($j = $indice + 1; $j -lt $lineas.Count; $j++) {
                if ($lineas[$j] -match '^\s*>\s+\S') { $fin = $j - 1; break }
            }
            $registro = (($lineas[$indice..$fin] | ForEach-Object { $_.Trim() }) -join ' ')
            if ($registro -match '(?i)\b(disabled|deshabilitad|inactive|desactivad)\b') {
                FalloVerificacion ("El plugin aparece DESHABILITADO en 'claude plugin list': " +
                                   $registro + ". Habilitalo con: claude plugin enable horizun-pbi-mcp@horizun")
            } elseif ($registro -match '(?i)\berror\b') {
                FalloVerificacion ("El registro del plugin en 'claude plugin list' reporta un error: " +
                                   $registro)
            } elseif ($registro -match '(?i)\benabled\b') {
                Ok ("Plugin registrado y habilitado: " + $registro)
            } else {
                FalloVerificacion ("No se pudo confirmar un estado enabled para el plugin: " +
                                   $registro + ". No reconocer no es aprobar.")
            }
        }
    }
} else {
    if ($script:Seco) {
        Write-Host "  [DIAGNOSTICO] Sin Claude Code no habria donde registrar el plugin." -ForegroundColor Yellow
    } else {
        Aviso "Sin Claude Code no se puede registrar el plugin todavia."
    }
}

# --- 8. Veredicto ------------------------------------------------------------
Write-Host ""
Write-Host "-------------------------------------------------------------"
if ($script:FallosVerificacion.Count -gt 0) {
    exit 1
}

if ($script:Seco) {
    Write-Host "PLAN (ejecucion en seco). Cero efectos: nada se instalo ni se registro." -ForegroundColor Cyan
    Write-Host ""
    Write-Host "PREREQUISITOS DETECTADOS:"
    if ($script:Detectado.Count -eq 0) { Write-Host "  (ninguno)" }
    else { $script:Detectado | ForEach-Object { Write-Host ("  - " + $_) } }

    Write-Host ""
    Write-Host "DEPENDENCIAS FALTANTES:"
    if ($script:Faltante.Count -eq 0) { Write-Host "  (ninguna)" }
    else { $script:Faltante | ForEach-Object { Write-Host ("  - " + $_) } }

    Write-Host ""
    Write-Host "CLIENTES REGISTRABLES:"
    $script:Clientes | ForEach-Object { Write-Host ("  - " + $_) }

    Write-Host ""
    Write-Host ("ACCIONES PREVISTAS (" + $script:Plan.Count + "):")
    if ($script:Plan.Count -eq 0) {
        Write-Host "  (ninguna: la maquina ya cumple todo lo que este instalador cubre)"
    } else {
        $i = 0
        foreach ($p in $script:Plan) {
            $i++
            Write-Host ("  " + $i + ". [" + $p.Categoria + "] " + $p.Accion)
        }
    }

    Write-Host ""
    Write-Host ("RESULTADO DEL PLAN: construido con " + $script:Plan.Count + " accion(es) previstas y " +
                $script:Faltante.Count + " dependencia(s) faltante(s).")
    Write-Host "Una dependencia faltante NO es un fallo del plan: el plan describe como resolverla."
    Write-Host "Para ejecutarlo de verdad: powershell -NoProfile -File scripts/instalar.ps1"
    Write-Host "-------------------------------------------------------------"
    exit 0
}

if ($script:Pendientes.Count -eq 0) {
    Write-Host "LISTO. Pasos restantes:" -ForegroundColor Green
    Write-Host "  1. ChatGPT Desktop: reinicia, abre Plugins, fuente Personal, e instala Horizun PBI MCP."
    Write-Host "  2. Claude Desktop gratuito: instala el asset .mcpb de la release con doble clic."
    Write-Host "  3. Claude Code: abre (o reinicia); la primera sesion prepara el runtime SOLA."
    Write-Host "     (Si es la primera vez en este equipo, Claude te pedira iniciar sesion:"
    Write-Host "      eso es normal, no es un fallo de la instalacion.)"
    Write-Host "  4. Escribe 'pbi_install_status' si quieres ver el avance (tarda ~1-2 min)."
    Write-Host "  5. Cuando diga 'Runtime listo', reinicia el cliente una vez: apareceran las tools pbi_*."
} else {
    Write-Host ("QUEDARON " + $script:Pendientes.Count + " PENDIENTE(S):") -ForegroundColor Yellow
    $script:Pendientes | ForEach-Object { Write-Host ("  - " + $_) }
    Write-Host "  Resuelvelos (o pide a TI lo marcado) y vuelve a pegar este mismo comando: es seguro repetirlo."
}
Write-Host "-------------------------------------------------------------"

# SIG # Begin signature block
# MIIoZQYJKoZIhvcNAQcCoIIoVjCCKFICAQExDzANBglghkgBZQMEAgEFADB5Bgor
# BgEEAYI3AgEEoGswaTA0BgorBgEEAYI3AgEeMCYCAwEAAAQQH8w7YFlLCE63JNLG
# KX7zUQIBAAIBAAIBAAIBAAIBADAxMA0GCWCGSAFlAwQCAQUABCBmANAw7Z0adgAN
# 33oiAShJg/j3YjTGKmrOAGBUDzMr46CCDQ8wggZOMIIENqADAgECAhBrLj7aPHNf
# QyzMrEnkorNcMA0GCSqGSIb3DQEBCwUAMFYxCzAJBgNVBAYTAlBMMSEwHwYDVQQK
# ExhBc3NlY28gRGF0YSBTeXN0ZW1zIFMuQS4xJDAiBgNVBAMTG0NlcnR1bSBDb2Rl
# IFNpZ25pbmcgMjAyMSBDQTAeFw0yNjEwMTkxMTU4MDdaFw0yNzEwMTkxMTU4MDZa
# MHQxCzAJBgNVBAYTAkNPMRgwFgYDVQQIDA9WYWxsZSBkZWwgQ2F1Y2ExDTALBgNV
# BAcMBENhbGkxHTAbBgNVBAoMFEhPUklaVU4gR1JPVVAgUy5BLlMuMR0wGwYDVQQD
# DBRIT1JJWlVOIEdST1VQIFMuQS5TLjCCAaIwDQYJKoZIhvcNAQEBBQADggGPADCC
# AYoCggGBAMMfir6ZfLjQSxeLCA6DggiUlNQ9qq0adiNtupf5rjcldCCJ6FUHWQRn
# 6tD9jDWicNj3x6QjmtKP6iJFi6S7pI7mOGxckP4y4WvUupgZpDWFAEofLOczmo/W
# JdS43qncuBihGTR6P0TIF1RDLO7EQ/b6QNTmfKGIMFWZd1gV+tptfL9L8NFXcu7a
# Fv8RmYUs0iDNi7Jvwluxijly5mPObUAsWL+oQV9AUwDwyIn3zIltD03Y1XG5H5Nf
# n5lDd9OsAaA1Cy/9/O+rL1A9X+eLg2vssKD/0daNALES/0m3Do/Z1n+KTU5TDgyu
# 5SqsIdzi10IdZfpCZlghxla2BsknnAc1ai9X8AMgdFP/0SOiuf8ePtWYvVCeAjC5
# 8jUAy3pIdP0F6CS+pe0fuZIiwPikb9txLGhrl/kNRhsuyEn9u1r1YJI2Q1ShbQeZ
# MbiJpNBm5DqtprjQLLEwe0zWRDj7x+oUQti9ghD61oKn/ROsO929MA5K9Q17+znV
# zimy4lR3pQIDAQABo4IBeDCCAXQwDAYDVR0TAQH/BAIwADA9BgNVHR8ENjA0MDKg
# MKAuhixodHRwOi8vY2NzY2EyMDIxLmNybC5jZXJ0dW0ucGwvY2NzY2EyMDIxLmNy
# bDBzBggrBgEFBQcBAQRnMGUwLAYIKwYBBQUHMAGGIGh0dHA6Ly9jY3NjYTIwMjEu
# b2NzcC1jZXJ0dW0uY29tMDUGCCsGAQUFBzAChilodHRwOi8vcmVwb3NpdG9yeS5j
# ZXJ0dW0ucGwvY2NzY2EyMDIxLmNlcjAfBgNVHSMEGDAWgBTddF1MANt7n6B0yrFu
# 9zzAMsBwzTAdBgNVHQ4EFgQUFcFIHokKwMEzDuspbooMIGvqXfwwSwYDVR0gBEQw
# QjAIBgZngQwBBAEwNgYLKoRoAYb2dwIFAQQwJzAlBggrBgEFBQcCARYZaHR0cHM6
# Ly93d3cuY2VydHVtLnBsL0NQUzATBgNVHSUEDDAKBggrBgEFBQcDAzAOBgNVHQ8B
# Af8EBAMCB4AwDQYJKoZIhvcNAQELBQADggIBADU5UA3OBCazNWvPipsNpRepq/vC
# 64ePU5rEF2HlVWTzWLg19nOOkmSlkIGC/L0du4W9ZaBopy7XadY8tLVTndu4/yoi
# pGS6HFclsMdgfD16IdkEg2wC4CE3UTASM5yChs0o1ViYF4wMD/PBqZ+aL+ZWeBKK
# Y8nQyJ6vdFV+nE/DRgDRtR3zaV+fkR6PY44Vd8kq0wHRmXu0ODmHrUpnk7n6Q5AE
# w2T029DjjoiNumfmcQLvn+xB580nwWbCvfXjrZsfeAK2aNkHIdaTjgOTPUXUjyhV
# VeXAutiLJP0ECS7G4/wsi3XKPt2MI1qVctzYSkzmSA0ocnvIWNyz6KZe75mfo+Fs
# RMPWJePDdIoGQQAOtD6eh/+vwd6cQvQEMi3UsKzyuqpY2flqOfhW5eyyWX/vqtUG
# DDcZrmg0aoiD/PGPSGhOrpagqB3SRNsC8JC5urSAt5IxWOJpbu4nz3/6IEJkcqwA
# 6vHCMa5dvkLC6CKaJnDP+RZcDbkU6usOfnqckQPddpjSE/jSl/UslrLaNbYR/648
# DliiTZig76myWWkw1XG4mwpaeW60HTfBNEuehHpX+3HBmhECwuW8wGGyqt5ticnq
# Q30ZAw1zfnrBJahQTuyAZS6zYqjKKjo/qNz6xDVWHathP4+rPvf9vqdu/4m7n1yk
# Yv7vu/T7jWJfulQpMIIGuTCCBKGgAwIBAgIRAJmjgAomVTtlq9xuhKaz6jkwDQYJ
# KoZIhvcNAQEMBQAwgYAxCzAJBgNVBAYTAlBMMSIwIAYDVQQKExlVbml6ZXRvIFRl
# Y2hub2xvZ2llcyBTLkEuMScwJQYDVQQLEx5DZXJ0dW0gQ2VydGlmaWNhdGlvbiBB
# dXRob3JpdHkxJDAiBgNVBAMTG0NlcnR1bSBUcnVzdGVkIE5ldHdvcmsgQ0EgMjAe
# Fw0yMTA1MTkwNTMyMThaFw0zNjA1MTgwNTMyMThaMFYxCzAJBgNVBAYTAlBMMSEw
# HwYDVQQKExhBc3NlY28gRGF0YSBTeXN0ZW1zIFMuQS4xJDAiBgNVBAMTG0NlcnR1
# bSBDb2RlIFNpZ25pbmcgMjAyMSBDQTCCAiIwDQYJKoZIhvcNAQEBBQADggIPADCC
# AgoCggIBAJ0jzwQwIzvBRiznM3M+Y116dbq+XE26vest+L7k5n5TeJkgH4Cyk74I
# L9uP61olRsxsU/WBAElTMNQI/HsE0uCJ3VPLO1UufnY0qDHG7yCnJOvoSNbIbMpT
# +Cci75scCx7UsKK1fcJo4TXetu4du2vEXa09Tx/bndCBfp47zJNsamzUyD7J1rcN
# xOw5g6FJg0ImIv7nCeNn3B6gZG28WAwe0mDqLrvU49chyKIc7gvCjan3GH+2eP4m
# YJASflBTQ3HOs6JGdriSMVoD1lzBJobtYDF4L/GhlLEXWgrVQ9m0pW37KuwYqpY4
# 2grp/kSYE4BUQrbLgBMNKRvfhQPskDfZ/5GbTCyvlqPN+0OEDmYGKlVkOMenDO/x
# tMrMINRJS5SY+jWCi8PRHAVxO0xdx8m2bWL4/ZQ1dp0/JhUpHEpABMc3eKax8GI1
# F03mSJVV6o/nmmKqDE6TK34eTAgDiBuZJzeEPyR7rq30yOVw2DvetlmWssewAhX+
# cnSaaBKMEj9O2GgYkPJ16Q5Da1APYO6n/6wpCm1qUOW6Ln1J6tVImDyAB5Xs3+Jr
# iasaiJ7P5KpXeiVV/HIsW3ej85A6cGaOEpQA2gotiUqZSkoQUjQ9+hPxDVb/Lqz0
# tMjp6RuLSKARsVQgETwoNQZ8jCeKwSQHDkpwFndfCceZ/OfCUqjxAgMBAAGjggFV
# MIIBUTAPBgNVHRMBAf8EBTADAQH/MB0GA1UdDgQWBBTddF1MANt7n6B0yrFu9zzA
# MsBwzTAfBgNVHSMEGDAWgBS2oVQ5AsOgP46KvPrU+Bym0ToO/TAOBgNVHQ8BAf8E
# BAMCAQYwEwYDVR0lBAwwCgYIKwYBBQUHAwMwMAYDVR0fBCkwJzAloCOgIYYfaHR0
# cDovL2NybC5jZXJ0dW0ucGwvY3RuY2EyLmNybDBsBggrBgEFBQcBAQRgMF4wKAYI
# KwYBBQUHMAGGHGh0dHA6Ly9zdWJjYS5vY3NwLWNlcnR1bS5jb20wMgYIKwYBBQUH
# MAKGJmh0dHA6Ly9yZXBvc2l0b3J5LmNlcnR1bS5wbC9jdG5jYTIuY2VyMDkGA1Ud
# IAQyMDAwLgYEVR0gADAmMCQGCCsGAQUFBwIBFhhodHRwOi8vd3d3LmNlcnR1bS5w
# bC9DUFMwDQYJKoZIhvcNAQEMBQADggIBAHWIWA/lj1AomlOfEOxD/PQ7bcmahmJ9
# l0Q4SZC+j/v09CD2csX8Yl7pmJQETIMEcy0VErSZePdC/eAvSxhd7488x/Cat4ke
# +AUZZDtfCd8yHZgikGuS8mePCHyAiU2VSXgoQ1MrkMuqxg8S1FALDtHqnizYS1bI
# MOv8znyJjZQESp9RT+6NH024/IqTRsRwSLrYkbFq4VjNn/KV3Xd8dpmyQiirZdrO
# NoPSlCRxCIi54vQcqKiFLpeBm5S0IoDtLoIe21kSw5tAnWPazS6sgN2oXvFpcVVp
# Mcq0C4x/CLSNe0XckmmGsl9z4UUguAJtf+5gE8GVsEg/ge3jHGTYaZ/MyfujE8hO
# mKBAUkVa7NMxRSB1EdPFpNIpEn/pSHuSL+kWN/2xQBJaDFPr1AX0qLgkXmcEi6PF
# naw5T17UdIInA58rTu3mefNuzUtse4AgYmxEmJDodf8NbVcU6VdjWtz0e58WFZT7
# tST6EWQmx/OoHPelE77lojq7lpsjhDCzhhp4kfsfszxf9g2hoCtltXhCX6NqsqwT
# T7xe8LgMkH4hVy8L1h2pqGLT2aNCx7h/F95/QvsTeGGjY7dssMzq/rSshFQKLZ8l
# Pb8hFTmiGDJNyHga5hZ59IGynk08mHhBFM/0MLeBzlAQq1utNjQprztZ5vv/NJy8
# ua9AGbwkMWkOMYIarDCCGqgCAQEwajBWMQswCQYDVQQGEwJQTDEhMB8GA1UEChMY
# QXNzZWNvIERhdGEgU3lzdGVtcyBTLkEuMSQwIgYDVQQDExtDZXJ0dW0gQ29kZSBT
# aWduaW5nIDIwMjEgQ0ECEGsuPto8c19DLMysSeSis1wwDQYJYIZIAWUDBAIBBQCg
# fDAQBgorBgEEAYI3AgEMMQIwADAZBgkqhkiG9w0BCQMxDAYKKwYBBAGCNwIBBDAc
# BgorBgEEAYI3AgELMQ4wDAYKKwYBBAGCNwIBFTAvBgkqhkiG9w0BCQQxIgQgD+7Q
# NUF1Heije9jVsS58L2WpIUgG5xaPIVOgXhpT9RcwDQYJKoZIhvcNAQEBBQAEggGA
# lch4qkpYfz1oIHKGKbCSdlVr4RNpeaTXsQb9mmqdjBVN+94Rcg8uxp2BQhGyBYnX
# bOn3agK0kJqAsNhVLBHh64ahs0BaEWAgU1UzwE1cdUde0Jq6ifI4JIQfzex7hzdM
# T/7Q5Y2pT9Aw41bl69LAQ0Oq3KVhQd0bUPo8KqVzO5OXKp/83SM4nCSp8pxfFLF9
# Q1NCGSIyFQDtXB12tvt/70iY8OIabPGLFp2A5oya1XumKcZG4mhm96Ptp5rZDY2o
# r6bwZFrJ16PN1nzgpTHjwfrAyaaaaARJ5zVJwA2UBzKlclx0XTH4ueBKqrlt0wr/
# M6CYqgrH2/IF1BiP81a1ncDKo0EIkBC1g4A23whUHM85ErGXRRheU5iIVOB8OEN4
# Mca35lQ5TUU6FF/8lCHPOxaxm4fU6W2DY/6GYJYGTpjvqgUFkAVSbrigkp5giXMm
# gZ7/G5ZrUim82G9zgdeWSJVvu4wvqCfb/c3g3Ubz7dBNbQ/3uh1B+OU/nPU4bshz
# oYIYFTCCGBEGCisGAQQBgjcDAwExghgBMIIX/QYJKoZIhvcNAQcCoIIX7jCCF+oC
# AQMxDTALBglghkgBZQMEAgIwgc4GCyqGSIb3DQEJEAEEoIG+BIG7MIG4AgEBBgsq
# hGgBhvZ3AgUBCzAxMA0GCWCGSAFlAwQCAQUABCCSLZAkyc1uE3aMh8OXtZcB2y61
# mnsAeYjEwTD+ZznHEwIHCqh8daI3fhgPMjAyNjExMDMyMjQ1MDNaMAMCAQGgVKRS
# MFAxCzAJBgNVBAYTAlBMMSEwHwYDVQQKDBhBc3NlY28gRGF0YSBTeXN0ZW1zIFMu
# QS4xHjAcBgNVBAMMFUNlcnR1bSBUaW1lc3RhbXAgMjAyNqCCExAwggaCMIIEaqAD
# AgECAhAo8HfBHDa9/l90MkdwJy4DMA0GCSqGSIb3DQEBDAUAMFYxCzAJBgNVBAYT
# AlBMMSEwHwYDVQQKExhBc3NlY28gRGF0YSBTeXN0ZW1zIFMuQS4xJDAiBgNVBAMT
# G0NlcnR1bSBUaW1lc3RhbXBpbmcgMjAyMSBDQTAeFw0yNjAzMTEwNzM0NTRaFw0z
# NjAyMjcwNzM0NTRaMFAxCzAJBgNVBAYTAlBMMSEwHwYDVQQKDBhBc3NlY28gRGF0
# YSBTeXN0ZW1zIFMuQS4xHjAcBgNVBAMMFUNlcnR1bSBUaW1lc3RhbXAgMjAyNjCC
# AiIwDQYJKoZIhvcNAQEBBQADggIPADCCAgoCggIBALi9zfBw+xI12hi2F9uNQBNO
# STkTIi13fuh3DiWybI5392bBfJw6tO7zDs7UqP/rejVf4xFTU3XNOPsuOFkP5SPF
# snWwn3VJL+NhNhsuujvYsQtaQnvNo1wk46reTgws1L8OFEdKaxgyqT4zOQx81mLw
# NZSqXNU9Rrb9oAJWJF4Mydz+C1ebu0D0+vgM3tqAQ8TU203fqgKCPqIdHloaFqH9
# eyR+8tnpmy2hnnN59ZOtmsuHeRwf5iTOhlhf0d/qqquLOSIGPgGVL1Hj5XBUox2O
# E7tQRr+l/xxNbL6OuwBO4Aa2EbuxYg37cpXv5i2/lTL4NASB/duf9W4fjA3Ro+GP
# +bC54n2OYiM3Cq8z92ejx8F5HWO2e4/75eC95B9tMw/eq7109GDyW2+2ut5Bn0o8
# MXJTERkyystWbqcnUkomA97qfiDCwvpWuOMdfTOksXe9S+xDxOJaCbiyTivgPulx
# cT+tfQTRXBr5UwtPvc+INnafOEilzVHA11tv+pHcylryxBc08cUihf/ttqkZS0bm
# x3VxQay8uohaTp0//ECA9vYGW51SKdJMJhXvKyIdBLy8WYNchTt8yRITv9Adxg9J
# qGujh+TnP1bW3aQjFtlEl7SzgNeI8OVA/CJAWWUuRT3Au9ToOpHy/flrrknhN4he
# Ew7/6ILMv4HSsj0YZ4otAgMBAAGjggFQMIIBTDB1BggrBgEFBQcBAQRpMGcwOwYI
# KwYBBQUHMAKGL2h0dHA6Ly9zdWJjYS5yZXBvc2l0b3J5LmNlcnR1bS5wbC9jdHNj
# YTIwMjEuY2VyMCgGCCsGAQUFBzABhhxodHRwOi8vc3ViY2Eub2NzcC1jZXJ0dW0u
# Y29tMB8GA1UdIwQYMBaAFL5UAi+/QGxzQ86sCSVOnkNEGu7gMAwGA1UdEwEB/wQC
# MAAwOQYDVR0fBDIwMDAuoCygKoYoaHR0cDovL3N1YmNhLmNybC5jZXJ0dW0ucGwv
# Y3RzY2EyMDIxLmNybDAWBgNVHSUBAf8EDDAKBggrBgEFBQcDCDAOBgNVHQ8BAf8E
# BAMCB4AwIgYDVR0gBBswGTAIBgZngQwBBAIwDQYLKoRoAYb2dwIFAQswHQYDVR0O
# BBYEFCM5aiirmhKnpKfyH/lFmr+N/oIDMA0GCSqGSIb3DQEBDAUAA4ICAQBm/pOb
# rAveTqYFPCSthxNVwBHTW4TfuBQY2KGCdMlH6ALjVPYf2XARNGMTXScR0JQ2/c2L
# xLpoHHhRwxFf3d933DhFS165rYj6iPSQKm41rc9AMpTCjMdmai1aK9o870pOzTzL
# RhWFVIxm+5Qf+t4V5z14tPoUsAWdfRKD1Zzvzfe5AHlE9rxOZcGROiULxs3Edf7Z
# J8RZI0jzD9juj2oEX2XS7BVKuNCK1W0h1mjapv028yqo8UtZh5/1Ib48Yyp88BDY
# Shh5PSGUUuS5tYXVfcduIPBe3PdNTYRYdT5VNxkwLgjMzIcr/6SH2fejFJDjnCxj
# vXLuarErJg/IdI+hXdGIBDVOGv1zrkF4ktoQvFZIp5DIAYh0GwyUhc87qqn5UaYT
# j2+iyiBDSNh7aGCrkuAnqg7Qh7frhX+QhMGeJ9SpLpnG76qN85hLyjvYR6gRYiLT
# j8G4fxjqV400/buPw0rBo9yJZyq/8cGdCOLPOYf9qpefTcrZKeO9pg+UJHx8UABb
# aTqizRxm8sUSEycccCCFhIit3AI/3wGuSOADeiA676NcQ7+dsP04YvzvdN7pTZNy
# g672WpBDJdg0eItHl4yomvNeIOWh1qs4QqOW6TI8Ocsi4uSDTLorxLUDnBTwKQzz
# pxd1YUctEgRh32R0J9FkFkeEruJU94RljFr1uDCCBrkwggShoAMCAQICEQDn/2nH
# OzXOS5Em2HR8aKWHMA0GCSqGSIb3DQEBDAUAMIGAMQswCQYDVQQGEwJQTDEiMCAG
# A1UEChMZVW5pemV0byBUZWNobm9sb2dpZXMgUy5BLjEnMCUGA1UECxMeQ2VydHVt
# IENlcnRpZmljYXRpb24gQXV0aG9yaXR5MSQwIgYDVQQDExtDZXJ0dW0gVHJ1c3Rl
# ZCBOZXR3b3JrIENBIDIwHhcNMjEwNTE5MDUzMjA3WhcNMzYwNTE4MDUzMjA3WjBW
# MQswCQYDVQQGEwJQTDEhMB8GA1UEChMYQXNzZWNvIERhdGEgU3lzdGVtcyBTLkEu
# MSQwIgYDVQQDExtDZXJ0dW0gVGltZXN0YW1waW5nIDIwMjEgQ0EwggIiMA0GCSqG
# SIb3DQEBAQUAA4ICDwAwggIKAoICAQDpEh8ENe25XXrFppVBvoplf0530W0lddNm
# jtv4YSh/f7eDQKFaIqc7tHj7ox+u8vIsJZlroakUeMS3i3T8aJRC+eQs4FF0Gqvk
# M6+WZO8kmzZfxmZaBYmMLs8FktgFYCzywmXeQ1fEExflee2OpbHVk665eXRHjH7M
# YZIzNnjl2m8Hy8ulB9mR8wL/W0v0pjKNT6G0sfrx1kk+3OGosFUb7yWNnVkWKU4q
# SxLv16kJ6oVJ4BSbZ4xMak6JLeB8szrK9vwGDpvGDnKCUMYL3NuviwH1x4gZG0JA
# XU3x2pOAz91JWKJSAmRy/l0s0l5bEYKolg+DMqVhlOANd8Yh5mkQWaMEvBRE/kAG
# zIqgWhwzN2OsKIVtO8mf5sPWSrvyplSABAYa13rMYnzwfg08nljZHghquCJYCa/x
# HK9acev9UD7Y+usr15d7mrszzxhF1JOr1Mpup2chNSBlyOObhlSO16rwrffVrg/S
# zaKfSndS5swRhr8bnDqNJY9TNyEYvBYpgF95K7p0g4LguR4A++Z1nFIHWVY5v0fN
# VZmgzxD9uVo/gta3onGOQj3JCxgYx0KrCXu4yc9QiVwTFLWbNdHFSjBCt5/8Q9pL
# uRhVocdCunhcHudMS1CGQ/Rn0+7P+fzMgWdRKfEOh/hjLrnQ8BdJiYrZNxvIOhM2
# aa3zEDHNwwIDAQABo4IBVTCCAVEwDwYDVR0TAQH/BAUwAwEB/zAdBgNVHQ4EFgQU
# vlQCL79AbHNDzqwJJU6eQ0Qa7uAwHwYDVR0jBBgwFoAUtqFUOQLDoD+Oirz61Pgc
# ptE6Dv0wDgYDVR0PAQH/BAQDAgEGMBMGA1UdJQQMMAoGCCsGAQUFBwMIMDAGA1Ud
# HwQpMCcwJaAjoCGGH2h0dHA6Ly9jcmwuY2VydHVtLnBsL2N0bmNhMi5jcmwwbAYI
# KwYBBQUHAQEEYDBeMCgGCCsGAQUFBzABhhxodHRwOi8vc3ViY2Eub2NzcC1jZXJ0
# dW0uY29tMDIGCCsGAQUFBzAChiZodHRwOi8vcmVwb3NpdG9yeS5jZXJ0dW0ucGwv
# Y3RuY2EyLmNlcjA5BgNVHSAEMjAwMC4GBFUdIAAwJjAkBggrBgEFBQcCARYYaHR0
# cDovL3d3dy5jZXJ0dW0ucGwvQ1BTMA0GCSqGSIb3DQEBDAUAA4ICAQC4k1l3yUwV
# /ZQHCKCneqAs8EGTnwEUJLdDpokN/dMhKjK0rR5qX8nIIHzxpQR3TAw2IRw1Uxsr
# 2PliG3bCFqSdQTUbfaTq6V3vBzEebDru9QFjqlKnxCF2h1jhLNFFplbPJiW+JSnJ
# Th1fKEqEdKdxgl9rVTvlxfEJ7exOn25MGbd/wGPwuSmMxRJVO0wnqgS7kmoJjNF9
# zqeehFSDDP8ZVkWg4EZ2tIS0M3uZmByRr+1Lkwjjt8AtW83mVnZTyTsOb+FNfwJY
# 7DS4FmWhkRbgcHRetreoTirPOr/ozyDKhT8MTSTf6Lttg6s6T/u08mDWw6HK04ZR
# DfQ9sb77QV8mKgO44WGP31vXnVKoWVJpFBjPvjL8/Zck/5wXX2iqjOaLStFOR/IQ
# ki+Ehn4zlcgVm22ZVCBPF+l8nAwUUShCtKuSU7GmZLKCmmxQMkSiWILTm8EtVD6A
# xnJhoq8EnhjEEyUoflkeRF2WhFiVQOmWTwZRr44IxWGkNJC6tTorW5rl2Zl+2e9J
# LPYf3pStAPMDoPKIjVXd6NW2+fZrNUBeDo2eOa5Fn7Brs/HLQff5Xgris5MeUbdV
# gDrF8uxO6cLPvZPo63j62SsNg55pTWk9fUIF9iPoRbb4QurjoY/woI1RAOKtYtTi
# c6aAJq3u83RIPpGXBSJKwx4KJAOZnCDCtTCCBckwggSxoAMCAQICEBu1jyUq3yMA
# SSjJrj1+7ScwDQYJKoZIhvcNAQEMBQAwfjELMAkGA1UEBhMCUEwxIjAgBgNVBAoT
# GVVuaXpldG8gVGVjaG5vbG9naWVzIFMuQS4xJzAlBgNVBAsTHkNlcnR1bSBDZXJ0
# aWZpY2F0aW9uIEF1dGhvcml0eTEiMCAGA1UEAxMZQ2VydHVtIFRydXN0ZWQgTmV0
# d29yayBDQTAeFw0yMTA1MzEwNjQzMDZaFw0yOTA5MTcwNjQzMDZaMIGAMQswCQYD
# VQQGEwJQTDEiMCAGA1UEChMZVW5pemV0byBUZWNobm9sb2dpZXMgUy5BLjEnMCUG
# A1UECxMeQ2VydHVtIENlcnRpZmljYXRpb24gQXV0aG9yaXR5MSQwIgYDVQQDExtD
# ZXJ0dW0gVHJ1c3RlZCBOZXR3b3JrIENBIDIwggIiMA0GCSqGSIb3DQEBAQUAA4IC
# DwAwggIKAoICAQC9+Xj45tWADGSdhhuWZGc/IjoedQF97/tcZ4zJzFxrqZHmuULl
# IEub2pt7uZld2ZuAS9eEQCsn0+i6MLs+CRqnSZXvK0AkwpfHp+6bJe+oCgCXhVqq
# ndwpyeI1B+twTUrWwbNWuKFBOJvR+zF/j+Bf4bE/D44WSWDXBo0Y+aomEKsq09DR
# Z40bRr5HMNUuctHFY9rnY3lEfktjJImGLjQ/KUxSiyqnwOKRKIm5wFv5HdnnJ63/
# mgKXwcZQkpsCLL2puTRZCr+ESv/f/rOf69me4Jgj7KZrdxYq28ytOxykh9xGc14Z
# YmhFV+SQgkK7QtbwYeDBoz1mo130GO6IyY0XRSmZMnUCMe4pJshrAua1YkV/NxVa
# I2iJ1D7eTiew8EAMvE0Xy02isx7QBlrd9pPPV3WZ9fqGGmd4s7+W/jTcvedSVuWz
# 5XV710GRBdxdaeOVDUO5/IOWOZV7bIBaTxNyxtd9KXpEulKkKtVBRgkg/iKgtlsw
# jbyJDNXXcPiHUv3a76xRLgezTv7QCdpw75j6VuZt27VXS9zlLCUVyJ4ueE742pye
# hizKV/Ma5ciSixqClnrDvFASadgOWkaLOusm+iPJtrCBvkIApPjW/jAux9JG9uWO
# df3yzLnQh1vMBhBgu4M1t15n3kfsmUjxpKEV/q2MYo45VU85FrmxY53/twIDAQAB
# o4IBPjCCATowDwYDVR0TAQH/BAUwAwEB/zAdBgNVHQ4EFgQUtqFUOQLDoD+Oirz6
# 1PgcptE6Dv0wHwYDVR0jBBgwFoAUCHbNywf/JPbFze27kLzihDdGdfcwDgYDVR0P
# AQH/BAQDAgEGMC8GA1UdHwQoMCYwJKAioCCGHmh0dHA6Ly9jcmwuY2VydHVtLnBs
# L2N0bmNhLmNybDBrBggrBgEFBQcBAQRfMF0wKAYIKwYBBQUHMAGGHGh0dHA6Ly9z
# dWJjYS5vY3NwLWNlcnR1bS5jb20wMQYIKwYBBQUHMAKGJWh0dHA6Ly9yZXBvc2l0
# b3J5LmNlcnR1bS5wbC9jdG5jYS5jZXIwOQYDVR0gBDIwMDAuBgRVHSAAMCYwJAYI
# KwYBBQUHAgEWGGh0dHA6Ly93d3cuY2VydHVtLnBsL0NQUzANBgkqhkiG9w0BAQwF
# AAOCAQEAUcKhWBYiEIv8hjHUwjLTXPhLoi9qPk34qwge7dXo+ucIDey3xh4OsnUF
# erEL+td0agAGH6TOqvJrfdgRwBEI1A5NYcU6qPqx4eOY6uJj/UH1k30NvF2SO5Ga
# ZbrQX0y+TI1oAxAW1fHQBIqFM/k351suJmlCPLrNvNR66U2d2udD+2NSgIUExCWV
# VfB+p58BxVso+ODOhnCmJzs5+zgumFNlInUt1/bdIiKRluvdaO0/oxmX0zsliOw2
# Ej2pfs4CjQWakqcnbkWynGKGNw2kM47XqE7QShdDTMXxp2zSHlhsFFF+ilSh9u+f
# eXpdgZoWkA/hu/FKAPn53b7tquzOCjGCA+8wggPrAgEBMGowVjELMAkGA1UEBhMC
# UEwxITAfBgNVBAoTGEFzc2VjbyBEYXRhIFN5c3RlbXMgUy5BLjEkMCIGA1UEAxMb
# Q2VydHVtIFRpbWVzdGFtcGluZyAyMDIxIENBAhAo8HfBHDa9/l90MkdwJy4DMA0G
# CWCGSAFlAwQCAgUAoIIBVjAaBgkqhkiG9w0BCQMxDQYLKoZIhvcNAQkQAQQwHAYJ
# KoZIhvcNAQkFMQ8XDTI2MTEwMzIyNDUwM1owNwYLKoZIhvcNAQkQAi8xKDAmMCQw
# IgQghb6Q4QrSQ418ySi2r0iwmrIIF3zs+LASbFjTkQUlxDwwPwYJKoZIhvcNAQkE
# MTIEMHZNnyPmooK05rJI/6K0o4vF4F4sqt2CVOdegZ3NfMPZ0Yv5BHzuLDrNzmtY
# jaJKfDCBnwYLKoZIhvcNAQkQAgwxgY8wgYwwgYkwgYYEFFcUaEEMqFrzQk75FkpR
# NhD0042YMG4wWqRYMFYxCzAJBgNVBAYTAlBMMSEwHwYDVQQKExhBc3NlY28gRGF0
# YSBTeXN0ZW1zIFMuQS4xJDAiBgNVBAMTG0NlcnR1bSBUaW1lc3RhbXBpbmcgMjAy
# MSBDQQIQKPB3wRw2vf5fdDJHcCcuAzANBgkqhkiG9w0BAQEFAASCAgB9zNn6myoZ
# irHEgLsUtp59vb/KIE2XeBV3OM52aAxiBjbLx/WSLbx4NqiyVR4DBajOqFIyd+eU
# bj8NcQryq65sF+S1MsrFT/azoonn1tNA6Jfc0WE3yanqt/RyscTB6+SCu3uSr8Uz
# 3ZmCW/Fc/HuLLY9Vr0BdeEWVkLv3miYd12TJhjfXBaNusD1cX/WcqXKpmwZ2Uwqv
# ubs9DVAmdHO3C51pAwXFslp3FIXXC39E5WzP0KUKm5GlqOg6Zmx/LaSd/SohTfCI
# zntLlrPfSeq7DCVCVSH0Xi2HvEoaODauGzZfFZTXdV6ZXZTVuaj8L+hv2tyGQM/q
# i54WHolSvHml0jUkZtkfad4I+3qv/7jWLQLvpabdlNUAYpfGD02tf3mM1Ep0Cr8I
# M/CPkWCnv3TEqINTiPxvyp/lvkNdtVNIJpkdn0NL/HQ6PQQq3aKRs2pPTX8kedEv
# JSLYQ9Tw9Mw7JQfSTvbscKvQzt32k6XD5JgyILBXlTEed1ce49b1dZn94mttCfly
# zfzF36TOWUS65l8rz9olGUFqEomYd8Xi7r6rytfRsUEpZfXm+ES5TYRMGzaOu55J
# EjNUEzq08SBtYzY7FUV4OdnCeKxNtLD4idmA5STfcfG9faMJB94DJ649WjsJNAC4
# T1NB5xS5+QfC5yCXHvMDvIrF5NSUX8VM1Q==
# SIG # End signature block
