"""Fase J3 — descarga determinista y verificada de las DLL.

`latest_stable()` preguntaba a NuGet cual era la ultima version y se la
tragaba, sin hash y sin comprobar nada: dos instalaciones del mismo commit
podian acabar con DLL distintas. Y extraia directamente sobre `libs/`, asi que
un fallo a medias dejaba una mezcla de dos versiones.

Nada aqui toca la red ni el `libs/` real: la descarga se sustituye por un
paquete sintetico.
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def cargar_script():
    ruta = REPO / "scripts" / "fetch_libs.py"
    spec = importlib.util.spec_from_file_location("fetch_libs_bajo_prueba", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture
def fl(tmp_path, monkeypatch):
    """El script con libs/ y manifiesto redirigidos a tmp_path."""
    modulo = cargar_script()
    monkeypatch.setattr(modulo, "LIBS", tmp_path / "libs")
    monkeypatch.setattr(modulo, "MANIFIESTO", tmp_path / "libs_manifest.json")
    return modulo


def paquete_falso(dlls: dict, tfm: str = "lib/net45") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("package.nuspec", "<package/>")
        for nombre, contenido in dlls.items():
            z.writestr(f"{tfm}/{nombre}", contenido)
    return buf.getvalue()


def dlls_completas(fl):
    return {d: f"contenido de {d}".encode() for d in fl.DLLS_OBLIGATORIAS}


@pytest.fixture
def con_paquete(fl, monkeypatch):
    """Sustituye la descarga por un paquete sintetico configurable."""
    def _instalar(datos=None, **kwargs):
        contenido = datos if datos is not None else paquete_falso(
            dlls_completas(fl), **kwargs)
        monkeypatch.setattr(fl, "descargar", lambda _url: contenido)
        return contenido
    return _instalar


def generar(fl, con_paquete):
    con_paquete()
    m = fl.construir_manifiesto()
    fl.MANIFIESTO.write_text(json.dumps(m), encoding="utf-8")
    return m


# ============================================== version fijada, no "latest" ===
def test_no_queda_rastro_de_latest_stable():
    """Se mira el CODIGO, no la prosa: la docstring explica que ya no se usa."""
    import ast

    arbol = ast.parse((REPO / "scripts" / "fetch_libs.py").read_text(
        encoding="utf-8"))
    nombres = {n.name for n in ast.walk(arbol) if isinstance(n, ast.FunctionDef)}
    assert "latest_stable" not in nombres, (
        "vuelve a resolverse la version mas reciente en vez de una fijada")

    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
            if ast.get_docstring(arbol) == nodo.value:
                continue
            assert "v3-flatcontainer" not in nodo.value


def test_la_version_esta_fijada(fl):
    assert fl.VERSION_FIJADA
    for p in fl.PAQUETES:
        assert p["version"] == fl.VERSION_FIJADA
        assert "license" in p, "cada paquete declara su licencia"


def test_la_url_es_determinista(fl):
    url = fl.url_de(fl.PAQUETES[0])
    assert url.startswith(fl.ORIGEN)
    assert fl.VERSION_FIJADA in url


def test_el_manifiesto_declara_lo_esperado(fl, con_paquete):
    m = generar(fl, con_paquete)
    assert m["required_dlls"] == fl.DLLS_OBLIGATORIAS
    assert "NO" in m["redistribution"]
    for p in m["packages"]:
        assert len(p["sha256"]) == 64 and p["bytes"] > 0
    for nombre, ref in m["files"].items():
        assert len(ref["sha256"]) == 64
        assert ref["package"]


# =========================================================== fallo cerrado ====
def test_hash_incorrecto_no_instala_nada(fl, con_paquete, monkeypatch):
    generar(fl, con_paquete)
    con_paquete(datos=paquete_falso(
        {d: b"CONTENIDO DISTINTO" for d in fl.DLLS_OBLIGATORIAS}))

    with pytest.raises(fl.LibsError) as exc:
        fl.instalar()
    assert "HASH DISTINTO" in str(exc.value)
    assert not list((fl.LIBS).glob("*.dll")) if fl.LIBS.exists() else True


def test_descarga_truncada_se_rechaza(fl, monkeypatch):
    monkeypatch.setattr(fl.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("corte")))
    with pytest.raises(fl.LibsError):
        fl.descargar(fl.ORIGEN + "x/1.0")


def test_paquete_vacio_se_rechaza(fl, monkeypatch):
    class Falsa:
        def read(self, _n=None):
            return b"x" * 10

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(fl.urllib.request, "urlopen", lambda *a, **k: Falsa())
    with pytest.raises(fl.LibsError) as exc:
        fl.descargar(fl.ORIGEN + "x/1.0")
    assert "truncada" in str(exc.value)


def test_un_zip_corrupto_se_rechaza(fl):
    with pytest.raises(fl.LibsError) as exc:
        fl.dlls_del_paquete(b"esto no es un zip", "paquete")
    assert "zip valido" in str(exc.value)


def test_un_paquete_sin_dlls_se_rechaza(fl):
    vacio = paquete_falso({})
    with pytest.raises(fl.LibsError) as exc:
        fl.dlls_del_paquete(vacio, "paquete")
    assert "ninguna DLL" in str(exc.value)


def test_un_tfm_incompatible_se_rechaza(fl):
    otro = paquete_falso(dlls_completas(fl), tfm="lib/net9.0")
    with pytest.raises(fl.LibsError) as exc:
        fl.dlls_del_paquete(otro, "paquete")
    assert "TFM compatible" in str(exc.value)


def test_falta_una_dll_obligatoria(fl, con_paquete):
    incompletas = {d: b"x" for d in fl.DLLS_OBLIGATORIAS[:-1]}
    con_paquete(datos=paquete_falso(incompletas))
    with pytest.raises(fl.LibsError) as exc:
        fl.construir_manifiesto()
    assert "NO traen" in str(exc.value)


def test_origen_no_permitido(fl):
    with pytest.raises(fl.LibsError) as exc:
        fl.descargar("https://ejemplo.invalido/paquete.nupkg")
    assert "no permitido" in str(exc.value)


def test_sin_manifiesto_no_se_instala(fl):
    with pytest.raises(fl.LibsError) as exc:
        fl.instalar()
    assert "--update" in str(exc.value)


# ============================================ instalacion atomica e idempotente ==
def test_instalacion_correcta(fl, con_paquete):
    m = generar(fl, con_paquete)
    r = fl.instalar()

    assert r["installed"] == len(m["files"])
    assert fl.estado()["ready"] is True
    for d in fl.DLLS_OBLIGATORIAS:
        assert (fl.LIBS / d).exists()


def test_reejecucion_idempotente(fl, con_paquete):
    generar(fl, con_paquete)
    fl.instalar()
    huella1 = {f.name: hashlib.sha256(f.read_bytes()).hexdigest()
               for f in fl.LIBS.glob("*.dll")}

    fl.instalar()
    huella2 = {f.name: hashlib.sha256(f.read_bytes()).hexdigest()
               for f in fl.LIBS.glob("*.dll")}
    assert huella2 == huella1


def test_un_fallo_a_medias_no_deja_mezcla(fl, con_paquete, monkeypatch):
    """Con la version anterior, extraer sobre libs/ mezclaba dos versiones."""
    generar(fl, con_paquete)
    fl.instalar()
    antes = {f.name: hashlib.sha256(f.read_bytes()).hexdigest()
             for f in fl.LIBS.glob("*.dll")}

    # La segunda descarga devuelve otro contenido: debe abortar sin tocar libs/
    con_paquete(datos=paquete_falso({d: b"VERSION NUEVA"
                                     for d in fl.DLLS_OBLIGATORIAS}))
    with pytest.raises(fl.LibsError):
        fl.instalar()

    despues = {f.name: hashlib.sha256(f.read_bytes()).hexdigest()
               for f in fl.LIBS.glob("*.dll")}
    assert despues == antes, "libs/ quedo con una mezcla de dos versiones"


def test_fallo_al_promover_restaura_directorio_anterior(
        fl, con_paquete, monkeypatch):
    """El corte entre los renombres no deja ``libs/`` ausente o mezclado."""
    generar(fl, con_paquete)
    fl.instalar()
    antes = {f.name: f.read_bytes() for f in fl.LIBS.glob("*.dll")}

    replace_real = fl.Path.replace
    fallo_inyectado = False

    def replace_con_fallo(path, target):
        nonlocal fallo_inyectado
        if (not fallo_inyectado
                and path.name.startswith(f".{fl.LIBS.name}.stage_")
                and fl.Path(target) == fl.LIBS):
            fallo_inyectado = True
            raise OSError("fallo de promocion inyectado")
        return replace_real(path, target)

    monkeypatch.setattr(fl.Path, "replace", replace_con_fallo)
    with pytest.raises(fl.LibsError) as exc:
        fl.instalar()

    assert "se restauro la anterior" in str(exc.value)
    assert {f.name: f.read_bytes() for f in fl.LIBS.glob("*.dll")} == antes
    assert not fl._ruta_anterior().exists()
    assert not list(fl.LIBS.parent.glob(f".{fl.LIBS.name}.stage_*"))


def test_reintento_recupera_corte_tras_apartar_version_anterior(
        fl, con_paquete):
    generar(fl, con_paquete)
    fl.instalar()
    antes = {f.name: f.read_bytes() for f in fl.LIBS.glob("*.dll")}

    # Estado exacto que deja una terminacion abrupta entre old -> previous y
    # stage -> libs. La ejecucion siguiente debe reconocerlo antes de descargar.
    fl.LIBS.replace(fl._ruta_anterior())
    assert not fl.LIBS.exists()

    fl.instalar()

    assert fl.estado()["ready"] is True
    assert {f.name: f.read_bytes() for f in fl.LIBS.glob("*.dll")} == antes
    assert not fl._ruta_anterior().exists()


def test_el_estado_detecta_una_dll_alterada(fl, con_paquete):
    generar(fl, con_paquete)
    fl.instalar()
    objetivo = fl.LIBS / fl.DLLS_OBLIGATORIAS[0]
    objetivo.write_bytes(b"alterada")

    e = fl.estado()
    assert e["ready"] is False
    assert objetivo.name in e["mismatch"]


def test_el_estado_detecta_una_dll_ausente(fl, con_paquete):
    generar(fl, con_paquete)
    fl.instalar()
    (fl.LIBS / fl.DLLS_OBLIGATORIAS[0]).unlink()

    e = fl.estado()
    assert e["ready"] is False
    assert fl.DLLS_OBLIGATORIAS[0] in e["missing"]


def test_libs_sigue_fuera_de_git():
    gitignore = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert any(l.strip().rstrip("/") == "libs" for l in gitignore.splitlines())


def test_el_manifiesto_real_existe_y_esta_fijado():
    """El del repositorio, no el sintetico de las pruebas."""
    m = json.loads((REPO / "scripts" / "libs_manifest.json").read_text(
        encoding="utf-8"))
    assert m["packages"], "el manifiesto real no tiene paquetes"
    versiones = {p["version"] for p in m["packages"]}
    assert len(versiones) == 1, f"versiones inconsistentes: {versiones}"
    for nombre, ref in m["files"].items():
        assert len(ref["sha256"]) == 64, f"{nombre} sin hash"
