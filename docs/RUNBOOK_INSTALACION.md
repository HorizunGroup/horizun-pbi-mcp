# Runbook de instalación — update, rollback, desinstalación, proxy y offline

`docs/RECOVERY.md` cubre los journals y el rollback de **escrituras al
proyecto**: qué hacer cuando una operación sobre un `.pbip` se queda a medias.
Este documento cubre la otra mitad, que no estaba escrita en ningún sitio: el
**ciclo de vida de la instalación**.

Cada paso es un comando ejecutable. Donde no lo hay, se dice —y se dice por qué.

> **Convención de rutas.** Todo cuelga del *data root*, que es estable y no
> depende del cliente que instale:
>
> ```
> %LOCALAPPDATA%\HorizunPbiMcp\plugin\
> ```
>
> Comprueba el tuyo con `python scripts/plugin_bootstrap.py --status`; el campo
> `data_dir` es la verdad. Un override explícito por `HORIZUN_PBI_PLUGIN_DATA`
> lo cambia, y es lo que usan las pruebas.

---

## 0. Lo primero, siempre: mirar

```bash
python scripts/plugin_bootstrap.py --status
```

Devuelve el estado completo. Los cinco campos que deciden qué hacer:

| Campo | Qué responde |
|---|---|
| `state` | el estado **operativo**: `ready`, `degraded`, `failed`, `installing`, `not_installed` |
| `estado_instalacion` | cómo acabó el último intento de instalar; **no** es lo mismo |
| `sirviendo` | qué se está ejecutando: `activo`, `last-known-good` o `ninguno` |
| `sirviendo_version` | la versión de eso |
| `degradacion` | por qué el activo dejó de servir, si es el caso |

**`state: degraded` no es un fallo de instalación.** Significa que la última
instalación fue bien y que lo que instaló ya no arranca — alguien borró un
archivo, un antivirus se llevó el paquete, el disco falló. Se resuelve
reinstalando (§1), no recuperando.

Y en el otro sentido: `estado_instalacion: failed` con `sirviendo:
last-known-good` significa que la actualización se cayó **y que sigues
trabajando** con la versión anterior. No hay prisa.

---

## 1. Actualizar

No hay que hacer nada especial: la actualización es la instalación.

```bash
python scripts/plugin_bootstrap.py
```

Desde un cliente MCP, la tool `pbi_install_runtime` hace lo mismo en segundo
plano y `pbi_install_status` informa del avance.

**Qué garantiza.** El runtime nuevo se prepara en un directorio aparte y **el
vigente no se toca en ningún momento** de la preparación. Solo se publica —con
un `rename`— después de que el preparado supere un handshake MCP real contra el
contrato: nombre exacto del servidor, versión igual a la preparada y las 134
tools. Si algo falla, el staging se descarta y sigues exactamente donde estabas.

**Reintentar NO reanuda.** Descarta lo preparado y empieza de nuevo,
reaprovechando por copia lo que ya esté verificado en disco. No reinicies el
cliente esperando que continúe solo.

---

## 2. Volver atrás (rollback de instalación)

**En la mayoría de los casos no hay que hacer nada.** Si la actualización falla,
el lanzador sirve solo el último runtime bueno: verifica el runtime antes de
entregarle el canal del cliente, y si no pasa, elige el anterior. Reinicia el
cliente MCP y sigues con la versión anterior y sus 134 tools.

Para comprobar cuál se serviría:

```bash
python -c "import sys; sys.path.insert(0,'scripts'); import plugin_bootstrap as b; print(b.seleccionar_runtime())"
```

**Volver a mano a una versión concreta.** Las versiones anteriores viven como
directorios hermanos bajo el data root:

```bash
dir %LOCALAPPDATA%\HorizunPbiMcp\plugin
```

Verás `1.5.4`, `2.0.1`, y quizá `.previous-<version>-<ts>-<uuid>`. Para forzar
que se sirva una de ellas, borra el estado y deja que se readopte:

```bash
del %LOCALAPPDATA%\HorizunPbiMcp\plugin\runtime-state.json
python scripts/plugin_bootstrap.py
```

La adopción **comprueba** el runtime que encuentra con el mismo handshake antes
de declararlo activo: no lo da por bueno porque exista un `python.exe`.

**Nunca borres a mano un `.previous-*` sin mirar antes** cuál es el
`last_known_good` en `runtime-state.json`. Es el único al que se puede volver.

---

## 3. Una promoción interrumpida

Un corte de luz entre los dos renombrados de una promoción deja el data root con
un `.promotion.json` y, quizá, sin la carpeta de la versión. **Se resuelve solo**:
la siguiente instalación llama a la recuperación antes de nada, dentro del
cerrojo del ciclo de vida.

```bash
python scripts/plugin_bootstrap.py
```

Si el journal no se puede interpretar, **no se toca nada de lo que menciona**:
se aparta a `.promotion-rechazada-<uuid>.json` dentro del data root y el motivo
sale en el status. Ese archivo es evidencia — mándalo si abres una incidencia.

---

## 4. Desinstalar

**Enumera primero. Siempre.** Sin `--confirm`, el comando no borra nada: dice
qué borraría y cuánto liberaría. Ese es el comportamiento por defecto a
propósito — un error de dedo debe ser un susto, no una pérdida.

```bash
python scripts/plugin_bootstrap.py --uninstall
```

Cuando la lista te cuadre:

```bash
python scripts/plugin_bootstrap.py --uninstall --confirm
```

**Qué conserva.** `outputs/` y `backups/` son **tuyos** —tus exportaciones y
los respaldos de tus proyectos— y sobreviven. Lo que se va es lo reconstruible:
runtimes, versiones anteriores, estado y registros. La respuesta trae
`residual_bytes`, que después de desinstalar tiene que ser exactamente el peso
de tus datos.

**Antes de ejecutarlo:** cierra Claude y Codex. Un runtime en uso no se puede
retirar del todo, y el comando se niega si hay una instalación en curso — borrar
mientras alguien publica dejaría al instalador escribiendo en el aire.

Y retira el plugin de su cliente, que vive fuera del data root:

```bash
claude plugin remove horizun-pbi-mcp
```

Lo que **no** retira nada de esto: Python, Node y Claude Code, que el instalador
puso por `winget` y que probablemente uses para otras cosas. Se quitan con
`winget uninstall`, uno a uno y a conciencia.

---

## 5. Purga completa

`--purge` es `--uninstall` **más tus datos**. Misma regla: sin `--confirm` solo
enumera.

```bash
python scripts/plugin_bootstrap.py --purge
python scripts/plugin_bootstrap.py --purge --confirm
```

Después de un purge confirmado, `residual_bytes` es `0`.

Si solo quieres mirar, sin intención de retirar nada:

```bash
python scripts/plugin_bootstrap.py --inventory
```

Da cada entrada del data root con su tipo —`runtime`, `runtime-anterior`,
`datos-del-usuario`, `preparacion-a-medias`— y su tamaño.

---

## 6. Proxy

El instalador y los descargadores usan `urllib` y `subprocess` con `pip` y
`npm`. Los tres respetan las variables estándar:

```bash
set HTTPS_PROXY=http://usuario:clave@proxy.empresa:8080
set HTTP_PROXY=http://usuario:clave@proxy.empresa:8080
set NO_PROXY=localhost,127.0.0.1
python scripts/plugin_bootstrap.py
```

**Lo que hay que saber antes de intentarlo:**

- Un proxy que **inspecciona TLS** rompe la verificación por hash de las DLL y
  los esquemas: los bytes que llegan no son los publicados y la instalación se
  niega. Eso es correcto y no se debe desactivar. Añade el certificado de la
  empresa al almacén del sistema y a `REQUESTS_CA_BUNDLE` / `SSL_CERT_FILE`.
- `npm` necesita además su propia configuración:
  ```bash
  npm config set proxy http://proxy.empresa:8080
  npm config set https-proxy http://proxy.empresa:8080
  ```
- El validador PBIR es **opcional**: si npm no puede salir, la instalación
  termina igual y el status lo dice en `validator.state`.

**Este procedimiento no se ha ejecutado contra un proxy real.** Es el gate G4.7
y está en [`audits/EXTERNAL_GATES.md`](audits/EXTERNAL_GATES.md).

---

## 7. Offline

Una instalación normal descarga de **cuatro** sitios: PyPI, nuget.org,
developer.microsoft.com y registry.npmjs.org. En una máquina sin salida directa
no hay nada que hacer con el instalador de siempre. El bundle es la alternativa:
un archivo que ya lleva las cuatro cosas, verificado por SHA-256.

### 7.1 Construirlo (en una máquina CON red)

```bash
python scripts/bundle.py construir --salida C:\entrega
```

Deja `horizun-pbi-mcp-<version>-bundle.zip` con las ruedas del lock de **este**
intérprete, el paquete propio, las DLL de Analysis Services, los esquemas PBIR y
el tarball del validador. Cada archivo con su SHA-256 en `bundle.json`, y el
hash del manifiesto aparte en `bundle.json.sha256` —un manifiesto que se
verifica a sí mismo no verifica nada—.

Para armar solo una parte:

```bash
python scripts/bundle.py construir --salida C:\entrega --componentes wheelhouse libs
```

### 7.2 Verificarlo antes de moverlo

```bash
python scripts/bundle.py verificar C:\entrega\horizun-pbi-mcp-2.0.1-bundle.zip
```

Comprueba tamaño, manifiesto contra su hash, que no haya archivos sin declarar
ni declarados que falten, y el SHA-256 de cada miembro. **No extrae nada.**

### 7.3 Instalarlo (en la máquina SIN red)

```bash
python scripts/bundle.py instalar C:\entrega\horizun-pbi-mcp-2.0.1-bundle.zip --destino %LOCALAPPDATA%\HorizunPbiMcp\plugin\2.0.1
```

Verifica **entero antes de escribir un solo byte**, extrae a un staging, relee
del disco lo escrito y solo entonces promueve, con el mismo ciclo de vida que el
resto: journal, `.previous-` y recuperación. Si algo falla, lo que hubiera
instalado sigue exactamente donde estaba.

### 7.4 Lo que este procedimiento **no** demuestra

Las pruebas prohíben abrir un socket y lanzar un proceso durante la instalación,
así que está medido que el código no sale a la red. Lo que no está medido es qué
hace **Windows** con un proxy corporativo mal configurado, o una VM realmente
desconectada: eso es lo único que queda de G4.7 y sigue en
[`audits/EXTERNAL_GATES.md`](audits/EXTERNAL_GATES.md).

### 7.5 La alternativa de antes, por si acaso

Copiar el directorio de versión entero
(`%LOCALAPPDATA%\HorizunPbiMcp\plugin\2.0.1\`) sigue funcionando, **pero solo si
el data root es idéntico en las dos máquinas**: un venv de Python no es
relocalizable. El bundle no tiene esa limitación.

---

## 8. Revertir lo que el instalador dejó permanente

Dos cosas sobreviven a la instalación y el instalador **no las deshace**. Están
aquí porque un cambio permanente que nadie documenta es un cambio que nadie
puede revertir.

**Política de ejecución de PowerShell.** Si estaba en `Restricted`, `Undefined`
o `AllSigned`, el instalador la pone en `RemoteSigned` **para tu usuario**
—nunca para la máquina, y nunca con elevación—. No se revierte al terminar
porque Claude necesita ejecutar sus propios guiones. Para ver dónde está y
volver atrás:

```powershell
Get-ExecutionPolicy -Scope CurrentUser
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy Restricted
```

Después de revertirla, Claude Code puede dejar de funcionar. Es una decisión
tuya, no un descuido del instalador.

**Paquetes instalados por `winget`.** Python, Node y Claude Code se instalan a
nivel de usuario. Cuando un paquete no publica un instalador etiquetado como
*user*, winget responde `0x8A150044` y el instalador **lo anuncia y reintenta**
con el instalador por defecto, que normalmente instala en tu perfil igual — y
después **comprueba dónde aterrizó** y te lo dice. En ningún caso se pide
administrador.

Si tu equipo exige user-scope estricto y prefiere fallar antes que instalar
fuera del perfil:

```powershell
.\instalar.ps1 -SoloUserScope
```

Para quitar un paquete: `winget uninstall <nombre>`, uno a uno.

---

## 9. Qué hacer con un `install.log`

```
%LOCALAPPDATA%\HorizunPbiMcp\plugin\<version>\install.log
```

Es la salida completa del instalador en segundo plano. **No lleva secretos ni
rutas de tus proyectos** —la redacción de telemetría se encarga—, así que se
puede adjuntar a una incidencia tal cual.

Lo primero que hay que buscar ahí es el `step` en el que se quedó: `python-runtime`,
`python-packages`, `analysis-services`, `pbir-schemas`, `report-validator`,
`healthcheck` o `promotion`. Cada uno tiene una causa típica distinta, y los
cuatro del medio son descargas — donde una carrera DNS IPv6 medida por el equipo
las tumba de forma intermitente. Reintentar es gratis: todo se verifica por hash.
