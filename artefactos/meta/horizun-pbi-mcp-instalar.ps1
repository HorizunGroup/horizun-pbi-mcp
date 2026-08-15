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
# la politica de ejecucion del usuario, registra el plugin en Claude Code y
# deja dicho el unico paso restante. Es idempotente: correrlo dos veces no
# rompe nada. No pide ni usa permisos de administrador.
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
$script:Plan       = @()
$script:Detectado  = @()
$script:Faltante   = @()
$script:Clientes   = @()

function Paso($m)  { Write-Host ""; Write-Host ("== " + $m) -ForegroundColor Cyan }
function Ok($m)    { Write-Host ("  [OK] " + $m) -ForegroundColor Green; $script:Detectado += $m }
function Aviso($m) { Write-Host ("  [PENDIENTE] " + $m) -ForegroundColor Yellow; $script:Pendientes += $m }
function Fallo($m) { Write-Host ("  [ERROR] " + $m) -ForegroundColor Red }
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

# --- 5. Claude Code ----------------------------------------------------------
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
# Codex se registra a mano: este instalador no lo cubre (CLI-001 sigue abierta).
$script:Clientes += "Codex: fuera del alcance de este instalador (registro manual)"

# --- 6. Registrar el plugin en Claude Code -----------------------------------
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
        # Ahora se busca la LINEA del plugin y se mira su estado. Se acepta lo
        # que Claude Code imprime hoy para "activo" -`enabled`, o la linea sin
        # marca de desactivado- y se rechaza explicitamente lo que marca
        # apagado. Si el formato cambia y no se reconoce, se avisa en vez de
        # dar por bueno: no reconocer no es aprobar.
        $linea = ($lista -split "`r?`n" | Where-Object { $_ -match 'horizun-pbi-mcp' } |
                  Select-Object -First 1)
        if (-not $linea) {
            Aviso "El plugin NO aparece en 'claude plugin list'. Reintenta con: claude plugin marketplace add HorizunGroup/horizun-pbi-mcp ; claude plugin install horizun-pbi-mcp@horizun"
        } elseif ($linea -match '(?i)(disabled|deshabilitad|inactive|desactivad)') {
            Aviso ("El plugin aparece DESHABILITADO en 'claude plugin list': " +
                   $linea.Trim() + ". Habilitalo con: claude plugin enable horizun-pbi-mcp")
        } elseif ($linea -match '(?i)error') {
            Aviso ("La linea del plugin en 'claude plugin list' reporta un error: " +
                   $linea.Trim())
        } else {
            Ok ("Plugin registrado y habilitado: " + $linea.Trim())
        }
    }
} else {
    if ($script:Seco) {
        Write-Host "  [DIAGNOSTICO] Sin Claude Code no habria donde registrar el plugin." -ForegroundColor Yellow
    } else {
        Aviso "Sin Claude Code no se puede registrar el plugin todavia."
    }
}

# --- 7. Veredicto ------------------------------------------------------------
Write-Host ""
Write-Host "-------------------------------------------------------------"

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
    Write-Host "LISTO. Un solo paso restante:" -ForegroundColor Green
    Write-Host "  1. Abre (o reinicia) Claude Code: la primera sesion prepara el runtime SOLA."
    Write-Host "     (Si es la primera vez en este equipo, Claude te pedira iniciar sesion:"
    Write-Host "      eso es normal, no es un fallo de la instalacion.)"
    Write-Host "  2. Escribe 'pbi_install_status' si quieres ver el avance (tarda ~1-2 min)."
    Write-Host "  3. Cuando diga 'Runtime listo', reinicia Claude Code una vez: apareceran las tools pbi_*."
} else {
    Write-Host ("QUEDARON " + $script:Pendientes.Count + " PENDIENTE(S):") -ForegroundColor Yellow
    $script:Pendientes | ForEach-Object { Write-Host ("  - " + $_) }
    Write-Host "  Resuelvelos (o pide a TI lo marcado) y vuelve a pegar este mismo comando: es seguro repetirlo."
}
Write-Host "-------------------------------------------------------------"
