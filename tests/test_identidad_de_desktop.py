"""Quien es cada instancia de Power BI Desktop, y con que evidencia.

El listado publicaba `pid` y ese `pid` era el de `msmdsrv.exe`, el motor
tabular, no el de `PBIDesktop.exe`. Son procesos distintos: el motor es hijo
de la ventana. Confundirlos lleva a cerrar el proceso equivocado y, peor, a
dar por buena para una ruta una instancia que esta sirviendo otro archivo solo
porque aparecio mientras esperabamos.

Aqui se sustituyen psutil y la enumeracion de ventanas por dobles: lo que se
prueba es QUE se correlaciona y que se afirma con cada pieza de evidencia, no
la API de Windows.
"""
from __future__ import annotations


import pytest

from horizun_pbi_mcp.powerbi import desktop_identity as di


class _Proc:
    """Doble de psutil.Process con solo lo que el modulo consulta."""

    def __init__(self, pid, nombre, *, padres=(), archivos=(), creado=1000.0):
        self.pid = pid
        self._nombre = nombre
        self._padres = list(padres)
        self._archivos = list(archivos)
        self._creado = creado

    def name(self):
        return self._nombre

    def parents(self):
        return self._padres

    def create_time(self):
        return self._creado

    def open_files(self):
        return [type("H", (), {"path": p})() for p in self._archivos]


@pytest.fixture
def procesos(monkeypatch):
    """Instala un arbol de procesos falso: motor -> ventana."""
    registro = {}

    def _instalar(*, engine_pid=4444, desktop_pid=1111, archivos=(),
                  titulos=(), creado=1000.0, con_padre=True):
        ventana = _Proc(desktop_pid, "PBIDesktop.exe", archivos=archivos,
                        creado=creado)
        motor = _Proc(engine_pid, "msmdsrv.exe",
                      padres=[ventana] if con_padre else [])
        registro[engine_pid] = motor
        registro[desktop_pid] = ventana

        class _PsutilFalso:
            NoSuchProcess = LookupError
            AccessDenied = PermissionError

            @staticmethod
            def Process(pid):
                if int(pid) not in registro:
                    raise LookupError(pid)
                return registro[int(pid)]

        import sys
        monkeypatch.setitem(sys.modules, "psutil", _PsutilFalso)
        monkeypatch.setattr(di, "titulos_de_ventana",
                            lambda pid: list(titulos))
        return {"pid": engine_pid, "port": 55001}
    return _instalar


# ================================================ el motor NO es la ventana ===
def test_el_pid_del_motor_y_el_de_la_ventana_no_se_confunden(procesos):
    instancia = procesos(engine_pid=4444, desktop_pid=1111)
    identidad = di.identify(instancia)

    assert identidad["engine_pid"] == 4444
    assert identidad["desktop_pid"] == 1111
    assert identidad["engine_pid"] != identidad["desktop_pid"]


def test_sin_ventana_antecesora_no_se_afirma_nada(procesos):
    instancia = procesos(con_padre=True)
    instancia_sin_padre = procesos(engine_pid=9999, desktop_pid=8888,
                                   con_padre=False)
    identidad = di.identify(instancia_sin_padre)

    assert identidad["desktop_pid"] is None
    assert identidad["identity_confidence"] == di.UNKNOWN
    assert any(e["signal"] == "desktop_parent" and e["status"] == "not_found"
               for e in identidad["identity_evidence"])
    assert instancia["pid"] != instancia_sin_padre["pid"]


def test_una_instancia_sin_proceso_se_declara_desconocida(procesos):
    procesos()
    identidad = di.identify({"pid": None, "port": 55002})

    assert identidad["identity_confidence"] == di.UNKNOWN
    assert identidad["desktop_pid"] is None
    assert identidad["project_path"] is None


# ================================================== la ruta, cuando se prueba ==
def test_con_un_pbix_abierto_la_ruta_es_un_hecho(procesos, tmp_path):
    informe = tmp_path / "Mi.pbix"
    informe.write_bytes(b"x")
    instancia = procesos(archivos=[str(informe)])

    identidad = di.identify(instancia, target=informe)

    assert identidad["project_path"] == str(informe)
    assert identidad["path_match"] is True
    assert identidad["identity_confidence"] == di.HIGH


def test_otro_documento_nunca_vale_para_la_ruta_pedida(procesos, tmp_path):
    """El caso peligroso: aparecio durante la espera, pero es OTRO informe."""
    abierto = tmp_path / "Otro.pbix"
    abierto.write_bytes(b"x")
    pedido = tmp_path / "Mi.pbix"
    pedido.write_bytes(b"x")
    instancia = procesos(archivos=[str(abierto)])

    identidad = di.identify(instancia, target=pedido)

    assert identidad["path_match"] is False
    assert identidad["identity_confidence"] == di.HIGH
    assert identidad["project_path"] == str(abierto)


def test_un_pbip_no_puede_demostrar_su_ruta(procesos, tmp_path):
    """Desktop no deja descriptor sobre la carpeta de un .pbip.

    El titulo da el NOMBRE, nunca la ruta: por eso `project_path` sigue en
    null y la confianza baja a media en vez de inventarse la carpeta.
    """
    proyecto = tmp_path / "Tablero.pbip"
    proyecto.write_text("{}", encoding="utf-8")
    instancia = procesos(archivos=[], titulos=["Tablero"])

    identidad = di.identify(instancia, target=proyecto)

    assert identidad["project_path"] is None
    assert identidad["desktop_window_title"] == "Tablero"
    assert identidad["path_match"] is True
    assert identidad["identity_confidence"] == di.MEDIUM


def test_un_titulo_que_solo_contiene_el_nombre_no_cuenta(procesos, tmp_path):
    proyecto = tmp_path / "Tablero.pbip"
    proyecto.write_text("{}", encoding="utf-8")
    instancia = procesos(archivos=[], titulos=["Tablero de Obra - copia"])

    identidad = di.identify(instancia, target=proyecto)

    assert identidad["path_match"] is False


def test_sin_descriptor_ni_titulo_la_coincidencia_es_indeterminada(procesos,
                                                                   tmp_path):
    proyecto = tmp_path / "Tablero.pbip"
    proyecto.write_text("{}", encoding="utf-8")
    instancia = procesos(archivos=[], titulos=[])

    identidad = di.identify(instancia, target=proyecto)

    assert identidad["path_match"] is None
    assert identidad["identity_confidence"] == di.LOW


def test_sin_objetivo_no_se_evalua_coincidencia(procesos, tmp_path):
    informe = tmp_path / "Mi.pbix"
    informe.write_bytes(b"x")
    identidad = di.identify(procesos(archivos=[str(informe)]))

    assert identidad["path_match"] is None
    assert identidad["project_path"] == str(informe)


# ============================================================== la evidencia ==
def test_toda_afirmacion_viene_con_su_evidencia(procesos, tmp_path):
    informe = tmp_path / "Mi.pbix"
    informe.write_bytes(b"x")
    identidad = di.identify(procesos(archivos=[str(informe)],
                                     titulos=["Mi - Power BI Desktop"]),
                            target=informe)

    senales = {e["signal"] for e in identidad["identity_evidence"]}
    assert {"engine_pid", "desktop_parent", "window_title", "open_document",
            "path_match"} <= senales


def test_annotate_conserva_los_campos_de_siempre(procesos, tmp_path):
    instancia = procesos()
    instancia.update({"host": "localhost", "catalog": "abc",
                      "table_count": 7, "status": "ok"})

    salida = di.annotate([instancia])[0]

    assert salida["host"] == "localhost"
    assert salida["catalog"] == "abc"
    assert salida["table_count"] == 7
    assert salida["port"] == 55001
    assert salida["identity_confidence"] in (di.HIGH, di.MEDIUM, di.LOW,
                                             di.UNKNOWN)


def test_un_fallo_al_identificar_no_tumba_el_listado(monkeypatch):
    def _explota(*_a, **_k):
        raise RuntimeError("psutil no disponible")

    monkeypatch.setattr(di, "identify", _explota)
    salida = di.annotate([{"pid": 1, "port": 5555, "catalog": "x"}])[0]

    assert salida["catalog"] == "x"
    assert salida["identity_confidence"] == di.UNKNOWN


# ================================= el lanzador descarta lo que sirve otra cosa =
def test_el_lanzador_descarta_una_instancia_de_otro_documento(monkeypatch,
                                                              tmp_path):
    from horizun_pbi_mcp.powerbi import desktop_launcher

    otro = tmp_path / "Otro.pbix"
    pedido = tmp_path / "Mi.pbix"
    for f in (otro, pedido):
        f.write_bytes(b"x")

    monkeypatch.setattr(
        di, "identify",
        lambda inst, target=None: {"project_path": str(otro),
                                   "path_match": False})
    assert desktop_launcher._sirve_otro_documento({"pid": 1, "port": 1},
                                                  pedido) is True


def test_el_lanzador_no_descarta_lo_que_no_puede_demostrar(monkeypatch,
                                                           tmp_path):
    """Con un .pbip no hay descriptor: no se descarta por falta de prueba."""
    from horizun_pbi_mcp.powerbi import desktop_launcher

    pedido = tmp_path / "Mi.pbip"
    pedido.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        di, "identify",
        lambda inst, target=None: {"project_path": None, "path_match": None})

    assert desktop_launcher._sirve_otro_documento({"pid": 1, "port": 1},
                                                  pedido) is False
