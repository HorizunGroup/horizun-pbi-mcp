# FUENTE CANONICA del bloque de un pegado. No lo copies a mano a ningun sitio.
#
# docs/INSTALL.md y skills/horizun-pbi-setup/SKILL.md incrustan ESTE archivo
# palabra por palabra, y tests/test_one_paste.py falla si alguno se desvia una
# coma. Un bloque que se mantiene en tres sitios acaba siendo tres bloques
# distintos, y el que se quede atras sera el que alguien pegue. El README ya no
# lo incrusta: solo enlaza a INSTALL, y por eso no esta en esta lista.
#
# Que sustituye: `irm .../main/scripts/instalar.ps1 | iex`, que descargaba de
# una RAMA -bytes que pueden cambiar bajo el mismo enlace- y ejecutaba lo que
# llegara sin mirarlo. HTTPS garantiza QUIEN te da los bytes, no QUE bytes.
#
# Que hace este, en orden: descarga a un temporal aleatorio desde una release
# FIJA, rechaza la respuesta si se anuncia mas grande de la cuenta, corta el
# stream si crece de la cuenta mientras baja, comprueba el SHA-256 y solo
# entonces ejecuta -con `&`, nunca con Invoke-Expression-. Si algo no cuadra,
# no se ejecuta nada y el temporal se borra igual.
#
# El SHA-256 se calcula con .NET y NO con `Get-FileHash`, y conviene decir con
# precision QUE arregla eso, porque una version anterior de este comentario lo
# exageraba. `Get-FileHash` es un CMDLET: usarlo obliga a RESOLVERLO, y esa
# resolucion depende del estado del interprete -que modulos hay cargados, que
# dice PSModulePath, que hay en el perfil-, que no controla quien pega el
# bloque. Con `$ErrorActionPreference = 'Stop'`, un comando que no resuelve
# LANZA, asi que el bloque se abortaba: **nunca hubo un camino por el que se
# ejecutara un instalador sin verificar**. Lo que se elimina no es un agujero de
# integridad, es una dependencia ambiental que podia convertir una instalacion
# buena en una fallida. `[Security.Cryptography.SHA256]` es un TIPO de la BCL:
# lo resuelve el runtime, no el descubrimiento de comandos. Se usa `::Create()`
# y no `SHA256Managed` porque en una maquina con FIPS activado la
# implementacion "managed" lanza.
#
# Por lo mismo, el instalador se lanza con la ruta ABSOLUTA de
# `powershell.exe` y no con el nombre `powershell`: un nombre lo resuelven
# primero los alias y las funciones de la sesion, y solo despues el PATH.
#
# El SHA-256 y el tamano de abajo son los de scripts/instalar.ps1 y viven
# tambien en scripts/downloads_manifest.json; una prueba comprueba los tres
# contra los bytes reales del archivo.
$ErrorActionPreference = 'Stop'
$url = 'https://github.com/HorizunGroup/horizun-pbi-mcp/releases/download/v2.1.0/horizun-pbi-mcp-instalar.ps1'
$sha = '00b7893c47a57de658eb69113ea709863e070fa653c35c4004ac612a4453d03d'
$max = 131072
$tmp = Join-Path ([IO.Path]::GetTempPath()) ('horizun-' + [guid]::NewGuid().ToString('N') + '.ps1')
# En que punto se quedo, para que el mensaje final diga la verdad y no una
# formula. Antes, un instalador que se ejecutaba y devolvia error terminaba
# imprimiendo "No se ejecuto nada que no coincidiera con el hash publicado":
# cierto en lo literal y enganoso en lo que la persona entiende, que es que no
# se ejecuto nada.
$fase = 'descarga'
$ejecutado = $false
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $peticion = [Net.HttpWebRequest]::Create($url)
    $peticion.UserAgent = 'horizun-pbi-mcp-one-paste'
    $peticion.Timeout = 60000
    $respuesta = $peticion.GetResponse()
    if ($respuesta.ContentLength -gt $max) {
        throw ("El servidor anuncia " + $respuesta.ContentLength + " bytes y el maximo aceptado es " + $max + ". No se descarga nada.")
    }
    $entrada = $respuesta.GetResponseStream()
    $salida = [IO.File]::Open($tmp, 'Create', 'Write', 'None')
    $total = 0
    try {
        $bloque = New-Object byte[] 8192
        while (($leidos = $entrada.Read($bloque, 0, $bloque.Length)) -gt 0) {
            $total += $leidos
            if ($total -gt $max) {
                throw ("La descarga supero " + $max + " bytes mientras bajaba. Se aborta sin ejecutar nada.")
            }
            $salida.Write($bloque, 0, $leidos)
        }
    } finally {
        $salida.Dispose(); $entrada.Dispose(); $respuesta.Dispose()
    }
    if ($total -eq 0) { throw "La descarga llego vacia. No se ejecuta nada." }
    $fase = 'verificacion'
    if ($sha -cnotmatch '^[0-9a-f]{64}$') {
        throw "El hash publicado en el bloque no es un SHA-256 de 64 hex en minusculas. No se ejecuta nada."
    }
    $flujo = [IO.File]::Open($tmp, 'Open', 'Read', 'Read')
    try {
        $algoritmo = [Security.Cryptography.SHA256]::Create()
        try { $digest = $algoritmo.ComputeHash($flujo) } finally { $algoritmo.Dispose() }
    } finally { $flujo.Dispose() }
    $real = [BitConverter]::ToString($digest).Replace('-', '').ToLowerInvariant()
    if ($real.Length -ne 64) {
        throw "No se pudo calcular el SHA-256 de lo descargado. No se ejecuta nada."
    }
    if ($real -ne $sha) {
        throw ("SHA-256 NO coincide. Esperado " + $sha + ", recibido " + $real + ". No se ejecuta nada.")
    }
    $fase = 'ejecucion'
    Write-Host ("SHA-256 verificado sobre " + $total + " bytes. Ejecutando el instalador...") -ForegroundColor Green
    $ps = [IO.Path]::Combine($PSHOME, 'powershell.exe')
    if (-not [IO.File]::Exists($ps)) {
        $ps = [IO.Path]::Combine([Environment]::SystemDirectory, 'WindowsPowerShell', 'v1.0', 'powershell.exe')
    }
    if (-not [IO.File]::Exists($ps)) {
        throw "No se encontro Windows PowerShell. No se ejecuta nada."
    }
    & $ps -NoProfile -ExecutionPolicy Bypass -File $tmp
    $ejecutado = $true
    if ($LASTEXITCODE -ne 0) {
        throw ("El instalador termino con codigo " + $LASTEXITCODE + ".")
    }
} catch {
    Write-Host ""
    Write-Host ("[ERROR] Instalacion abortada: " + $_.Exception.Message) -ForegroundColor Red
    if ($ejecutado) {
        Write-Host "        El instalador SI llego a ejecutarse: sus bytes coincidian con el hash publicado, y fallo durante la instalacion." -ForegroundColor Red
    } else {
        Write-Host ("        No se ejecuto nada: se aborto en la fase '" + $fase + "', antes de lanzar el instalador.") -ForegroundColor Red
    }
    throw
} finally {
    if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
}
