"""Regresiones de seguridad y recursos para las operaciones live.

No abre Power BI Desktop: todos los objetos TOM/ADOMD son dobles mínimos que
permiten inyectar fallos en las fronteras donde antes se filtraban conexiones o
se dejaban cambios parciales pendientes.
"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
import threading
import time

import pytest

from horizun_pbi_mcp.config import ActiveModel
from horizun_pbi_mcp.powerbi import (adomd_client, dax_runner, desktop_discovery,
                     desktop_launcher, model_reader, model_writer, refresh)
from horizun_pbi_mcp.powerbi.errors import (ConnectionFailedError, MeasureExistsError,
                            RefreshError, TableNotFoundError, ValidationError)


class DotNetError(Exception):
    Message = "fallo .NET inyectado"


class DotNetSecretError(Exception):
    Message = "Password=SuperSecreto;User ID=persona@empresa.test"


def active_model(catalog="esperado"):
    return ActiveModel("localhost", 50000, "Data Source=localhost:50000",
                       catalog=catalog)


class FakeSession:
    def __init__(self, model=None):
        self.model = model or active_model()
        self.calls = 0

    def require_active_model(self):
        self.calls += 1
        return self.model


# ------------------------------------------------------------------ TOM I/O
class FakeDatabases:
    def __init__(self, databases):
        self._databases = list(databases)

    @property
    def Count(self):  # noqa: N802 - API TOM
        return len(self._databases)

    def FindByName(self, name):  # noqa: N802 - API TOM
        return next((d for d in self._databases if d.Name == name), None)

    def __getitem__(self, index):
        return self._databases[index]


class FakeServer:
    def __init__(self, databases=(), connect_error=None):
        self.Databases = FakeDatabases(databases)
        self.connect_error = connect_error
        self.disconnect_calls = 0

    def Connect(self, _connection):  # noqa: N802 - API TOM
        if self.connect_error:
            raise self.connect_error

    def Disconnect(self):  # noqa: N802 - API TOM
        self.disconnect_calls += 1


def test_tom_no_cae_en_otra_base_si_el_catalogo_no_existe(monkeypatch):
    otra = SimpleNamespace(Name="otra", Model=object())
    server = FakeServer([otra])
    monkeypatch.setattr(model_reader, "load_tom",
                        lambda: SimpleNamespace(Server=lambda: server))

    with pytest.raises(ConnectionFailedError) as exc:
        with model_reader.connect(active_model("esperado")):
            pytest.fail("no debe entregar una base distinta")

    assert "esperado" in exc.value.message
    assert server.disconnect_calls == 1


def test_tom_desconecta_incluso_si_connect_falla(monkeypatch):
    server = FakeServer(connect_error=DotNetError())
    monkeypatch.setattr(model_reader, "load_tom",
                        lambda: SimpleNamespace(Server=lambda: server))

    with pytest.raises(ConnectionFailedError):
        with model_reader.connect(active_model()):
            pass

    assert server.disconnect_calls == 1


def test_tom_no_filtra_secretos_del_mensaje_dotnet(monkeypatch):
    server = FakeServer(connect_error=DotNetSecretError())
    monkeypatch.setattr(model_reader, "load_tom",
                        lambda: SimpleNamespace(Server=lambda: server))

    with pytest.raises(ConnectionFailedError) as exc:
        with model_reader.connect(active_model()):
            pass

    assert "SuperSecreto" not in exc.value.message
    assert "persona@empresa.test" not in exc.value.message


def test_tom_sin_catalogo_no_elige_una_de_varias_bases(monkeypatch):
    databases = [
        SimpleNamespace(Name="A", Model=object()),
        SimpleNamespace(Name="B", Model=object()),
    ]
    server = FakeServer(databases)
    monkeypatch.setattr(model_reader, "load_tom",
                        lambda: SimpleNamespace(Server=lambda: server))

    with pytest.raises(ConnectionFailedError):
        with model_reader.connect(active_model(catalog=None)):
            pass

    assert server.disconnect_calls == 1


# ---------------------------------------------------------------- ADOMD I/O
class FakeAdomdConnection:
    def __init__(self, _connection_string, change_error=None):
        self.change_error = change_error
        self.close_calls = 0

    def Open(self):  # noqa: N802 - API ADOMD
        return None

    def ChangeDatabase(self, _catalog):  # noqa: N802 - API ADOMD
        if self.change_error:
            raise self.change_error

    def Close(self):  # noqa: N802 - API ADOMD
        self.close_calls += 1


def test_adomd_cierra_si_change_database_falla(monkeypatch):
    connection = FakeAdomdConnection("x", change_error=DotNetError())
    monkeypatch.setattr(
        adomd_client, "load_adomd",
        lambda: SimpleNamespace(AdomdConnection=lambda _cs: connection))

    client = adomd_client.AdomdClient("Data Source=localhost:50000",
                                      catalog="inexistente")
    with pytest.raises(ConnectionFailedError):
        client.open()

    assert connection.close_calls == 1
    assert client._conn is None  # noqa: SLF001 - verifica liberacion real


def test_discovery_cambia_al_catalogo_antes_de_leer_tmdl(monkeypatch):
    changed = []

    class DiscoveryClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def change_database(self, catalog):
            changed.append(catalog)

        def execute_reader(self, query, max_rows=None):
            if "DBSCHEMA_CATALOGS" in query:
                return ["CATALOG_NAME"], [["catalog-A"]], False, 0.1
            assert changed == ["catalog-A"], "se leyo metadata sin catalogo activo"
            if "TMSCHEMA_MODEL" in query:
                return ["Name"], [["Model"]], False, 0.1
            return ["Name"], [["Ventas"]], False, 0.1

    monkeypatch.setattr(desktop_discovery, "AdomdClient", DiscoveryClient)

    result = desktop_discovery._enrich("localhost", 50000)  # noqa: SLF001

    assert result["status"] == "ok"
    assert changed == ["catalog-A"]


class FakeReader:
    FieldCount = 1

    def __init__(self):
        self._read = False
        self.close_calls = 0

    def GetName(self, _index):  # noqa: N802
        return "x"

    def Read(self):  # noqa: N802
        if self._read:
            return False
        self._read = True
        return True

    def IsDBNull(self, _index):  # noqa: N802
        return False

    def GetValue(self, _index):  # noqa: N802
        return 1

    def Close(self):  # noqa: N802
        self.close_calls += 1


class FakeCommand:
    def __init__(self, reader):
        self.reader = reader
        self.dispose_calls = 0
        self.CommandText = None
        self.CommandTimeout = None

    def ExecuteReader(self):  # noqa: N802
        return self.reader

    def Dispose(self):  # noqa: N802
        self.dispose_calls += 1


def test_adomd_libera_reader_y_command(monkeypatch):
    reader = FakeReader()
    command = FakeCommand(reader)
    client = adomd_client.AdomdClient("Data Source=localhost:50000")
    client._conn = SimpleNamespace(CreateCommand=lambda: command)  # noqa: SLF001
    monkeypatch.setattr(adomd_client, "_convert", lambda value: value)

    assert client.execute_reader("EVALUATE ROW(\"x\", 1)")[1] == [[1]]
    assert reader.close_calls == 1
    assert command.dispose_calls == 1


def test_adomd_corta_por_bytes_durante_el_stream(monkeypatch):
    class TwoRowsReader(FakeReader):
        def __init__(self):
            super().__init__()
            self.index = 0

        def Read(self):  # noqa: N802
            self.index += 1
            return self.index <= 2

        def GetValue(self, _index):  # noqa: N802
            return "😀" * self.index

    reader = TwoRowsReader()
    command = FakeCommand(reader)
    client = adomd_client.AdomdClient("Data Source=localhost:50000")
    client._conn = SimpleNamespace(CreateCommand=lambda: command)  # noqa: SLF001
    monkeypatch.setattr(adomd_client, "_convert", lambda value: value)
    first_only = dax_runner._tamano_aproximado([["😀"]])  # noqa: SLF001

    _columns, rows, truncated, _elapsed = client.execute_reader(
        "EVALUATE X", max_rows=100, max_bytes=first_only)

    assert rows == [["😀"]]
    assert truncated is True
    assert client.last_truncation_reason == "bytes"
    assert reader.close_calls == 1
    assert command.dispose_calls == 1


def test_run_dax_informa_corte_streaming_por_bytes(monkeypatch):
    seen = []

    class StreamingClient:
        last_truncation_reason = None

        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def execute_reader(self, query, max_rows=None, max_bytes=None):
            seen.append((max_rows, max_bytes))
            self.last_truncation_reason = "bytes"
            return ["x"], [["a"]], True, 0.1

    monkeypatch.setattr(dax_runner, "AdomdClient", StreamingClient)

    result = dax_runner.run_dax(
        FakeSession(), 'EVALUATE ROW("x", "a")', max_rows=10, max_bytes=100)

    assert seen == [(10, 100)]
    assert result["stats"]["truncated_by_bytes"] is True
    assert result["stats"]["truncated_by_rows"] is False


def test_connect_timeout_no_se_confunde_con_otro_timeout():
    result = adomd_client._with_connect_timeout(  # noqa: SLF001
        "Data Source=localhost:50000;Command Timeout=90", 7)
    assert "Command Timeout=90" in result
    assert "Connect Timeout=7" in result


# ------------------------------------------------------------ DAX resultado
def test_tamano_dax_cuenta_bytes_utf8_y_sintaxis_json():
    rows = [["😀"]]
    expected = len('[['.encode()) + len('"😀"'.encode("utf-8")) + len(']]'.encode())
    assert dax_runner._tamano_aproximado(rows) == expected  # noqa: SLF001
    assert dax_runner._recortar_a_bytes(rows, expected - 1) == []  # noqa: SLF001
    assert dax_runner._recortar_a_bytes(rows, expected) == rows  # noqa: SLF001


def test_exportaciones_dax_del_mismo_segundo_no_se_sobrescriben(
        isolated_settings, monkeypatch):
    from horizun_pbi_mcp.utils import file_utils

    monkeypatch.setattr(file_utils, "timestamp", lambda: "20260101_000000")
    stats = {"truncated_by_rows": False, "truncated_by_bytes": False}

    first = dax_runner._exportar("EVALUATE ROW(1)", ["x"], [[1]], stats)  # noqa: SLF001
    second = dax_runner._exportar("EVALUATE ROW(2)", ["x"], [[2]], stats)  # noqa: SLF001

    assert first != second


@pytest.mark.parametrize("measures", [
    [{"dax": "1"}],
    [{"name": "M"}],
    [{"name": "", "dax": "1"}],
    [{"name": "M", "dax": ""}],
    [{"name": "M", "dax": "1", "table": ""}],
    [{"name": "M", "dax": "1"}, {"name": "m", "dax": "2"}],
    ["no es un objeto"],
])
def test_validate_measures_rechaza_entrada_antes_de_conectar(measures,
                                                               monkeypatch):
    session = FakeSession()

    class MustNotOpen:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("no debe abrir ADOMD")

    monkeypatch.setattr(dax_runner, "AdomdClient", MustNotOpen)

    with pytest.raises(ValidationError):
        dax_runner.validate_measures(session, measures)

    assert session.calls == 0


def test_validate_measures_escapa_corchete_del_nombre(monkeypatch):
    queries = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def execute_reader(self, query, max_rows=None):
            queries.append(query)
            return ["v"], [[1]], False, 0.1

    monkeypatch.setattr(dax_runner, "AdomdClient", FakeClient)

    result = dax_runner.validate_measures(FakeSession(), [{
        "name": "Margen ] especial", "dax": "1", "table": "Ventas",
    }])

    assert result["valid"] == 1
    assert all("[Margen ]] especial]" in q for q in queries)


# --------------------------------------------------------- descubrimiento I/O
def test_archivo_de_puerto_utf8_no_se_interpreta_primero_como_utf16(
        tmp_path, monkeypatch):
    base = (tmp_path / "Microsoft" / "Power BI Desktop" /
            "AnalysisServicesWorkspaces" / "Workspace-A" / "Data")
    base.mkdir(parents=True)
    # Seis bytes: UTF-16 lo puede decodificar sin UnicodeError, pero produce
    # basura. Antes se dejaba de probar UTF-8 en cuanto esa decodificacion
    # accidental tenia exito.
    (base / "msmdsrv.port.txt").write_bytes(b"50000\n")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    found = desktop_discovery._workspace_port_files()  # noqa: SLF001

    assert [item["port"] for item in found] == [50000]


def test_descubrimiento_no_asigna_workspace_arbitrario_si_hay_dos_para_el_puerto(
        monkeypatch):
    monkeypatch.setattr(desktop_discovery, "_ports_from_processes", lambda: [{
        "host": "localhost", "port": 50000, "pid": 321,
        "create_time": 123.0, "source": "process",
    }])
    monkeypatch.setattr(desktop_discovery, "_workspace_port_files", lambda: [
        {"host": "localhost", "port": 50000, "pid": None,
         "create_time": None, "source": "port_file", "workspace": "A"},
        {"host": "localhost", "port": 50000, "pid": None,
         "create_time": None, "source": "port_file", "workspace": "B"},
    ])
    monkeypatch.setattr(desktop_discovery, "_port_is_listening",
                        lambda _host, _port: True)
    monkeypatch.setattr(desktop_discovery, "_enrich", lambda host, port: {
        "host": host, "port": port, "catalog": "cat", "status": "ok",
        "warnings": [],
    })

    [found] = desktop_discovery.discover_instances()

    assert "workspace" not in found
    assert any("workspace" in warning.casefold()
               for warning in found["warnings"])


def test_verify_model_rechaza_connection_string_a_otro_puerto(monkeypatch):
    instance = {
        "host": "localhost", "port": 50000, "pid": 111,
        "create_time": 100.0, "catalog": "A", "status": "ok",
        "workspace": None, "warnings": [],
    }
    instance["session_fingerprint"] = desktop_discovery.session_fingerprint(
        instance)
    model = ActiveModel(
        "localhost", 50000, "Data Source=localhost:59999", catalog="A",
        pid=111, process_started=100.0,
        session_fingerprint=instance["session_fingerprint"])
    monkeypatch.setattr(desktop_discovery, "discover_instances",
                        lambda: [instance])

    result = desktop_discovery.verify_model(model)

    assert result["status"] == "mismatch"
    assert "connection string" in result["reason"].casefold()


# ------------------------------------------------------------ Desktop launch
def test_launcher_prefiere_el_proceso_que_tiene_el_archivo_a_otro_puerto_nuevo(
        tmp_path, monkeypatch):
    pbix = tmp_path / "objetivo.pbix"
    pbix.write_bytes(b"fake")
    wrong = {"port": 51001, "pid": 101, "table_count": 2}
    right = {"port": 51002, "pid": 202, "table_count": 3}
    probes = iter([None, 900])

    monkeypatch.setattr(desktop_launcher, "proceso_con_archivo_abierto",
                        lambda _path: next(probes, 900))
    monkeypatch.setattr(desktop_launcher, "_instancia_de_proceso",
                        lambda pid: right if pid == 900 else None)
    monkeypatch.setattr(desktop_launcher, "_instancias_utiles",
                        lambda: [wrong])
    monkeypatch.setattr(desktop_launcher, "_estabilizar", lambda inst: inst)
    monkeypatch.setattr(desktop_launcher.time, "sleep", lambda _seconds: None)
    ticks = iter([0.0, 0.1, 0.2, 0.3])
    monkeypatch.setattr(desktop_launcher.time, "monotonic",
                        lambda: next(ticks, 0.4))

    found = desktop_launcher._esperar_instancia_nueva(  # noqa: SLF001
        set(), 1, pbix.name, pbix_path=pbix, launched_pid=700)

    assert found["port"] == right["port"]


def test_detecta_archivo_abierto_con_prefijo_windows_extended_path(
        tmp_path, monkeypatch):
    pbix = (tmp_path / "informe.pbix").resolve()
    pbix.write_bytes(b"fake")
    process = SimpleNamespace(
        pid=777,
        open_files=lambda: [SimpleNamespace(path="\\\\?\\" + str(pbix))],
    )
    monkeypatch.setattr(desktop_launcher, "_procesos_desktop",
                        lambda: [process])

    assert desktop_launcher.proceso_con_archivo_abierto(pbix) == 777


def test_launcher_rechaza_timeout_invalido_antes_de_descubrir(
        tmp_path, monkeypatch):
    pbix = tmp_path / "x.pbix"
    pbix.write_bytes(b"fake")
    monkeypatch.setattr(
        desktop_launcher.desktop_discovery, "discover_instances",
        lambda: pytest.fail("no debe descubrir con timeout invalido"))

    with pytest.raises(ValidationError):
        desktop_launcher.open_pbix(pbix, timeout=0)


def test_estabilizacion_no_devuelve_una_instancia_que_desaparecio(monkeypatch):
    monkeypatch.setattr(desktop_launcher.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(desktop_launcher, "_instancias_utiles", lambda: [])

    with pytest.raises(desktop_launcher.DesktopTimeoutError):
        desktop_launcher._estabilizar({"port": 50000, "table_count": 3})  # noqa: SLF001


def test_estabilizacion_no_acepta_otro_proceso_en_el_mismo_puerto(monkeypatch):
    original = {"port": 50000, "pid": 111, "create_time": 100.0,
                "table_count": 3}
    recycled = {"port": 50000, "pid": 222, "create_time": 200.0,
                "table_count": 3}
    monkeypatch.setattr(desktop_launcher.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(desktop_launcher, "_instancias_utiles",
                        lambda: [recycled])

    with pytest.raises(desktop_launcher.DesktopTimeoutError) as exc:
        desktop_launcher._estabilizar(original)  # noqa: SLF001

    assert exc.value.details["phase"] == "stabilization_identity"


def test_close_no_mata_un_pid_reciclado(monkeypatch):
    terminated = []

    class ReusedProcess:
        pid = 800

        def create_time(self):
            return 999.0

        def name(self):
            return "PBIDesktop.exe"

        def children(self, recursive=True):
            return []

        def terminate(self):
            terminated.append(self.pid)

    import psutil

    monkeypatch.setattr(psutil, "Process", lambda _pid: ReusedProcess())
    opened = desktop_launcher.OpenedPbix(
        "x.pbix", {"port": 1}, 800, True, 0.0, desktop_started=100.0)

    result = desktop_launcher.close(opened)

    assert result["closed"] is False
    assert result["reason"] == "desktop_pid_reused"
    assert terminated == []


# ------------------------------------------------------------------- refresh
class FakeRefreshTable:
    def __init__(self, name):
        self.Name = name
        self.requested = []

    def RequestRefresh(self, refresh_type):  # noqa: N802
        self.requested.append(refresh_type)


class FakeRefreshModel:
    def __init__(self, tables=()):
        self.Tables = list(tables)
        self.model_requests = []
        self.save_calls = 0
        self.save_error = None

    def RequestRefresh(self, refresh_type):  # noqa: N802
        self.model_requests.append(refresh_type)

    def SaveChanges(self):  # noqa: N802
        self.save_calls += 1
        if self.save_error:
            raise self.save_error


def install_refresh_fakes(monkeypatch, model):
    monkeypatch.setattr(
        refresh, "load_tom",
        lambda: SimpleNamespace(RefreshType=SimpleNamespace(
            Full="Full", Calculate="Calculate", ClearValues="ClearValues",
            Automatic="Automatic", DataOnly="DataOnly")))

    @contextmanager
    def fake_connect(_active):
        yield object(), object(), model

    monkeypatch.setattr(refresh, "connect", fake_connect)


def test_refresh_lista_vacia_no_significa_todo_el_modelo(monkeypatch):
    session = FakeSession()
    model = FakeRefreshModel()
    install_refresh_fakes(monkeypatch, model)

    with pytest.raises(ValidationError):
        refresh.refresh_model(session, tables=[])

    assert session.calls == 0
    assert model.model_requests == []
    assert model.save_calls == 0


def test_refresh_valida_todo_antes_de_solicitar_el_primero(monkeypatch):
    ventas = FakeRefreshTable("Ventas")
    model = FakeRefreshModel([ventas])
    install_refresh_fakes(monkeypatch, model)

    with pytest.raises(TableNotFoundError):
        refresh.refresh_model(FakeSession(), tables=["Ventas", "NoExiste"])

    assert ventas.requested == []
    assert model.save_calls == 0


def test_refresh_resuelve_casefold_y_no_procesa_duplicados(monkeypatch):
    ventas = FakeRefreshTable("Ventas")
    model = FakeRefreshModel([ventas])
    install_refresh_fakes(monkeypatch, model)

    result = refresh.refresh_model(
        FakeSession(), tables=["ventas", "VENTAS"], refresh_type="data_only")

    assert ventas.requested == ["DataOnly"]
    assert model.save_calls == 1
    assert result["tables"] == ["Ventas"]


def test_refresh_envuelve_fallo_dotnet_de_savechanges(monkeypatch):
    model = FakeRefreshModel()
    model.save_error = DotNetError()
    install_refresh_fakes(monkeypatch, model)

    with pytest.raises(RefreshError) as exc:
        refresh.refresh_model(FakeSession())

    assert exc.value.__cause__ is model.save_error
    assert model.save_calls == 1


def test_refresh_no_filtra_secretos_del_error_dotnet(monkeypatch):
    model = FakeRefreshModel()
    model.save_error = DotNetSecretError()
    install_refresh_fakes(monkeypatch, model)

    with pytest.raises(RefreshError) as exc:
        refresh.refresh_model(FakeSession())

    assert "SuperSecreto" not in exc.value.message
    assert "persona@empresa.test" not in exc.value.message


# ------------------------------------------------------------- model writer
class FakeMeasure:
    def __init__(self, name, expression="1"):
        self.Name = name
        self.Expression = expression
        self.FormatString = ""
        self.DisplayFolder = ""
        self.Description = ""


class FakeMeasures(list):
    def Find(self, name):  # noqa: N802
        return next((m for m in self if m.Name.casefold() == name.casefold()), None)

    def Add(self, measure):  # noqa: N802
        self.append(measure)

    def Remove(self, measure):  # noqa: N802
        self.remove(measure)


class FakeColumn:
    def __init__(self, name, hidden=False):
        self.Name = name
        self.IsHidden = hidden


class FakeWriterTable:
    def __init__(self, name, measures=(), columns=()):
        self.Name = name
        self.Measures = FakeMeasures(measures)
        self.Columns = list(columns)


class FakeWriterModel:
    def __init__(self, tables, save_error=None, relationships=()):
        self.Tables = list(tables)
        self.Relationships = list(relationships)
        self.save_error = save_error
        self.save_calls = 0

    def SaveChanges(self):  # noqa: N802
        self.save_calls += 1
        if self.save_error:
            raise self.save_error


def install_writer_model(monkeypatch, model):
    @contextmanager
    def fake_connect(_active):
        yield object(), object(), model

    monkeypatch.setattr(model_writer, "connect", fake_connect)


def test_create_overwrite_no_toca_homonima_de_otra_tabla(monkeypatch):
    existing = FakeMeasure("Total", "1")
    model = FakeWriterModel([
        FakeWriterTable("Ventas", [existing]),
        FakeWriterTable("Presupuesto"),
    ])
    install_writer_model(monkeypatch, model)

    with pytest.raises(MeasureExistsError) as exc:
        model_writer.create_measure(
            FakeSession(), "Presupuesto", "Total", "2", overwrite=True)

    assert existing.Expression == "1"
    assert model.save_calls == 0
    assert exc.value.details["existing_table"] == "Ventas"


def test_escritura_live_envuelve_fallo_dotnet(monkeypatch):
    column = FakeColumn("Importe")
    model = FakeWriterModel(
        [FakeWriterTable("Ventas", columns=[column])], save_error=DotNetError())
    install_writer_model(monkeypatch, model)

    with pytest.raises(model_writer.LiveWriteError) as exc:
        model_writer.set_column_hidden(
            FakeSession(), "ventas", "importe", hidden=True)

    assert exc.value.__cause__ is model.save_error
    assert model.save_calls == 1


@pytest.mark.parametrize("entries", [
    None,
    [{"table": "Ventas"}],
    [{"table": "", "column": "Importe"}],
    ["Ventas[Importe]"],
])
def test_lote_columnas_invalido_falla_antes_de_sesion(entries):
    session = FakeSession()

    with pytest.raises(ValidationError):
        model_writer.set_columns_hidden_bulk(session, entries)

    assert session.calls == 0


def test_relacion_live_resuelve_tablas_sin_depender_de_mayusculas(monkeypatch):
    relation = SimpleNamespace(
        FromTable=SimpleNamespace(Name="Ventas"),
        ToTable=SimpleNamespace(Name="Clientes"),
        CrossFilteringBehavior="OneDirection",
    )
    model = FakeWriterModel([], relationships=[relation])
    install_writer_model(monkeypatch, model)
    monkeypatch.setattr(model_writer, "load_tom", lambda: SimpleNamespace(
        CrossFilteringBehavior=SimpleNamespace(
            BothDirections="BothDirections", OneDirection="OneDirection")))

    result = model_writer.set_relationship_crossfilter(
        FakeSession(), "ventas", "CLIENTES", "both")

    assert result["matched"] == 1
    assert relation.CrossFilteringBehavior == "BothDirections"
    assert model.save_calls == 1


def test_mutacion_live_mantiene_fija_la_seleccion_hasta_savechanges(
        session, monkeypatch):
    original = ActiveModel(
        "localhost", 50000, "Data Source=localhost:50000", catalog="A",
        session_fingerprint="fingerprint-A")
    replacement = ActiveModel(
        "localhost", 50001, "Data Source=localhost:50001", catalog="B",
        session_fingerprint="fingerprint-B")
    session.set_active_model(original)

    entered = threading.Event()
    release = threading.Event()
    switched = threading.Event()
    column = FakeColumn("Importe")
    model = FakeWriterModel([FakeWriterTable("Ventas", columns=[column])])

    @contextmanager
    def blocking_connect(active):
        assert active is original
        entered.set()
        assert release.wait(2)
        yield object(), object(), model

    monkeypatch.setattr(model_writer, "connect", blocking_connect)

    mutation = threading.Thread(target=lambda: model_writer.set_column_hidden(
        session, "Ventas", "Importe", True))

    def switch():
        session.set_active_model(replacement)
        switched.set()

    mutation.start()
    assert entered.wait(1)
    selector = threading.Thread(target=switch)
    selector.start()
    time.sleep(0.05)
    assert not switched.is_set(), "la seleccion cambio durante la mutacion"
    release.set()
    mutation.join(2)
    selector.join(2)

    assert switched.is_set()
    assert model.save_calls == 1
    assert session.active_model is replacement
