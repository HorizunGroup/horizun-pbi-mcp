"""Conector SharePoint: Graph simulado, sin red ni datos reales."""
from __future__ import annotations

import json
import io
from pathlib import Path

import pytest

from horizun_pbi_mcp.powerbi.errors import ValidationError
from horizun_pbi_mcp.services import sharepoint


SITE = {"id": "tenant,site,web", "displayName": "Sitio sintetico",
        "webUrl": "https://example.sharepoint.com/sites/demo"}
DRIVE = {"id": "drive-1", "name": "Documents",
         "webUrl": "https://example.sharepoint.com/sites/demo/Documents"}


def test_credenciales_faltantes_fallan_sin_incluir_secreto(monkeypatch):
    for suffix in ("TENANT_ID", "CLIENT_ID", "CLIENT_SECRET"):
        monkeypatch.delenv(f"HORIZUN_PBI_MCP_SHAREPOINT_{suffix}", raising=False)
        monkeypatch.delenv(f"PBI_MCP_SHAREPOINT_{suffix}", raising=False)
    with pytest.raises(sharepoint.SharePointConfigError) as exc:
        sharepoint._credentials()
    assert "CLIENT_SECRET" in str(exc.value.details)
    assert "access_token" not in str(exc.value.details)


@pytest.mark.parametrize("url", [
    "http://example.sharepoint.com/sites/demo",
    "https://evil.example/sites/demo",
    "https://user:secret@example.sharepoint.com/sites/demo",
    "https://example.sharepoint.com/sites/demo?token=secret",
])
def test_site_url_rechaza_destinos_inseguros(url):
    with pytest.raises(ValidationError):
        sharepoint._site_request(url)


def test_site_raiz_y_subsitio_producen_rutas_graph_validas():
    root, _, _ = sharepoint._site_request("https://example.sharepoint.com")
    sub, _, _ = sharepoint._site_request(
        "https://example.sharepoint.com/sites/Area Tecnica")
    assert root.startswith("/sites/example.sharepoint.com?")
    assert ":/sites/Area%20Tecnica?" in sub


def test_site_url_ya_codificada_no_duplica_el_porcentaje():
    sub, _, _ = sharepoint._site_request(
        "https://example.sharepoint.com/sites/Area%20Tecnica")
    assert ":/sites/Area%20Tecnica?" in sub
    assert "%2520" not in sub


def test_nextlink_solo_puede_seguir_en_graph():
    with pytest.raises(sharepoint.SharePointRequestError):
        sharepoint._graph_url("https://evil.example/robar")
    with pytest.raises(sharepoint.SharePointRequestError):
        sharepoint._graph_url("https://graph.microsoft.com:444/v1.0/sites")


def test_graph_json_envia_bearer_sin_devolverlo(monkeypatch):
    captured = []

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    def open_fake(request, timeout):
        captured.append((request.full_url, request.get_header("Authorization"), timeout))
        return Response(b'{"value":[{"id":"x"}]}')

    monkeypatch.setattr(sharepoint, "urlopen", open_fake)
    result = sharepoint._graph_json("/sites/root", "secreto-token")
    assert captured[0][0] == "https://graph.microsoft.com/v1.0/sites/root"
    assert captured[0][1] == "Bearer secreto-token"
    assert "secreto-token" not in json.dumps(result)


def test_paginacion_sigue_nextlink_oficial(monkeypatch):
    calls = []

    def graph(route, _token):
        calls.append(route)
        if len(calls) == 1:
            return {"value": [{"id": "a"}],
                    "@odata.nextLink":
                    "https://graph.microsoft.com/v1.0/drives/x/root/children?$skiptoken=1"}
        return {"value": [{"id": "b"}]}

    monkeypatch.setattr(sharepoint, "_graph_json", graph)
    assert [item["id"] for item in sharepoint._paged_children("/inicio", "t")] == [
        "a", "b"]
    assert len(calls) == 2


def test_descarga_streaming_verifica_hash_y_tamano(monkeypatch, tmp_path):
    payload = b"contenido-sintetico"

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    monkeypatch.setattr(
        sharepoint, "urlopen", lambda *_a, **_k: Response(payload))
    target = tmp_path / "descarga.bin"
    result = sharepoint._download_one(
        "https://download.invalid/item", target,
        expected_size=len(payload), max_bytes=len(payload))
    assert target.read_bytes() == payload
    assert result["sha256"] == __import__("hashlib").sha256(payload).hexdigest()


def test_descarga_verifica_en_streaming_sin_read_bytes(monkeypatch, tmp_path):
    payload = b"contenido-sintetico"

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    monkeypatch.setattr(sharepoint, "urlopen", lambda *_a, **_k: Response(payload))
    monkeypatch.setattr(Path, "read_bytes", lambda _self: (_ for _ in ()).throw(
        AssertionError("no debe cargar el archivo completo en RAM")))
    target = tmp_path / "stream.bin"

    result = sharepoint._download_one(
        "https://download.invalid/item", target,
        expected_size=len(payload), max_bytes=len(payload))

    assert result["bytes"] == len(payload)
    with target.open("rb") as downloaded:
        assert downloaded.read() == payload


def test_descarga_streaming_corta_antes_de_exceder_limite(monkeypatch, tmp_path):
    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    monkeypatch.setattr(
        sharepoint, "urlopen", lambda *_a, **_k: Response(b"demasiado"))
    target = tmp_path / "no-publicar.bin"
    with pytest.raises(sharepoint.SharePointLimitError):
        sharepoint._download_one(
            "https://download.invalid/item", target,
            expected_size=9, max_bytes=3)
    assert not target.exists()
    assert not list(tmp_path.glob("*.part"))


def test_listado_recursivo_conserva_rutas_y_limite(monkeypatch):
    monkeypatch.setattr(sharepoint, "_token", lambda: "token-no-sale")
    monkeypatch.setattr(sharepoint, "_site_and_drive",
                        lambda *_: (SITE, DRIVE))

    def pages(route, _token):
        if "items/folder-1" in route:
            yield {"id": "file-2", "name": "detalle.csv", "size": 7,
                   "file": {"mimeType": "text/csv"}}
        else:
            yield {"id": "folder-1", "name": "Datos", "size": 0,
                   "folder": {"childCount": 1}}
            yield {"id": "file-1", "name": "resumen.xlsx", "size": 10,
                   "file": {"mimeType":
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}}

    monkeypatch.setattr(sharepoint, "_paged_children", pages)
    result = sharepoint.list_folder(
        SITE["webUrl"], recursive=True, max_items=10)
    assert [item["relative_path"] for item in result["items"]] == [
        "Datos", "resumen.xlsx", "Datos/detalle.csv"]
    assert result["truncated"] is False
    assert "token-no-sale" not in json.dumps(result)


@pytest.mark.parametrize("max_items,expected_count,truncated", [
    (2, 2, False),
    (1, 1, True),
])
def test_listado_solo_marca_truncado_si_hay_un_elemento_adicional(
        monkeypatch, max_items, expected_count, truncated):
    monkeypatch.setattr(sharepoint, "_token", lambda: "token")
    monkeypatch.setattr(sharepoint, "_site_and_drive", lambda *_: (SITE, DRIVE))
    monkeypatch.setattr(sharepoint, "_paged_children", lambda *_: iter([
        {"id": "a", "name": "a.csv", "size": 1, "file": {}},
        {"id": "b", "name": "b.csv", "size": 1, "file": {}},
    ]))

    result = sharepoint.list_folder(SITE["webUrl"], max_items=max_items)

    assert result["count"] == expected_count
    assert result["truncated"] is truncated


def _listing(files):
    return {"site": SITE, "library": DRIVE, "folder_path": "", "recursive": True,
            "count": len(files), "items": files, "truncated": False,
            "max_items": 100}


def test_descarga_publica_el_lote_completo(monkeypatch, isolated_settings):
    files = [
        {"id": "a", "name": "uno.xlsx", "relative_path": "Datos/uno.xlsx",
         "kind": "file", "size": 3},
        {"id": "b", "name": "dos.csv", "relative_path": "Datos/dos.csv",
         "kind": "file", "size": 4},
    ]
    monkeypatch.setattr(sharepoint, "_token", lambda: "token")
    monkeypatch.setattr(sharepoint, "_site_and_drive", lambda *_: (SITE, DRIVE))
    monkeypatch.setattr(sharepoint, "list_folder", lambda *a, **k: _listing(files))
    monkeypatch.setattr(sharepoint, "_download_url",
                        lambda _t, _d, item: f"https://download.invalid/{item}")

    contents = {"a": b"abc", "b": b"1234"}

    def download(url, target, *, expected_size, max_bytes):
        item = url.rsplit("/", 1)[-1]
        payload = contents[item]
        assert len(payload) == expected_size and len(payload) <= max_bytes
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        return {"path": str(target), "bytes": len(payload), "sha256": "x" * 64}

    monkeypatch.setattr(sharepoint, "_download_one", download)
    result = sharepoint.download_folder(
        SITE["webUrl"], extensions=["xlsx", ".csv"])

    root = Path(result["output_path"])
    assert (root / "Datos" / "uno.xlsx").read_bytes() == b"abc"
    assert (root / "Datos" / "dos.csv").read_bytes() == b"1234"
    assert result["verified"] is True and result["file_count"] == 2
    assert not list((isolated_settings.outputs_dir / "sharepoint").glob(".stage_*"))


def test_fallo_intermedio_no_publica_carpeta_parcial(
        monkeypatch, isolated_settings):
    files = [
        {"id": "a", "name": "uno.csv", "relative_path": "uno.csv",
         "kind": "file", "size": 1},
        {"id": "b", "name": "dos.csv", "relative_path": "dos.csv",
         "kind": "file", "size": 1},
    ]
    monkeypatch.setattr(sharepoint, "_token", lambda: "token")
    monkeypatch.setattr(sharepoint, "_site_and_drive", lambda *_: (SITE, DRIVE))
    monkeypatch.setattr(sharepoint, "list_folder", lambda *a, **k: _listing(files))
    monkeypatch.setattr(sharepoint, "_download_url", lambda *_: "https://download.invalid/x")
    calls = []

    def download(_url, target, **_kwargs):
        calls.append(target)
        if len(calls) == 2:
            raise OSError("corte inyectado")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x")
        return {"path": str(target), "bytes": 1, "sha256": "x" * 64}

    monkeypatch.setattr(sharepoint, "_download_one", download)
    with pytest.raises(OSError):
        sharepoint.download_folder(SITE["webUrl"], extensions=["csv"])

    root = isolated_settings.outputs_dir / "sharepoint"
    assert not list(root.glob(".stage_*"))
    assert not list(root.glob("sharepoint_*"))


def test_ruta_remota_no_puede_salir_del_staging(monkeypatch):
    bad = [{"id": "a", "name": "evil.csv", "relative_path": "../evil.csv",
            "kind": "file", "size": 1}]
    monkeypatch.setattr(sharepoint, "_token", lambda: "token")
    monkeypatch.setattr(sharepoint, "_site_and_drive", lambda *_: (SITE, DRIVE))
    monkeypatch.setattr(sharepoint, "list_folder", lambda *a, **k: _listing(bad))
    with pytest.raises(Exception):
        sharepoint.download_folder(SITE["webUrl"])


def test_limites_se_validan_antes_de_descargar(monkeypatch):
    files = [{"id": str(i), "name": f"{i}.csv", "relative_path": f"{i}.csv",
              "kind": "file", "size": 1} for i in range(3)]
    monkeypatch.setattr(sharepoint, "_token", lambda: "token")
    monkeypatch.setattr(sharepoint, "_site_and_drive", lambda *_: (SITE, DRIVE))
    monkeypatch.setattr(sharepoint, "list_folder", lambda *a, **k: _listing(files))
    called = []
    monkeypatch.setattr(sharepoint, "_download_one", lambda *a, **k: called.append(1))
    with pytest.raises(sharepoint.SharePointLimitError):
        sharepoint.download_folder(SITE["webUrl"], max_files=2)
    assert called == []
