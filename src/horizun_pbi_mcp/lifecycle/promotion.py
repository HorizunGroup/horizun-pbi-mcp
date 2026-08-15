"""INSTALL-001 — preparar aparte, verificar, y solo entonces promover.

El defecto: `plugin_bootstrap._semilla()` **movia** `runtime`, `libs`, `schemas`
y `validator` desde la version anterior a la carpeta nueva *antes* de validar
nada. Si un paso posterior fallaba —pip, una descarga, la red— el estado
quedaba en `failed` y el runtime N−1 **ya no existia**: se lo habia llevado la
siembra. La persona se quedaba sin instalacion anterior a la que volver, que es
exactamente lo que una actualizacion no puede hacer.

La forma correcta es la de siempre en este repositorio: preparar en un lado,
verificar, y publicar con un `rename`. Aqui hay un matiz que conviene decir en
voz alta porque decide el diseño: **promover un directorio no es una operacion
atomica**. `os.replace` sobre un directorio que ya existe falla en Windows, asi
que hacen falta dos renombrados —apartar el vigente, poner el nuevo— y entre
ellos hay un instante en el que el destino no existe. Un corte de luz ahi
dejaria la instalacion sin runtime.

Por eso la promocion lleva **journal**, y la recuperacion decide mirando el
disco y no solo lo que diga el journal: un journal se escribe con un `write`
que tambien puede interrumpirse, asi que la fase anotada es una pista, no una
verdad. Lo que manda es que exista el destino, el apartado o el staging.

Nada de esto necesita dependencias: se carga con el Python anfitrion desde
`scripts/plugin_bootstrap.py` antes de que exista ningun entorno, y tambien
desde el paquete instalado. Es la misma implementacion en los dos caminos.
"""
from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

#: Prefijos reconocibles. Solo se borra lo que lleva uno de estos: una ruta que
#: no reconocemos no se toca ni para limpiar.
PREFIJO_STAGING = ".staging-"
PREFIJO_ANTERIOR = ".previous-"
PREFIJO_CUARENTENA = ".promotion-rechazada-"
JOURNAL = ".promotion.json"

#: Version del formato del journal. Se comprueba al leer y se rechaza lo que no
#: cuadre -viejo o del futuro-. Un journal que no sabemos interpretar no es una
#: pista: es una razon para no tocar nada.
#:
#: 1 -> rutas ABSOLUTAS de staging/destino/anterior. Retirado por INSTALL-011:
#:      un archivo en disco decidia a que ruta le hacia `os.rename` el
#:      instalador, y se demostro moviendo una carpeta fuera de la raiz.
#: 2 -> solo NOMBRES de hijos directos de la raiz, validados al leer.
ESQUEMA_JOURNAL = 2

#: Las unicas fases que pueden sobrevivir en disco. `completa` no esta: se
#: escribe y se borra en el mismo suspiro, asi que encontrarla significa que
#: alguien la puso a mano.
FASES_RECUPERABLES = ("preparada", "anterior-apartado")

#: Cuantas versiones anteriores se conservan. Una es el minimo que exige
#: INSTALL-001: siempre tiene que quedar un N−1 al que volver.
CONSERVAR_ANTERIORES = 1


class PromocionError(RuntimeError):
    """Algo impidio publicar el staging. El destino vigente sigue intacto."""


class JournalInvalido(PromocionError):
    """El journal no se puede interpretar. No se toca nada de lo que menciona."""


def _es_nombre_simple(valor: Any) -> bool:
    """¿Es UN componente de ruta, sin trucos?

    Se comprueban los separadores de los DOS sistemas a proposito: en Windows
    `/` tambien separa, y en POSIX `\\` es un caracter valido dentro de un
    nombre, asi que mirar solo `os.sep` dejaria pasar precisamente el que no
    corresponde a la maquina donde se escribio el journal. Los dos puntos van
    aparte porque en Windows abren dos puertas distintas: la unidad (`C:`) y
    los flujos de datos alternos (`archivo:oculto`).
    """
    if not isinstance(valor, str) or not valor or len(valor) > 255:
        return False
    if valor in (".", ".."):
        return False
    if "/" in valor or "\\" in valor or ":" in valor:
        return False
    if valor != valor.strip():
        # NTFS recorta los espacios de los extremos al crear: el nombre que se
        # valida y el que acaba en disco dejarian de ser el mismo.
        return False
    if os.path.isabs(valor) or os.path.splitdrive(valor)[0]:
        return False
    return True


def bajo_root(root: Path, nombre: Any, *, que: str) -> Path:
    """`root/nombre`, comprobado LEXICA y RESUELTAMENTE como hijo directo.

    Las dos comprobaciones hacen falta y ninguna sustituye a la otra. La lexica
    rechaza `..`, separadores y rutas absolutas sin tocar el disco. La resuelta
    rechaza lo que el disco puede estar escondiendo: `.staging-x` es un nombre
    de hijo directo impecable y, si ademas es una junction, seguirlo saca la
    operacion de la raiz sin que ningun `..` llegue a aparecer en el journal.
    """
    if not _es_nombre_simple(nombre):
        raise JournalInvalido(f"{que}={nombre!r} no es el nombre de un hijo directo")
    ruta = root / nombre
    if ruta.is_symlink():
        raise JournalInvalido(f"{que}={nombre!r} es un enlace: no se sigue")
    try:
        real, raiz_real = ruta.resolve(), root.resolve()
    except OSError as exc:                                  # pragma: no cover
        raise JournalInvalido(f"no se pudo resolver {que}={nombre!r}: {exc}") from exc
    if real.parent != raiz_real:
        raise JournalInvalido(
            f"{que}={nombre!r} se resuelve fuera de {raiz_real}: {real}")
    return ruta


def _interpretar_journal(root: Path, datos: dict[str, Any]) -> dict[str, Any]:
    """Traduce el journal a rutas CONTENIDAS, o lanza sin haber tocado nada."""
    if datos.get("esquema") != ESQUEMA_JOURNAL:
        raise JournalInvalido(
            f"esquema {datos.get('esquema')!r}; este binario entiende "
            f"{ESQUEMA_JOURNAL}")
    fase = datos.get("fase")
    if fase not in FASES_RECUPERABLES:
        raise JournalInvalido(f"fase {fase!r} no recuperable")

    destino = bajo_root(root, datos.get("destino"), que="destino")
    if destino.name.startswith((PREFIJO_STAGING, PREFIJO_ANTERIOR)):
        raise JournalInvalido(
            f"destino={destino.name!r} lleva un prefijo reservado")

    staging = bajo_root(root, datos.get("staging"), que="staging")
    if not staging.name.startswith(PREFIJO_STAGING):
        raise JournalInvalido(f"staging={staging.name!r} sin el prefijo {PREFIJO_STAGING!r}")

    anterior = None
    if datos.get("anterior") is not None:
        anterior = bajo_root(root, datos["anterior"], que="anterior")
        if not anterior.name.startswith(PREFIJO_ANTERIOR):
            raise JournalInvalido(
                f"anterior={anterior.name!r} sin el prefijo {PREFIJO_ANTERIOR!r}")

    return {"fase": fase, "destino": destino, "staging": staging,
            "anterior": anterior}


def _leer_journal(root: Path) -> dict[str, Any] | None:
    try:
        datos = json.loads((root / JOURNAL).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return datos if isinstance(datos, dict) else None


def _poner_en_cuarentena(root: Path) -> str | None:
    """Aparta el journal ilegible SIN salir de la raiz.

    Borrarlo destruiria la unica pista de lo que paso; dejarlo donde esta hace
    que el siguiente arranque lo vuelva a leer y a rechazar. Se aparta.
    """
    apartado = root / f"{PREFIJO_CUARENTENA}{uuid.uuid4().hex[:12]}.json"
    try:
        os.replace(root / JOURNAL, apartado)
    except OSError:                                          # pragma: no cover
        return None
    return str(apartado)


def _escribir_journal(root: Path, **valores: Any) -> None:
    root.mkdir(parents=True, exist_ok=True)
    destino = root / JOURNAL
    tmp = destino.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(valores, indent=2), encoding="utf-8")
    os.replace(tmp, destino)


def _borrar_journal(root: Path) -> None:
    try:
        (root / JOURNAL).unlink()
    except FileNotFoundError:
        pass


def _borrar_arbol(ruta: Path) -> bool:
    try:
        shutil.rmtree(ruta)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


def crear_staging(root: Path, version: str) -> Path:
    """Un directorio hermano del destino, en el MISMO volumen.

    Hermano a proposito: `os.rename` entre volumenes no existe, asi que un
    staging en `%TEMP%` obligaria a copiar en la promocion y la promocion
    dejaria de ser barata y reversible.
    """
    root.mkdir(parents=True, exist_ok=True)
    staging = root / f"{PREFIJO_STAGING}{version}-{uuid.uuid4().hex[:12]}"
    staging.mkdir(parents=True)
    return staging


def semillar(staging: Path, donante: Path, componentes: tuple[str, ...]) -> list[str]:
    """Copia lo reaprovechable del donante al staging. **Nunca lo mueve.**

    Esta es la correccion de INSTALL-001 en una linea: `shutil.copytree` en vez
    de `shutil.move`. Cuesta espacio en disco durante la actualizacion y a
    cambio el runtime anterior sigue entero y arrancable mientras el nuevo se
    prepara. Si algo falla, no hay nada que restaurar porque no se toco nada.

    En Windows se intenta primero un clon copy-on-write; si el sistema de
    archivos no lo soporta, `copytree` normal. No se usan hardlinks: un
    hardlink haria que escribir en el staging modificara el donante, que es el
    fallo que se esta corrigiendo.
    """
    copiados: list[str] = []
    for nombre in componentes:
        origen, destino = donante / nombre, staging / nombre
        if not origen.is_dir() or destino.exists():
            continue
        try:
            shutil.copytree(origen, destino, symlinks=False,
                            ignore_dangling_symlinks=True)
        except OSError:
            _borrar_arbol(destino)
            continue
        copiados.append(nombre)
    return copiados


def promover(root: Path, staging: Path, destino: Path) -> dict[str, Any]:
    """Publica el staging como destino vigente, conservando el anterior.

    Secuencia, y cada paso deja rastro antes de hacerse:

      1. journal `preparada`
      2. si el destino existe -> renombrarlo a `.previous-<...>`
      3. journal `anterior-apartado`
      4. renombrar staging -> destino
      5. journal `completa`, y se borra

    El unico hueco es entre 2 y 4, y de ese hueco sale `recuperar()`.
    """
    if not staging.is_dir():
        raise PromocionError(f"el staging {staging} no existe")
    # El journal solo guarda NOMBRES, asi que las dos rutas tienen que ser
    # hijas directas de la raiz o no habria nada que anotar. Comprobarlo aqui
    # convierte un error de programacion en un fallo inmediato y legible, en
    # vez de en un journal que la recuperacion rechazara mucho despues.
    for etiqueta, ruta in (("staging", staging), ("destino", destino)):
        if ruta.parent != root:
            raise PromocionError(
                f"el {etiqueta} {ruta} no cuelga de {root}: la promocion solo "
                "publica dentro del directorio de datos")

    # UUID ademas del segundo: dos promociones dentro del mismo segundo
    # producian el MISMO nombre. En Windows el segundo `os.rename` fallaba y
    # tumbaba la actualizacion; en POSIX habria sobrescrito el N−1 anterior,
    # que es peor porque no se nota.
    anterior = (root / f"{PREFIJO_ANTERIOR}{destino.name}-{int(time.time())}"
                f"-{uuid.uuid4().hex[:12]}")
    _escribir_journal(root, esquema=ESQUEMA_JOURNAL, fase="preparada",
                      staging=staging.name, destino=destino.name,
                      anterior=anterior.name, ts=time.time())

    apartado = None
    if destino.exists():
        try:
            os.rename(destino, anterior)
        except OSError as exc:
            _borrar_journal(root)
            raise PromocionError(
                f"no se pudo apartar el runtime vigente ({exc}). No se toco "
                "nada: la instalacion anterior sigue en su sitio.") from exc
        apartado = anterior
        _escribir_journal(root, esquema=ESQUEMA_JOURNAL, fase="anterior-apartado",
                          staging=staging.name, destino=destino.name,
                          anterior=anterior.name, ts=time.time())

    try:
        os.rename(staging, destino)
    except OSError as exc:
        # Deshacer: devolver el anterior a su sitio. Si esto tambien falla, el
        # estado queda descrito en el journal y `recuperar()` lo reintenta al
        # siguiente arranque en vez de dejarlo mudo.
        if apartado is not None:
            try:
                os.rename(apartado, destino)
            except OSError:
                raise PromocionError(
                    f"no se pudo publicar el staging ({exc}) NI devolver el "
                    f"runtime anterior. Esta descrito en {root / JOURNAL} y se "
                    "recupera al siguiente arranque.") from exc
        _borrar_journal(root)
        raise PromocionError(
            f"no se pudo publicar el staging ({exc}). El runtime anterior "
            "quedo restaurado y sigue siendo utilizable.") from exc

    _escribir_journal(root, esquema=ESQUEMA_JOURNAL, fase="completa",
                      destino=destino.name,
                      anterior=anterior.name if apartado else None, ts=time.time())
    _borrar_journal(root)
    return {"destino": str(destino),
            "anterior": str(apartado) if apartado else None}


def recuperar(root: Path) -> dict[str, Any]:
    """Arregla una promocion interrumpida. Decide mirando el DISCO.

    La fase del journal es una pista: el propio journal se escribe con una
    operacion que tambien puede cortarse. Lo que decide es que existe.

    INSTALL-011 — el journal dejo de ser autoridad sobre RUTAS. Antes se sacaban
    `staging`, `destino` y `anterior` como rutas absolutas y se usaban tal cual,
    de modo que un archivo del directorio de datos decidia a que carpeta le
    hacia `os.rename` un proceso que normalmente arranca solo, sin nadie
    delante. Se demostro moviendo una carpeta a una hermana de la raiz. Ahora el
    journal guarda solo NOMBRES y aqui se reconstruyen bajo la raiz y se validan.

    **Quien llama tiene que tener el cerrojo del ciclo de vida.** Esto renombra
    el runtime vigente; hacerlo fuera del cerrojo era la otra mitad del defecto.
    """
    root = Path(root)
    crudo = _leer_journal(root)
    if crudo is None:
        if (root / JOURNAL).exists():
            # Existe pero no es un objeto JSON legible: tampoco se adivina.
            return {"accion": "journal-invalido",
                    "motivo": "el journal no es un objeto JSON legible",
                    "cuarentena": _poner_en_cuarentena(root)}
        return {"accion": "ninguna"}

    try:
        plan = _interpretar_journal(root, crudo)
    except JournalInvalido as exc:
        # No se toca NADA de lo que menciona: si no se puede interpretar, no se
        # sabe que significan sus rutas. La evidencia se aparta dentro de la
        # raiz para que se pueda diagnosticar y no se relea en cada arranque.
        return {"accion": "journal-invalido", "motivo": str(exc),
                "cuarentena": _poner_en_cuarentena(root)}

    destino, staging, anterior = plan["destino"], plan["staging"], plan["anterior"]

    if destino.exists():
        # El renombrado final llego a ocurrir (o nunca se aparto nada). No hay
        # nada roto; solo sobra el rastro y, quiza, un staging a medias.
        if staging.is_dir():
            _borrar_arbol(staging)
        _borrar_journal(root)
        return {"accion": "completada", "destino": str(destino)}

    if staging.is_dir():
        try:
            os.rename(staging, destino)
        except OSError:
            pass
        else:
            _borrar_journal(root)
            return {"accion": "reintentada", "destino": str(destino)}

    if anterior is not None and anterior.is_dir():
        try:
            os.rename(anterior, destino)
        except OSError as exc:
            raise PromocionError(
                f"promocion interrumpida y no se pudo devolver {anterior} a "
                f"{destino}: {exc}") from exc
        _borrar_journal(root)
        return {"accion": "revertida", "destino": str(destino)}

    _borrar_journal(root)
    return {"accion": "sin-rastro-utilizable"}


def anteriores(root: Path) -> list[Path]:
    """Los N−1 conservados, del mas reciente al mas viejo."""
    try:
        hijos = list(root.iterdir())
    except OSError:
        return []
    encontrados = [d for d in hijos
                   if d.is_dir() and d.name.startswith(PREFIJO_ANTERIOR)]
    return sorted(encontrados, key=lambda d: d.name, reverse=True)


def restaurar_anterior(root: Path, destino: Path) -> Path | None:
    """Vuelve al ultimo runtime bueno conservado. Devuelve cual se restauro."""
    candidatos = [d for d in anteriores(root) if (d / "runtime").is_dir()]
    if not candidatos:
        return None
    elegido = candidatos[0]
    if destino.exists():
        caido = (root / f"{PREFIJO_ANTERIOR}fallido-{destino.name}-"
                 f"{int(time.time())}-{uuid.uuid4().hex[:12]}")
        try:
            os.rename(destino, caido)
        except OSError:
            return None
    try:
        os.rename(elegido, destino)
    except OSError:
        return None
    return elegido


def limpiar_apartados_de(root: Path, nombre: str) -> list[str]:
    """Borra los `.previous-` de UN destino concreto, y solo los de ese.

    Existe aparte de `limpiar()` por una razon de seguridad, no de comodidad.
    Los publicadores de componentes -esquemas, validador- promueven dentro de
    una raiz que puede ser la MISMA del ciclo de vida del runtime: basta que
    alguien pase `--dest <raiz>/schemas`. Barrer ahi todos los `.previous-` se
    llevaria por delante el que guarda el N−1, o sea la unica instalacion a la
    que se puede volver. Filtrar por el nombre del destino es la diferencia
    entre recoger lo propio y borrar lo ajeno.

    Se llama DESPUES de publicar: la ventana en la que el apartado hacia falta
    -entre los dos renombrados- se cerro con el `rename`. Dejarlo solo garantiza
    que la siguiente actualizacion encuentre dos, y la siguiente tres.
    """
    prefijo = f"{PREFIJO_ANTERIOR}{nombre}-"
    borrados: list[str] = []
    try:
        hijos = list(root.iterdir())
    except OSError:                                          # pragma: no cover
        return borrados
    for d in hijos:
        if d.is_dir() and d.name.startswith(prefijo) and _borrar_arbol(d):
            borrados.append(str(d))
    return borrados


def limpiar(root: Path, *, conservar: int = CONSERVAR_ANTERIORES,
            proteger: "set[Path] | None" = None) -> list[str]:
    """Borra stagings huerfanos y los `.previous-` que sobran.

    **Solo toca rutas con nuestros prefijos.** Una carpeta que no reconocemos
    no se borra ni aunque estorbe: esa regla es lo que separa una limpieza de
    una perdida de datos.

    `proteger` lleva las rutas que no se borran pase lo que pase. La usa quien
    conoce el last-known-good: conservar "el mas reciente por nombre" no basta
    -`.previous-fallido-...` ordena por delante de `.previous-2.0.0-...`- y
    borrar el N−1 bueno por un criterio de ordenacion seria justo el fallo que
    todo esto existe para evitar.
    """
    intocables = {p.resolve() for p in (proteger or set())}
    borrados: list[str] = []
    try:
        hijos = list(root.iterdir())
    except OSError:
        return borrados

    for d in hijos:
        if d.is_dir() and d.name.startswith(PREFIJO_STAGING):
            if d.resolve() in intocables:
                continue
            if _borrar_arbol(d):
                borrados.append(str(d))

    sobran = [d for d in anteriores(root) if d.resolve() not in intocables]
    for sobra in sobran[max(conservar, 0):]:
        if _borrar_arbol(sobra):
            borrados.append(str(sobra))
    return borrados
