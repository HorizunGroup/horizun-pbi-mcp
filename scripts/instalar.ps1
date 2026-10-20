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
            $a.source.ref -eq 'v2.1.3' -and
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
            ref = 'v2.1.3'
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
# MIInawYJKoZIhvcNAQcCoIInXDCCJ1gCAQExDzANBglghkgBZQMEAgEFADB5Bgor
# BgEEAYI3AgEEoGswaTA0BgorBgEEAYI3AgEeMCYCAwEAAAQQH8w7YFlLCE63JNLG
# KX7zUQIBAAIBAAIBAAIBAAIBADAxMA0GCWCGSAFlAwQCAQUABCDEsmDLwT/1hW4O
# aGFNvJ7g14pnajcXk46breI/PQO33aCCIB8wggXJMIIEsaADAgECAhAbtY8lKt8j
# AEkoya49fu0nMA0GCSqGSIb3DQEBDAUAMH4xCzAJBgNVBAYTAlBMMSIwIAYDVQQK
# ExlVbml6ZXRvIFRlY2hub2xvZ2llcyBTLkEuMScwJQYDVQQLEx5DZXJ0dW0gQ2Vy
# dGlmaWNhdGlvbiBBdXRob3JpdHkxIjAgBgNVBAMTGUNlcnR1bSBUcnVzdGVkIE5l
# dHdvcmsgQ0EwHhcNMjEwNTMxMDY0MzA2WhcNMjkwOTE3MDY0MzA2WjCBgDELMAkG
# A1UEBhMCUEwxIjAgBgNVBAoTGVVuaXpldG8gVGVjaG5vbG9naWVzIFMuQS4xJzAl
# BgNVBAsTHkNlcnR1bSBDZXJ0aWZpY2F0aW9uIEF1dGhvcml0eTEkMCIGA1UEAxMb
# Q2VydHVtIFRydXN0ZWQgTmV0d29yayBDQSAyMIICIjANBgkqhkiG9w0BAQEFAAOC
# Ag8AMIICCgKCAgEAvfl4+ObVgAxknYYblmRnPyI6HnUBfe/7XGeMycxca6mR5rlC
# 5SBLm9qbe7mZXdmbgEvXhEArJ9PoujC7Pgkap0mV7ytAJMKXx6fumyXvqAoAl4Va
# qp3cKcniNQfrcE1K1sGzVrihQTib0fsxf4/gX+GxPw+OFklg1waNGPmqJhCrKtPQ
# 0WeNG0a+RzDVLnLRxWPa52N5RH5LYySJhi40PylMUosqp8DikSiJucBb+R3Z5yet
# /5oCl8HGUJKbAiy9qbk0WQq/hEr/3/6zn+vZnuCYI+yma3cWKtvMrTscpIfcRnNe
# GWJoRVfkkIJCu0LW8GHgwaM9ZqNd9BjuiMmNF0UpmTJ1AjHuKSbIawLmtWJFfzcV
# WiNoidQ+3k4nsPBADLxNF8tNorMe0AZa3faTz1d1mfX6hhpneLO/lv403L3nUlbl
# s+V1e9dBkQXcXWnjlQ1DufyDljmVe2yAWk8TcsbXfSl6RLpSpCrVQUYJIP4ioLZb
# MI28iQzV13D4h1L92u+sUS4Hs07+0AnacO+Y+lbmbdu1V0vc5SwlFcieLnhO+Nqc
# noYsylfzGuXIkosagpZ6w7xQEmnYDlpGizrrJvojybawgb5CAKT41v4wLsfSRvbl
# jnX98sy50IdbzAYQYLuDNbdeZ95H7JlI8aShFf6tjGKOOVVPORa5sWOd/7cCAwEA
# AaOCAT4wggE6MA8GA1UdEwEB/wQFMAMBAf8wHQYDVR0OBBYEFLahVDkCw6A/joq8
# +tT4HKbROg79MB8GA1UdIwQYMBaAFAh2zcsH/yT2xc3tu5C84oQ3RnX3MA4GA1Ud
# DwEB/wQEAwIBBjAvBgNVHR8EKDAmMCSgIqAghh5odHRwOi8vY3JsLmNlcnR1bS5w
# bC9jdG5jYS5jcmwwawYIKwYBBQUHAQEEXzBdMCgGCCsGAQUFBzABhhxodHRwOi8v
# c3ViY2Eub2NzcC1jZXJ0dW0uY29tMDEGCCsGAQUFBzAChiVodHRwOi8vcmVwb3Np
# dG9yeS5jZXJ0dW0ucGwvY3RuY2EuY2VyMDkGA1UdIAQyMDAwLgYEVR0gADAmMCQG
# CCsGAQUFBwIBFhhodHRwOi8vd3d3LmNlcnR1bS5wbC9DUFMwDQYJKoZIhvcNAQEM
# BQADggEBAFHCoVgWIhCL/IYx1MIy01z4S6Ivaj5N+KsIHu3V6PrnCA3st8YeDrJ1
# BXqxC/rXdGoABh+kzqrya33YEcARCNQOTWHFOqj6seHjmOriY/1B9ZN9DbxdkjuR
# mmW60F9MvkyNaAMQFtXx0ASKhTP5N+dbLiZpQjy6zbzUeulNndrnQ/tjUoCFBMQl
# lVXwfqefAcVbKPjgzoZwpic7Ofs4LphTZSJ1Ldf23SIikZbr3WjtP6MZl9M7JYjs
# NhI9qX7OAo0FmpKnJ25FspxihjcNpDOO16hO0EoXQ0zF8ads0h5YbBRRfopUofbv
# n3l6XYGaFpAP4bvxSgD5+d2+7arszgowggZOMIIENqADAgECAhBrLj7aPHNfQyzM
# rEnkorNcMA0GCSqGSIb3DQEBCwUAMFYxCzAJBgNVBAYTAlBMMSEwHwYDVQQKExhB
# c3NlY28gRGF0YSBTeXN0ZW1zIFMuQS4xJDAiBgNVBAMTG0NlcnR1bSBDb2RlIFNp
# Z25pbmcgMjAyMSBDQTAeFw0yNjEwMTkxMTU4MDdaFw0yNzEwMTkxMTU4MDZaMHQx
# CzAJBgNVBAYTAkNPMRgwFgYDVQQIDA9WYWxsZSBkZWwgQ2F1Y2ExDTALBgNVBAcM
# BENhbGkxHTAbBgNVBAoMFEhPUklaVU4gR1JPVVAgUy5BLlMuMR0wGwYDVQQDDBRI
# T1JJWlVOIEdST1VQIFMuQS5TLjCCAaIwDQYJKoZIhvcNAQEBBQADggGPADCCAYoC
# ggGBAMMfir6ZfLjQSxeLCA6DggiUlNQ9qq0adiNtupf5rjcldCCJ6FUHWQRn6tD9
# jDWicNj3x6QjmtKP6iJFi6S7pI7mOGxckP4y4WvUupgZpDWFAEofLOczmo/WJdS4
# 3qncuBihGTR6P0TIF1RDLO7EQ/b6QNTmfKGIMFWZd1gV+tptfL9L8NFXcu7aFv8R
# mYUs0iDNi7Jvwluxijly5mPObUAsWL+oQV9AUwDwyIn3zIltD03Y1XG5H5Nfn5lD
# d9OsAaA1Cy/9/O+rL1A9X+eLg2vssKD/0daNALES/0m3Do/Z1n+KTU5TDgyu5Sqs
# Idzi10IdZfpCZlghxla2BsknnAc1ai9X8AMgdFP/0SOiuf8ePtWYvVCeAjC58jUA
# y3pIdP0F6CS+pe0fuZIiwPikb9txLGhrl/kNRhsuyEn9u1r1YJI2Q1ShbQeZMbiJ
# pNBm5DqtprjQLLEwe0zWRDj7x+oUQti9ghD61oKn/ROsO929MA5K9Q17+znVzimy
# 4lR3pQIDAQABo4IBeDCCAXQwDAYDVR0TAQH/BAIwADA9BgNVHR8ENjA0MDKgMKAu
# hixodHRwOi8vY2NzY2EyMDIxLmNybC5jZXJ0dW0ucGwvY2NzY2EyMDIxLmNybDBz
# BggrBgEFBQcBAQRnMGUwLAYIKwYBBQUHMAGGIGh0dHA6Ly9jY3NjYTIwMjEub2Nz
# cC1jZXJ0dW0uY29tMDUGCCsGAQUFBzAChilodHRwOi8vcmVwb3NpdG9yeS5jZXJ0
# dW0ucGwvY2NzY2EyMDIxLmNlcjAfBgNVHSMEGDAWgBTddF1MANt7n6B0yrFu9zzA
# MsBwzTAdBgNVHQ4EFgQUFcFIHokKwMEzDuspbooMIGvqXfwwSwYDVR0gBEQwQjAI
# BgZngQwBBAEwNgYLKoRoAYb2dwIFAQQwJzAlBggrBgEFBQcCARYZaHR0cHM6Ly93
# d3cuY2VydHVtLnBsL0NQUzATBgNVHSUEDDAKBggrBgEFBQcDAzAOBgNVHQ8BAf8E
# BAMCB4AwDQYJKoZIhvcNAQELBQADggIBADU5UA3OBCazNWvPipsNpRepq/vC64eP
# U5rEF2HlVWTzWLg19nOOkmSlkIGC/L0du4W9ZaBopy7XadY8tLVTndu4/yoipGS6
# HFclsMdgfD16IdkEg2wC4CE3UTASM5yChs0o1ViYF4wMD/PBqZ+aL+ZWeBKKY8nQ
# yJ6vdFV+nE/DRgDRtR3zaV+fkR6PY44Vd8kq0wHRmXu0ODmHrUpnk7n6Q5AEw2T0
# 29DjjoiNumfmcQLvn+xB580nwWbCvfXjrZsfeAK2aNkHIdaTjgOTPUXUjyhVVeXA
# utiLJP0ECS7G4/wsi3XKPt2MI1qVctzYSkzmSA0ocnvIWNyz6KZe75mfo+FsRMPW
# JePDdIoGQQAOtD6eh/+vwd6cQvQEMi3UsKzyuqpY2flqOfhW5eyyWX/vqtUGDDcZ
# rmg0aoiD/PGPSGhOrpagqB3SRNsC8JC5urSAt5IxWOJpbu4nz3/6IEJkcqwA6vHC
# Ma5dvkLC6CKaJnDP+RZcDbkU6usOfnqckQPddpjSE/jSl/UslrLaNbYR/648Dlii
# TZig76myWWkw1XG4mwpaeW60HTfBNEuehHpX+3HBmhECwuW8wGGyqt5ticnqQ30Z
# Aw1zfnrBJahQTuyAZS6zYqjKKjo/qNz6xDVWHathP4+rPvf9vqdu/4m7n1ykYv7v
# u/T7jWJfulQpMIIGgjCCBGqgAwIBAgIQKPB3wRw2vf5fdDJHcCcuAzANBgkqhkiG
# 9w0BAQwFADBWMQswCQYDVQQGEwJQTDEhMB8GA1UEChMYQXNzZWNvIERhdGEgU3lz
# dGVtcyBTLkEuMSQwIgYDVQQDExtDZXJ0dW0gVGltZXN0YW1waW5nIDIwMjEgQ0Ew
# HhcNMjYwMzExMDczNDU0WhcNMzYwMjI3MDczNDU0WjBQMQswCQYDVQQGEwJQTDEh
# MB8GA1UECgwYQXNzZWNvIERhdGEgU3lzdGVtcyBTLkEuMR4wHAYDVQQDDBVDZXJ0
# dW0gVGltZXN0YW1wIDIwMjYwggIiMA0GCSqGSIb3DQEBAQUAA4ICDwAwggIKAoIC
# AQC4vc3wcPsSNdoYthfbjUATTkk5EyItd37odw4lsmyOd/dmwXycOrTu8w7O1Kj/
# 63o1X+MRU1N1zTj7LjhZD+UjxbJ1sJ91SS/jYTYbLro72LELWkJ7zaNcJOOq3k4M
# LNS/DhRHSmsYMqk+MzkMfNZi8DWUqlzVPUa2/aACViReDMnc/gtXm7tA9Pr4DN7a
# gEPE1NtN36oCgj6iHR5aGhah/XskfvLZ6ZstoZ5zefWTrZrLh3kcH+YkzoZYX9Hf
# 6qqrizkiBj4BlS9R4+VwVKMdjhO7UEa/pf8cTWy+jrsATuAGthG7sWIN+3KV7+Yt
# v5Uy+DQEgf3bn/VuH4wN0aPhj/mwueJ9jmIjNwqvM/dno8fBeR1jtnuP++XgveQf
# bTMP3qu9dPRg8ltvtrreQZ9KPDFyUxEZMsrLVm6nJ1JKJgPe6n4gwsL6VrjjHX0z
# pLF3vUvsQ8TiWgm4sk4r4D7pcXE/rX0E0Vwa+VMLT73PiDZ2nzhIpc1RwNdbb/qR
# 3Mpa8sQXNPHFIoX/7bapGUtG5sd1cUGsvLqIWk6dP/xAgPb2BludUinSTCYV7ysi
# HQS8vFmDXIU7fMkSE7/QHcYPSahro4fk5z9W1t2kIxbZRJe0s4DXiPDlQPwiQFll
# LkU9wLvU6DqR8v35a65J4TeIXhMO/+iCzL+B0rI9GGeKLQIDAQABo4IBUDCCAUww
# dQYIKwYBBQUHAQEEaTBnMDsGCCsGAQUFBzAChi9odHRwOi8vc3ViY2EucmVwb3Np
# dG9yeS5jZXJ0dW0ucGwvY3RzY2EyMDIxLmNlcjAoBggrBgEFBQcwAYYcaHR0cDov
# L3N1YmNhLm9jc3AtY2VydHVtLmNvbTAfBgNVHSMEGDAWgBS+VAIvv0Bsc0POrAkl
# Tp5DRBru4DAMBgNVHRMBAf8EAjAAMDkGA1UdHwQyMDAwLqAsoCqGKGh0dHA6Ly9z
# dWJjYS5jcmwuY2VydHVtLnBsL2N0c2NhMjAyMS5jcmwwFgYDVR0lAQH/BAwwCgYI
# KwYBBQUHAwgwDgYDVR0PAQH/BAQDAgeAMCIGA1UdIAQbMBkwCAYGZ4EMAQQCMA0G
# CyqEaAGG9ncCBQELMB0GA1UdDgQWBBQjOWooq5oSp6Sn8h/5RZq/jf6CAzANBgkq
# hkiG9w0BAQwFAAOCAgEAZv6Tm6wL3k6mBTwkrYcTVcAR01uE37gUGNihgnTJR+gC
# 41T2H9lwETRjE10nEdCUNv3Ni8S6aBx4UcMRX93fd9w4RUteua2I+oj0kCpuNa3P
# QDKUwozHZmotWivaPO9KTs08y0YVhVSMZvuUH/reFec9eLT6FLAFnX0Sg9Wc7833
# uQB5RPa8TmXBkTolC8bNxHX+2SfEWSNI8w/Y7o9qBF9l0uwVSrjQitVtIdZo2qb9
# NvMqqPFLWYef9SG+PGMqfPAQ2EoYeT0hlFLkubWF1X3HbiDwXtz3TU2EWHU+VTcZ
# MC4IzMyHK/+kh9n3oxSQ45wsY71y7mqxKyYPyHSPoV3RiAQ1Thr9c65BeJLaELxW
# SKeQyAGIdBsMlIXPO6qp+VGmE49vosogQ0jYe2hgq5LgJ6oO0Ie364V/kITBnifU
# qS6Zxu+qjfOYS8o72EeoEWIi04/BuH8Y6leNNP27j8NKwaPciWcqv/HBnQjizzmH
# /aqXn03K2SnjvaYPlCR8fFAAW2k6os0cZvLFEhMnHHAghYSIrdwCP98BrkjgA3og
# Ou+jXEO/nbD9OGL873Te6U2TcoOu9lqQQyXYNHiLR5eMqJrzXiDlodarOEKjluky
# PDnLIuLkg0y6K8S1A5wU8CkM86cXdWFHLRIEYd9kdCfRZBZHhK7iVPeEZYxa9bgw
# gga5MIIEoaADAgECAhEAmaOACiZVO2Wr3G6EprPqOTANBgkqhkiG9w0BAQwFADCB
# gDELMAkGA1UEBhMCUEwxIjAgBgNVBAoTGVVuaXpldG8gVGVjaG5vbG9naWVzIFMu
# QS4xJzAlBgNVBAsTHkNlcnR1bSBDZXJ0aWZpY2F0aW9uIEF1dGhvcml0eTEkMCIG
# A1UEAxMbQ2VydHVtIFRydXN0ZWQgTmV0d29yayBDQSAyMB4XDTIxMDUxOTA1MzIx
# OFoXDTM2MDUxODA1MzIxOFowVjELMAkGA1UEBhMCUEwxITAfBgNVBAoTGEFzc2Vj
# byBEYXRhIFN5c3RlbXMgUy5BLjEkMCIGA1UEAxMbQ2VydHVtIENvZGUgU2lnbmlu
# ZyAyMDIxIENBMIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEAnSPPBDAj
# O8FGLOczcz5jXXp1ur5cTbq96y34vuTmflN4mSAfgLKTvggv24/rWiVGzGxT9YEA
# SVMw1Aj8ewTS4IndU8s7VS5+djSoMcbvIKck6+hI1shsylP4JyLvmxwLHtSworV9
# wmjhNd627h27a8RdrT1PH9ud0IF+njvMk2xqbNTIPsnWtw3E7DmDoUmDQiYi/ucJ
# 42fcHqBkbbxYDB7SYOouu9Tj1yHIohzuC8KNqfcYf7Z4/iZgkBJ+UFNDcc6zokZ2
# uJIxWgPWXMEmhu1gMXgv8aGUsRdaCtVD2bSlbfsq7BiqljjaCun+RJgTgFRCtsuA
# Ew0pG9+FA+yQN9n/kZtMLK+Wo837Q4QOZgYqVWQ4x6cM7/G0yswg1ElLlJj6NYKL
# w9EcBXE7TF3HybZtYvj9lDV2nT8mFSkcSkAExzd4prHwYjUXTeZIlVXqj+eaYqoM
# TpMrfh5MCAOIG5knN4Q/JHuurfTI5XDYO962WZayx7ACFf5ydJpoEowSP07YaBiQ
# 8nXpDkNrUA9g7qf/rCkKbWpQ5boufUnq1UiYPIAHlezf4muJqxqIns/kqld6JVX8
# cixbd6PzkDpwZo4SlADaCi2JSplKShBSND36E/ENVv8urPS0yOnpG4tIoBGxVCAR
# PCg1BnyMJ4rBJAcOSnAWd18Jx5n858JSqPECAwEAAaOCAVUwggFRMA8GA1UdEwEB
# /wQFMAMBAf8wHQYDVR0OBBYEFN10XUwA23ufoHTKsW73PMAywHDNMB8GA1UdIwQY
# MBaAFLahVDkCw6A/joq8+tT4HKbROg79MA4GA1UdDwEB/wQEAwIBBjATBgNVHSUE
# DDAKBggrBgEFBQcDAzAwBgNVHR8EKTAnMCWgI6Ahhh9odHRwOi8vY3JsLmNlcnR1
# bS5wbC9jdG5jYTIuY3JsMGwGCCsGAQUFBwEBBGAwXjAoBggrBgEFBQcwAYYcaHR0
# cDovL3N1YmNhLm9jc3AtY2VydHVtLmNvbTAyBggrBgEFBQcwAoYmaHR0cDovL3Jl
# cG9zaXRvcnkuY2VydHVtLnBsL2N0bmNhMi5jZXIwOQYDVR0gBDIwMDAuBgRVHSAA
# MCYwJAYIKwYBBQUHAgEWGGh0dHA6Ly93d3cuY2VydHVtLnBsL0NQUzANBgkqhkiG
# 9w0BAQwFAAOCAgEAdYhYD+WPUCiaU58Q7EP89DttyZqGYn2XRDhJkL6P+/T0IPZy
# xfxiXumYlARMgwRzLRUStJl490L94C9LGF3vjzzH8Jq3iR74BRlkO18J3zIdmCKQ
# a5LyZ48IfICJTZVJeChDUyuQy6rGDxLUUAsO0eqeLNhLVsgw6/zOfImNlARKn1FP
# 7o0fTbj8ipNGxHBIutiRsWrhWM2f8pXdd3x2mbJCKKtl2s42g9KUJHEIiLni9Byo
# qIUul4GblLQigO0ugh7bWRLDm0CdY9rNLqyA3ahe8WlxVWkxyrQLjH8ItI17RdyS
# aYayX3PhRSC4Am1/7mATwZWwSD+B7eMcZNhpn8zJ+6MTyE6YoEBSRVrs0zFFIHUR
# 08Wk0ikSf+lIe5Iv6RY3/bFAEloMU+vUBfSouCReZwSLo8WdrDlPXtR0gicDnytO
# 7eZ5827NS2x7gCBibESYkOh1/w1tVxTpV2Na3PR7nxYVlPu1JPoRZCbH86gc96UT
# vuWiOruWmyOEMLOGGniR+x+zPF/2DaGgK2W1eEJfo2qyrBNPvF7wuAyQfiFXLwvW
# HamoYtPZo0LHuH8X3n9C+xN4YaNjt2ywzOr+tKyEVAotnyU9vyEVOaIYMk3IeBrm
# Fnn0gbKeTTyYeEEUz/Qwt4HOUBCrW602NCmvO1nm+/80nLy5r0AZvCQxaQ4wgga5
# MIIEoaADAgECAhEA5/9pxzs1zkuRJth0fGilhzANBgkqhkiG9w0BAQwFADCBgDEL
# MAkGA1UEBhMCUEwxIjAgBgNVBAoTGVVuaXpldG8gVGVjaG5vbG9naWVzIFMuQS4x
# JzAlBgNVBAsTHkNlcnR1bSBDZXJ0aWZpY2F0aW9uIEF1dGhvcml0eTEkMCIGA1UE
# AxMbQ2VydHVtIFRydXN0ZWQgTmV0d29yayBDQSAyMB4XDTIxMDUxOTA1MzIwN1oX
# DTM2MDUxODA1MzIwN1owVjELMAkGA1UEBhMCUEwxITAfBgNVBAoTGEFzc2VjbyBE
# YXRhIFN5c3RlbXMgUy5BLjEkMCIGA1UEAxMbQ2VydHVtIFRpbWVzdGFtcGluZyAy
# MDIxIENBMIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEA6RIfBDXtuV16
# xaaVQb6KZX9Od9FtJXXTZo7b+GEof3+3g0ChWiKnO7R4+6MfrvLyLCWZa6GpFHjE
# t4t0/GiUQvnkLOBRdBqr5DOvlmTvJJs2X8ZmWgWJjC7PBZLYBWAs8sJl3kNXxBMX
# 5XntjqWx1ZOuuXl0R4x+zGGSMzZ45dpvB8vLpQfZkfMC/1tL9KYyjU+htLH68dZJ
# PtzhqLBVG+8ljZ1ZFilOKksS79epCeqFSeAUm2eMTGpOiS3gfLM6yvb8Bg6bxg5y
# glDGC9zbr4sB9ceIGRtCQF1N8dqTgM/dSViiUgJkcv5dLNJeWxGCqJYPgzKlYZTg
# DXfGIeZpEFmjBLwURP5ABsyKoFocMzdjrCiFbTvJn+bD1kq78qZUgAQGGtd6zGJ8
# 8H4NPJ5Y2R4IargiWAmv8RyvWnHr/VA+2PrrK9eXe5q7M88YRdSTq9TKbqdnITUg
# Zcjjm4ZUjteq8K331a4P0s2in0p3UubMEYa/G5w6jSWPUzchGLwWKYBfeSu6dIOC
# 4LkeAPvmdZxSB1lWOb9HzVWZoM8Q/blaP4LWt6JxjkI9yQsYGMdCqwl7uMnPUIlc
# ExS1mzXRxUowQref/EPaS7kYVaHHQrp4XB7nTEtQhkP0Z9Puz/n8zIFnUSnxDof4
# Yy650PAXSYmK2TcbyDoTNmmt8xAxzcMCAwEAAaOCAVUwggFRMA8GA1UdEwEB/wQF
# MAMBAf8wHQYDVR0OBBYEFL5UAi+/QGxzQ86sCSVOnkNEGu7gMB8GA1UdIwQYMBaA
# FLahVDkCw6A/joq8+tT4HKbROg79MA4GA1UdDwEB/wQEAwIBBjATBgNVHSUEDDAK
# BggrBgEFBQcDCDAwBgNVHR8EKTAnMCWgI6Ahhh9odHRwOi8vY3JsLmNlcnR1bS5w
# bC9jdG5jYTIuY3JsMGwGCCsGAQUFBwEBBGAwXjAoBggrBgEFBQcwAYYcaHR0cDov
# L3N1YmNhLm9jc3AtY2VydHVtLmNvbTAyBggrBgEFBQcwAoYmaHR0cDovL3JlcG9z
# aXRvcnkuY2VydHVtLnBsL2N0bmNhMi5jZXIwOQYDVR0gBDIwMDAuBgRVHSAAMCYw
# JAYIKwYBBQUHAgEWGGh0dHA6Ly93d3cuY2VydHVtLnBsL0NQUzANBgkqhkiG9w0B
# AQwFAAOCAgEAuJNZd8lMFf2UBwigp3qgLPBBk58BFCS3Q6aJDf3TISoytK0eal/J
# yCB88aUEd0wMNiEcNVMbK9j5Yht2whaknUE1G32k6uld7wcxHmw67vUBY6pSp8Qh
# dodY4SzRRaZWzyYlviUpyU4dXyhKhHSncYJfa1U75cXxCe3sTp9uTBm3f8Bj8Lkp
# jMUSVTtMJ6oEu5JqCYzRfc6nnoRUgwz/GVZFoOBGdrSEtDN7mZgcka/tS5MI47fA
# LVvN5lZ2U8k7Dm/hTX8CWOw0uBZloZEW4HB0Xra3qE4qzzq/6M8gyoU/DE0k3+i7
# bYOrOk/7tPJg1sOhytOGUQ30PbG++0FfJioDuOFhj99b151SqFlSaRQYz74y/P2X
# JP+cF19oqozmi0rRTkfyEJIvhIZ+M5XIFZttmVQgTxfpfJwMFFEoQrSrklOxpmSy
# gppsUDJEoliC05vBLVQ+gMZyYaKvBJ4YxBMlKH5ZHkRdloRYlUDplk8GUa+OCMVh
# pDSQurU6K1ua5dmZftnvSSz2H96UrQDzA6DyiI1V3ejVtvn2azVAXg6NnjmuRZ+w
# a7Pxy0H3+V4K4rOTHlG3VYA6xfLsTunCz72T6Ot4+tkrDYOeaU1pPX1CBfYj6EW2
# +ELq46GP8KCNUQDirWLU4nOmgCat7vN0SD6RlwUiSsMeCiQDmZwgwrUxggaiMIIG
# ngIBATBqMFYxCzAJBgNVBAYTAlBMMSEwHwYDVQQKExhBc3NlY28gRGF0YSBTeXN0
# ZW1zIFMuQS4xJDAiBgNVBAMTG0NlcnR1bSBDb2RlIFNpZ25pbmcgMjAyMSBDQQIQ
# ay4+2jxzX0MszKxJ5KKzXDANBglghkgBZQMEAgEFAKCBhDAYBgorBgEEAYI3AgEM
# MQowCKACgAChAoAAMBkGCSqGSIb3DQEJAzEMBgorBgEEAYI3AgEEMBwGCisGAQQB
# gjcCAQsxDjAMBgorBgEEAYI3AgEVMC8GCSqGSIb3DQEJBDEiBCAdDhs8H69bfwXp
# Cv0eoR8pzM6UpkHiSvTm2yclSsfxLTANBgkqhkiG9w0BAQEFAASCAYBJbtf5Xeog
# ihgIWIG5A/WNTe9Ofqq1uEH3PxR0A0VMLC1FA29bOYzyirpDFOkIZYzu3k4Ol88m
# RQqn3u8jkkXtS6rEZwwZlwtRhDzmajv5fH1vgxuMx2h5331IU+UyK/WHXrVOodxZ
# 7sf4/tbJaTAY90TjLGYZdayI9d1xEHxAviFIUtVP8LzN7G7dUKjeStA9tqcMIcqb
# 5bi9eItnnwXiipr6GvtxF9FXED45lNEEdQn+lXgayUTiuE1HftAWutvhyyt5n5S5
# AZFIuHn6yQ3mpSLpSedpGWs7ZulAu+YQaFLYMtEBKaBcZvuaNZz03KQC7fP1asIx
# 4Y8/WZqfEmohnI5F7M3YuvdDEt5wnUytd0pZpBi+rgrvqbB/J4av+Jw7Zii7T258
# oGjvMxiG9sJaRfD8jDyATuqN0MpUtGLvd7jjIfWExe8DRkAHOn6DasSeS0s+MApZ
# 2yLTpnyCLsKRXqgQuLJLgQhA8eBDEY3x4Wa76XE0ICoYQhH+rV+GWFahggQCMIID
# /gYJKoZIhvcNAQkGMYID7zCCA+sCAQEwajBWMQswCQYDVQQGEwJQTDEhMB8GA1UE
# ChMYQXNzZWNvIERhdGEgU3lzdGVtcyBTLkEuMSQwIgYDVQQDExtDZXJ0dW0gVGlt
# ZXN0YW1waW5nIDIwMjEgQ0ECECjwd8EcNr3+X3QyR3AnLgMwDQYJYIZIAWUDBAIC
# BQCgggFWMBoGCSqGSIb3DQEJAzENBgsqhkiG9w0BCRABBDAcBgkqhkiG9w0BCQUx
# DxcNMjYxMDIwMDI1MTU3WjA3BgsqhkiG9w0BCRACLzEoMCYwJDAiBCCFvpDhCtJD
# jXzJKLavSLCasggXfOz4sBJsWNORBSXEPDA/BgkqhkiG9w0BCQQxMgQwPSR3xD4M
# ntnZgoDg8uvwFYfXwFCNVznBvZUEsXYH/5uLHvXkG6v57M1cT+UNAYgVMIGfBgsq
# hkiG9w0BCRACDDGBjzCBjDCBiTCBhgQUVxRoQQyoWvNCTvkWSlE2EPTTjZgwbjBa
# pFgwVjELMAkGA1UEBhMCUEwxITAfBgNVBAoTGEFzc2VjbyBEYXRhIFN5c3RlbXMg
# Uy5BLjEkMCIGA1UEAxMbQ2VydHVtIFRpbWVzdGFtcGluZyAyMDIxIENBAhAo8HfB
# HDa9/l90MkdwJy4DMA0GCSqGSIb3DQEBAQUABIICABD3yl7gWYkcag0d4+1x9QjM
# 9Tt20/Ab/ShlYB8wE329Hg2mElT8wNng8euqCmPiA8fjJzs74zVXSWFDPONsb2ay
# RUrcFGPadtoPgp7nn8W+YTxAav3GIKX0MOUULfJpguLnelQtAjQZcaVwMU2F52HX
# OafYNZvKVbLp8SZ4z092lrhRO7jQR+TJVYm2LOs10RBlC6S1L6IGUUx5EBo1JdXL
# B/v14WvCyRlZWQt1KJIc6TTnMzoVkRcLgeVkup2Ah61dBL2/2JJNxC8DHEoQ+K1M
# /5sAPCWIfcwSr1trKja1OGNXM4CKUMi8jbPEAaHNmxf8iSsKRdvEnvKwDhhqrQGW
# swgAKRjWePrp0g+aMEgR7JbL7v39BOorbjITXaOLSgjac9kMef/zopnS1ZRfzLz0
# xD5kTQx0af5ziVt1St/rLwBOggL1U8geoPLLKDRFpWpFtJKKtDip8csPauaBjyST
# uIk4KVp5H+WHrAUynYPkibBQM7/R25TQRkluCTI0d9TQS8jIsx43MsLJQEJqOZTA
# l0ANBzivmNihCqs9CUPkefMsLwwd7ZQKTOy8qA0CCf7o8G7iR/YWjfRdTXVXmAn2
# KynNZU5v75eXldzPqbqGvmOQ9XZTbnizYEcEL1Cq0ZvH7L+sfRpYXvc1B28DTJOg
# a5+SLeiBuy0YyVGD9prO
# SIG # End signature block
