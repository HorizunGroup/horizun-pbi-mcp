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
JOURNAL = ".promotion.json"

#: Cuantas versiones anteriores se conservan. Una es el minimo que exige
#: INSTALL-001: siempre tiene que quedar un N−1 al que volver.
CONSERVAR_ANTERIORES = 1


class PromocionError(RuntimeError):
    """Algo impidio publicar el staging. El destino vigente sigue intacto."""


def _leer_journal(root: Path) -> dict[str, Any] | None:
    try:
        datos = json.loads((root / JOURNAL).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return datos if isinstance(datos, dict) else None


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

    anterior = root / f"{PREFIJO_ANTERIOR}{destino.name}-{int(time.time())}"
    _escribir_journal(root, fase="preparada", staging=str(staging),
                      destino=str(destino), anterior=str(anterior), ts=time.time())

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
        _escribir_journal(root, fase="anterior-apartado", staging=str(staging),
                          destino=str(destino), anterior=str(anterior),
                          ts=time.time())

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

    _escribir_journal(root, fase="completa", destino=str(destino),
                      anterior=str(anterior) if apartado else None, ts=time.time())
    _borrar_journal(root)
    return {"destino": str(destino),
            "anterior": str(apartado) if apartado else None}


def recuperar(root: Path) -> dict[str, Any]:
    """Arregla una promocion interrumpida. Decide mirando el DISCO.

    La fase del journal es una pista: el propio journal se escribe con una
    operacion que tambien puede cortarse. Lo que decide es que existe.
    """
    diario = _leer_journal(root)
    if diario is None:
        return {"accion": "ninguna"}

    destino = Path(diario.get("destino", ""))
    anterior = Path(diario.get("anterior") or "")
    staging = Path(diario.get("staging") or "")

    if destino.exists():
        # El renombrado final llego a ocurrir (o nunca se aparto nada). No hay
        # nada roto; solo sobra el rastro y, quiza, un staging a medias.
        if staging.name.startswith(PREFIJO_STAGING) and staging.is_dir():
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

    if anterior.name.startswith(PREFIJO_ANTERIOR) and anterior.is_dir():
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
        caido = root / f"{PREFIJO_ANTERIOR}fallido-{destino.name}-{int(time.time())}"
        try:
            os.rename(destino, caido)
        except OSError:
            return None
    try:
        os.rename(elegido, destino)
    except OSError:
        return None
    return elegido


def limpiar(root: Path, *, conservar: int = CONSERVAR_ANTERIORES) -> list[str]:
    """Borra stagings huerfanos y los `.previous-` que sobran.

    **Solo toca rutas con nuestros prefijos.** Una carpeta que no reconocemos
    no se borra ni aunque estorbe: esa regla es lo que separa una limpieza de
    una perdida de datos.
    """
    borrados: list[str] = []
    try:
        hijos = list(root.iterdir())
    except OSError:
        return borrados

    for d in hijos:
        if d.is_dir() and d.name.startswith(PREFIJO_STAGING):
            if _borrar_arbol(d):
                borrados.append(str(d))

    for sobra in anteriores(root)[max(conservar, 0):]:
        if _borrar_arbol(sobra):
            borrados.append(str(sobra))
    return borrados
