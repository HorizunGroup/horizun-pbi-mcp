"""Fase 1A.2 — `pbi_hide_columns` como lote atomico.

Antes, esta tool llamaba a otra tool decorada con `guard()`: los errores se
convertian en datos, el bucle seguia adelante y el lote devolvia `ok:true` con
los fallos escondidos en `results`. Ahora la logica vive en un servicio sin
decorar y los errores son excepciones que detienen todo.

Las pruebas del modo `live` usan dobles: nunca se toca un modelo real.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
from pathlib import Path

import pytest

from config import ActiveModel
from pbip import model_edit, project_locator
from powerbi import model_writer
from powerbi.errors import PowerBIMCPError, TableNotFoundError, ValidationError
from services import txn as txn_service
from tests.fixtures import synthetic
from tools.model_edit_tools import (BulkApplyFailedError, BulkPartialError,
                                    _apply_both_compensated, _validar_entradas,
                                    hide_columns_service)


def aplicar_both(session, entradas, hidden=True):
    """Invoca DIRECTAMENTE el coordinador compensado.

    La tool publica ya no admite `mode='both'` (lo rechaza la precondicion de
    `services.dual_mode`). El coordinador se conserva como mecanismo interno y
    se prueba aqui de forma directa, que es como debe cubrirse.
    """
    unicas, duplicadas = _validar_entradas(entradas)
    return _apply_both_compensated(session, unicas, entradas, duplicadas, hidden)


# ------------------------------------------------------------------ dobles ---
class FakeColumn:
    def __init__(self, name, hidden=False):
        self.Name = name
        self.IsHidden = hidden


class FakeTable:
    def __init__(self, name, columnas):
        self.Name = name
        self.Columns = [FakeColumn(c) for c in columnas]


class FakeModel:
    """Modelo TOM simulado que cuenta las llamadas a SaveChanges."""

    def __init__(self, tablas, fallar_en_save=False):
        self.Tables = tablas
        self.save_calls = 0
        self._fallar = fallar_en_save

    def SaveChanges(self):
        self.save_calls += 1
        if self._fallar:
            raise RuntimeError("SaveChanges rechazado por el motor")


@pytest.fixture
def modelo_fake(monkeypatch):
    """Sustituye la conexion TOM. Devuelve (instalar, estado)."""
    estado = {"conexiones": 0, "modelo": None}

    def _instalar(tablas, fallar_en_save=False):
        modelo = FakeModel(tablas, fallar_en_save)
        estado["modelo"] = modelo

        @contextlib.contextmanager
        def fake_connect(model):
            estado["conexiones"] += 1
            yield (object(), object(), modelo)

        monkeypatch.setattr(model_writer, "connect", fake_connect)
        return modelo

    return _instalar, estado


@pytest.fixture
def sesion_viva(session):
    """Sesion con un modelo activo ya verificado (no consulta el sistema)."""
    modelo = ActiveModel(host="localhost", port=12345,
                         connection_string="Data Source=localhost:12345",
                         catalog="cat", database_name="cat", model_name="M",
                         pid=1, process_started=1.0, session_fingerprint="fp")
    session.set_active_model(modelo)
    return session


@pytest.fixture
def proyecto(session, tmp_path):
    pbip = synthetic.materialize(tmp_path)
    project_locator.open_project(session, str(pbip))
    return session, session.require_active_pbip(), pbip.parent


def huella(project: Path) -> dict:
    return {str(p.relative_to(project)).replace("\\", "/"):
            hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(project.rglob("*")) if p.is_file()}


def directorios(project: Path) -> set:
    return {str(p.relative_to(project)) for p in project.rglob("*") if p.is_dir()}


def journals(backups: Path) -> list:
    return sorted(backups.rglob("manifest.json"))


def oculta(project: Path, tabla: str, columna: str) -> bool:
    fp = project / "Demo.SemanticModel" / "definition" / "tables" / f"{tabla}.tmdl"
    lineas = fp.read_text(encoding="utf-8").splitlines()
    loc = model_edit._column_block(lineas, columna)
    return any(lineas[k].strip() == "isHidden" for k in range(loc[0], loc[1]))


# ============================================================== validacion ====
def test_columns_debe_ser_lista(proyecto):
    session, _, _ = proyecto
    with pytest.raises(ValidationError):
        hide_columns_service(session, "no soy una lista", True, "pbip")


@pytest.mark.parametrize("entrada", [
    [{"table": "Fact"}],                       # falta column
    [{"column": "Amount"}],                    # falta table
    [{"table": "", "column": "Amount"}],       # vacio
    [{"table": "Fact", "column": "   "}],      # solo espacios
    ["Fact[Amount]"],                          # no es objeto
    [{"table": "Fact", "column": None}],       # tipo invalido
])
def test_entradas_incompletas_se_rechazan(proyecto, entrada):
    session, _, project = proyecto
    antes = huella(project)
    with pytest.raises(ValidationError) as exc:
        hide_columns_service(session, entrada, True, "pbip")
    assert exc.value.details["index"] == 0, (
        "el error debe decir QUE entrada de la lista es invalida, no solo que "
        "algo lo es")
    assert huella(project) == antes, "no se escribe nada si la entrada es invalida"


def test_lista_vacia_conserva_el_comportamiento_previo(proyecto):
    session, _, project = proyecto
    antes = huella(project)
    res = hide_columns_service(session, [], True, "pbip")
    assert res == {"mode": "pbip", "count": 0, "results": [],
                   "duplicates_ignored": []}
    assert huella(project) == antes


def test_modo_invalido_se_rechaza(proyecto):
    session, _, _ = proyecto
    with pytest.raises(ValidationError):
        hide_columns_service(session, [{"table": "Fact", "column": "Amount"}],
                             True, "modo_raro")


# ==================================================================== PBIP ====
def test_varias_columnas_del_mismo_archivo(proyecto, isolated_settings):
    session, active, project = proyecto
    res = hide_columns_service(session, [
        {"table": "Fact", "column": "Amount"},
        {"table": "Fact", "column": "FactID"}], True, "pbip")

    assert res["count"] == 2 and len(res["results"]) == 2
    assert oculta(project, "Fact", "Amount") and oculta(project, "Fact", "FactID")
    assert len(journals(isolated_settings.backups_dir)) == 1, "una transaccion"
    manifest = json.loads(journals(isolated_settings.backups_dir)[0]
                          .read_text(encoding="utf-8"))
    assert len(manifest["files"]) == 1, "un solo archivo TMDL, incluido una vez"


def test_columnas_de_archivos_distintos(proyecto, isolated_settings):
    session, active, project = proyecto
    res = hide_columns_service(session, [
        {"table": "Fact", "column": "Amount"},
        {"table": "Calendar", "column": "Year"}], True, "pbip")

    assert res["count"] == 2
    assert oculta(project, "Fact", "Amount") and oculta(project, "Calendar", "Year")
    assert len(journals(isolated_settings.backups_dir)) == 1, "una sola transaccion"
    manifest = json.loads(journals(isolated_settings.backups_dir)[0]
                          .read_text(encoding="utf-8"))
    assert len(manifest["files"]) == 2, "dos archivos TMDL en la misma transaccion"


def test_columnas_duplicadas_se_agrupan_pero_se_reportan_todas(proyecto):
    session, active, project = proyecto
    res = hide_columns_service(session, [
        {"table": "Fact", "column": "Amount"},
        {"table": "Fact", "column": "Amount"},
        {"table": "Fact", "column": "FactID"}], True, "pbip")

    assert res["count"] == 3, "count = entradas SOLICITADAS"
    assert len(res["results"]) == 3, "una entrada de resultado por cada solicitud"
    assert res["results"][0] == res["results"][1], "el duplicado reporta lo mismo"
    assert len(res["duplicates_ignored"]) == 1
    assert res["duplicates_ignored"][0]["duplica_a"] == 0


def test_tabla_inexistente_no_escribe_nada(proyecto, isolated_settings):
    session, active, project = proyecto
    antes = huella(project)
    with pytest.raises(ValidationError) as exc:
        hide_columns_service(session, [
            {"table": "Fact", "column": "Amount"},
            {"table": "TablaFantasma", "column": "X"}], True, "pbip")
    assert exc.value.details["index"] == 1
    assert exc.value.details["table"] == "TablaFantasma"
    assert huella(project) == antes
    assert journals(isolated_settings.backups_dir) == [], "ni siquiera se abre journal"


@pytest.mark.parametrize("posicion,indice", [("primera", 0), ("intermedia", 1),
                                             ("ultima", 2)])
def test_columna_inexistente_en_cualquier_posicion(proyecto, isolated_settings,
                                                   posicion, indice):
    session, active, project = proyecto
    antes = huella(project)
    entradas = [{"table": "Fact", "column": "Amount"},
                {"table": "Fact", "column": "FactID"},
                {"table": "Calendar", "column": "Year"}]
    entradas[indice] = {"table": entradas[indice]["table"], "column": "NoExiste"}

    with pytest.raises(ValidationError) as exc:
        hide_columns_service(session, entradas, True, "pbip")
    assert exc.value.details["index"] == indice
    assert huella(project) == antes, f"cero escrituras (columna mala en {posicion})"
    assert journals(isolated_settings.backups_dir) == []


@pytest.mark.parametrize("fallo_en", [0, 1])
def test_fallo_al_escribir_un_archivo_revierte_el_lote(proyecto, isolated_settings,
                                                       monkeypatch, fallo_en):
    session, active, project = proyecto
    antes = huella(project)
    dirs_antes = directorios(project)

    original = txn_service.durable_write
    contador = {"n": 0}

    def fake(path, data, validator=None):
        if Path(path).suffix == ".tmdl":
            if contador["n"] == fallo_en:
                contador["n"] += 1
                raise OSError("fallo inyectado")
            contador["n"] += 1
        return original(path, data, validator)

    monkeypatch.setattr(txn_service, "durable_write", fake)

    with pytest.raises(Exception):
        hide_columns_service(session, [
            {"table": "Fact", "column": "Amount"},
            {"table": "Calendar", "column": "Year"}], True, "pbip")

    assert huella(project) == antes, "restauracion byte a byte"
    assert directorios(project) == dirs_antes, "cero directorios huerfanos"
    assert list(project.rglob("*.tmp")) == [], "cero temporales"


def test_cambio_concurrente_antes_de_escribir_aborta(proyecto, monkeypatch):
    session, active, project = proyecto
    calendario = project / "Demo.SemanticModel" / "definition" / "tables" / "Calendar.tmdl"
    fact = project / "Demo.SemanticModel" / "definition" / "tables" / "Fact.tmdl"
    fact_antes = fact.read_bytes()

    original = txn_service.durable_write
    hecho = {"v": False}

    def fake(path, data, validator=None):
        if Path(path).suffix == ".tmdl" and not hecho["v"]:
            hecho["v"] = True
            calendario.write_text("table Calendar\n\tEXTERNO\n", encoding="utf-8")
        return original(path, data, validator)

    monkeypatch.setattr(txn_service, "durable_write", fake)

    with pytest.raises(Exception):
        hide_columns_service(session, [
            {"table": "Fact", "column": "Amount"},
            {"table": "Calendar", "column": "Year"}], True, "pbip")

    assert "EXTERNO" in calendario.read_text(encoding="utf-8"), \
        "el cambio externo se preserva"
    assert fact.read_bytes() == fact_antes, "el ya escrito vuelve a su estado"


def test_conflicto_durante_el_rollback_se_reporta(proyecto, monkeypatch):
    session, active, project = proyecto
    fact = project / "Demo.SemanticModel" / "definition" / "tables" / "Fact.tmdl"

    original = txn_service.durable_write
    contador = {"n": 0}

    def fake(path, data, validator=None):
        if Path(path).suffix == ".tmdl":
            r = original(path, data, validator)
            contador["n"] += 1
            if contador["n"] == 1:
                fact.write_text("table Fact\n\tEXTERNO\n", encoding="utf-8")
            return r
        return original(path, data, validator)

    monkeypatch.setattr(txn_service, "durable_write", fake)

    with pytest.raises(txn_service.RollbackIncompleteError) as exc:
        hide_columns_service(session, [
            {"table": "Fact", "column": "Amount"},
            {"table": "Calendar", "column": "Year"}], True, "pbip")

    assert "EXTERNO" in fact.read_text(encoding="utf-8")
    assert exc.value.details["clean"] is False


def test_mostrar_columna_oculta(proyecto):
    """El sentido inverso tambien funciona en lote."""
    session, active, project = proyecto
    assert oculta(project, "Calendar", "MonthNumber")
    hide_columns_service(session, [
        {"table": "Calendar", "column": "MonthNumber"}], False, "pbip")
    assert not oculta(project, "Calendar", "MonthNumber")


def test_sin_cambios_reales_no_abre_transaccion(proyecto, isolated_settings):
    """Ocultar algo ya oculto no debe escribir ni respaldar."""
    session, active, project = proyecto
    antes = huella(project)
    res = hide_columns_service(session, [
        {"table": "Calendar", "column": "MonthNumber"}], True, "pbip")
    assert res["results"][0]["pbip"]["changed"] is False
    assert huella(project) == antes
    assert journals(isolated_settings.backups_dir) == []


# ==================================================================== LIVE ====
def test_live_un_solo_savechanges_para_n_columnas(sesion_viva, modelo_fake):
    instalar, estado = modelo_fake
    modelo = instalar([FakeTable("Fact", ["Amount", "FactID"]),
                       FakeTable("Calendar", ["Year"])])

    res = hide_columns_service(sesion_viva, [
        {"table": "Fact", "column": "Amount"},
        {"table": "Fact", "column": "FactID"},
        {"table": "Calendar", "column": "Year"}], True, "live")

    assert modelo.save_calls == 1, "un solo SaveChanges para todo el lote"
    assert estado["conexiones"] == 1, "una sola conexion TOM"
    assert res["count"] == 3
    assert all(c.IsHidden for t in modelo.Tables for c in t.Columns)


def test_live_captura_los_valores_previos(sesion_viva, modelo_fake):
    instalar, _ = modelo_fake
    tabla = FakeTable("Fact", ["Amount", "FactID"])
    tabla.Columns[1].IsHidden = True            # ya estaba oculta
    instalar([tabla])

    res = hide_columns_service(sesion_viva, [
        {"table": "Fact", "column": "Amount"},
        {"table": "Fact", "column": "FactID"}], True, "live")

    previos = [r["live"]["before_hidden"] for r in res["results"]]
    cambios = [r["live"]["changed"] for r in res["results"]]
    assert previos == [False, True]
    assert cambios == [True, False]


def test_live_tabla_inexistente_no_llama_savechanges(sesion_viva, modelo_fake):
    instalar, _ = modelo_fake
    modelo = instalar([FakeTable("Fact", ["Amount"])])

    with pytest.raises(TableNotFoundError) as exc:
        hide_columns_service(sesion_viva, [
            {"table": "Fact", "column": "Amount"},
            {"table": "NoExiste", "column": "X"}], True, "live")

    assert modelo.save_calls == 0, "validacion fallida -> cero SaveChanges"
    assert exc.value.details["index"] == 1
    assert not modelo.Tables[0].Columns[0].IsHidden, "nada se modifico"


@pytest.mark.parametrize("indice", [0, 1, 2])
def test_live_columna_inexistente_en_cualquier_posicion(sesion_viva, modelo_fake,
                                                        indice):
    instalar, _ = modelo_fake
    modelo = instalar([FakeTable("Fact", ["Amount", "FactID", "DateKey"])])
    entradas = [{"table": "Fact", "column": "Amount"},
                {"table": "Fact", "column": "FactID"},
                {"table": "Fact", "column": "DateKey"}]
    entradas[indice] = {"table": "Fact", "column": "NoExiste"}

    with pytest.raises(TableNotFoundError) as exc:
        hide_columns_service(sesion_viva, entradas, True, "live")
    assert modelo.save_calls == 0
    assert exc.value.details["index"] == indice
    assert not any(c.IsHidden for c in modelo.Tables[0].Columns)


def test_live_fallo_del_propio_savechanges_se_envuelve(sesion_viva, modelo_fake):
    """Una excepcion cruda del motor se convierte en error de dominio.

    Si se escapara sin envolver, el coordinador de `mode='both'` no podria
    distinguirla y dejaria el disco escrito sin compensar.
    """
    instalar, _ = modelo_fake
    modelo = instalar([FakeTable("Fact", ["Amount"])], fallar_en_save=True)

    with pytest.raises(PowerBIMCPError) as exc:
        hide_columns_service(sesion_viva, [
            {"table": "Fact", "column": "Amount"}], True, "live")
    assert exc.value.code == "live_write_failed"
    assert "SaveChanges rechazado por el motor" in exc.value.message, \
        "se conserva el mensaje original del motor"
    assert exc.value.details["original_type"] == "RuntimeError"
    assert modelo.save_calls == 1, "se intento una vez, no N"


def test_live_lista_vacia_no_conecta(sesion_viva, modelo_fake):
    instalar, estado = modelo_fake
    instalar([FakeTable("Fact", ["Amount"])])
    res = hide_columns_service(sesion_viva, [], True, "live")
    assert res["count"] == 0
    assert estado["conexiones"] == 0, "una lista vacia no abre conexion"


def test_live_duplicados_se_aplican_una_vez(sesion_viva, modelo_fake):
    instalar, _ = modelo_fake
    modelo = instalar([FakeTable("Fact", ["Amount"])])
    res = hide_columns_service(sesion_viva, [
        {"table": "Fact", "column": "Amount"},
        {"table": "Fact", "column": "Amount"}], True, "live")
    assert modelo.save_calls == 1
    assert res["count"] == 2 and len(res["results"]) == 2


# ==================================================================== BOTH ====
def test_both_exito_en_ambos_destinos(proyecto, modelo_fake, isolated_settings):
    session, active, project = proyecto
    instalar, estado = modelo_fake
    modelo = instalar([FakeTable("Fact", ["Amount"]),
                       FakeTable("Calendar", ["Year"])])
    session.set_active_model(ActiveModel(
        host="localhost", port=1, connection_string="cs", catalog="c",
        session_fingerprint="fp"))

    res = aplicar_both(session, [
        {"table": "Fact", "column": "Amount"},
        {"table": "Calendar", "column": "Year"}])

    assert res["consistent"] is True
    assert oculta(project, "Fact", "Amount")
    assert all(c.IsHidden for t in modelo.Tables for c in t.Columns)
    assert modelo.save_calls == 1
    assert len(journals(isolated_settings.backups_dir)) == 1
    for r in res["results"]:
        assert "live" in r and "pbip" in r


def test_both_fallo_de_planificacion_pbip_no_toca_lo_vivo(proyecto, modelo_fake):
    session, active, project = proyecto
    instalar, estado = modelo_fake
    modelo = instalar([FakeTable("Fact", ["Amount", "SoloEnVivo"])])
    session.set_active_model(ActiveModel(host="l", port=1, connection_string="c",
                                         session_fingerprint="fp"))
    antes = huella(project)

    # 'SoloEnVivo' existe en el modelo en vivo pero no en el TMDL.
    with pytest.raises(ValidationError):
        aplicar_both(session, [{"table": "Fact", "column": "SoloEnVivo"}])

    assert modelo.save_calls == 0, "no se toca lo vivo si el disco no valida"
    assert huella(project) == antes


def test_both_fallo_de_planificacion_live_no_toca_el_disco(proyecto, modelo_fake,
                                                           isolated_settings):
    session, active, project = proyecto
    instalar, _ = modelo_fake
    modelo = instalar([FakeTable("Fact", [])])       # sin columnas en vivo
    session.set_active_model(ActiveModel(host="l", port=1, connection_string="c",
                                         session_fingerprint="fp"))
    antes = huella(project)

    with pytest.raises(TableNotFoundError):
        aplicar_both(session, [{"table": "Fact", "column": "Amount"}])

    assert huella(project) == antes, "el disco no se toca"
    assert modelo.save_calls == 0
    assert journals(isolated_settings.backups_dir) == [], "no se abre journal"


def test_both_live_falla_y_el_disco_se_compensa(proyecto, modelo_fake,
                                                isolated_settings):
    """PBIP escrito, live falla: el disco vuelve a su estado original."""
    session, active, project = proyecto
    instalar, _ = modelo_fake
    modelo = instalar([FakeTable("Fact", ["Amount"])], fallar_en_save=True)
    session.set_active_model(ActiveModel(host="l", port=1, connection_string="c",
                                         session_fingerprint="fp"))
    antes = huella(project)

    with pytest.raises(PowerBIMCPError) as exc:
        aplicar_both(session, [{"table": "Fact", "column": "Amount"}])

    # Taxonomia: compensacion LIMPIA -> no es "parcial", no queda nada que
    # arreglar a mano. Decir "partial" aqui induciria a una intervencion inutil.
    assert exc.value.code == "bulk_apply_failed"
    assert isinstance(exc.value, BulkApplyFailedError)
    assert not isinstance(exc.value, BulkPartialError)
    assert exc.value.details["applied_to"] == "ninguno"
    assert exc.value.details["compensation"]["clean"] is True
    assert huella(project) == antes, "el disco se restauro byte a byte"
    assert not oculta(project, "Fact", "Amount")
    manifest = json.loads(journals(isolated_settings.backups_dir)[0]
                          .read_text(encoding="utf-8"))
    assert manifest["status"] == "compensated"


def test_both_compensacion_con_conflicto_se_reporta_sin_ocultarlo(
        proyecto, modelo_fake, monkeypatch):
    """Si alguien toca el TMDL entre la escritura y la compensacion."""
    session, active, project = proyecto
    instalar, _ = modelo_fake
    modelo = instalar([FakeTable("Fact", ["Amount"])], fallar_en_save=True)
    session.set_active_model(ActiveModel(host="l", port=1, connection_string="c",
                                         session_fingerprint="fp"))
    fact = project / "Demo.SemanticModel" / "definition" / "tables" / "Fact.tmdl"

    original = model_writer.set_columns_hidden_bulk

    def fake_live(sess, entries, hidden=True):
        # Cambio externo despues de escribir el disco, antes de compensar.
        fact.write_text("table Fact\n\tEXTERNO\n", encoding="utf-8")
        return original(sess, entries, hidden)

    monkeypatch.setattr(model_writer, "set_columns_hidden_bulk", fake_live)

    with pytest.raises(PowerBIMCPError) as exc:
        aplicar_both(session, [{"table": "Fact", "column": "Amount"}])

    assert exc.value.code == "bulk_partially_applied"
    assert exc.value.details["compensation"]["clean"] is False
    assert "EXTERNO" in fact.read_text(encoding="utf-8"), \
        "el cambio externo no se pisa ni siquiera al compensar"
    assert "journal" in exc.value.details


def test_both_nunca_devuelve_exito_simple_con_un_solo_destino(proyecto, modelo_fake):
    """El criterio central: si solo un destino quedo aplicado, no hay exito."""
    session, active, project = proyecto
    instalar, _ = modelo_fake
    instalar([FakeTable("Fact", ["Amount"])], fallar_en_save=True)
    session.set_active_model(ActiveModel(host="l", port=1, connection_string="c",
                                         session_fingerprint="fp"))

    with pytest.raises(PowerBIMCPError):
        aplicar_both(session, [{"table": "Fact", "column": "Amount"}])


# ================================================= el error llega como excepcion
def test_un_fallo_total_no_se_convierte_en_lista_de_exitos(proyecto):
    """La regresion que motiva esta fase: antes salia ok:true con fallos dentro."""
    session, active, project = proyecto
    with pytest.raises(PowerBIMCPError):
        hide_columns_service(session, [
            {"table": "Fact", "column": "Amount"},
            {"table": "NoExiste", "column": "X"}], True, "pbip")


def test_la_tool_envuelve_el_servicio_y_reporta_ok_false(proyecto):
    """A traves de guard(), un fallo total es ok:false, no ok:true con errores."""
    from tools._common import guard

    session, active, project = proyecto
    res = guard(lambda: hide_columns_service(
        session, [{"table": "NoExiste", "column": "X"}], True, "pbip"))
    assert res["ok"] is False
    assert res["error"] == "validation_error"
