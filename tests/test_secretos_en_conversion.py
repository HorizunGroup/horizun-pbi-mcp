"""Secretos incrustados: detectarlos, contenerlos y NO repetirlos.

Un `.pbix` es un zip: mientras el token vive dentro, nadie lo ve. Al
convertirlo a `.pbip` el mismo token queda en texto plano dentro de una
carpeta que casi siempre acaba en Git. Estas pruebas fijan las dos mitades del
contrato:

1. **Se detecta y se contiene**: alta confianza bloquea la publicacion y no
   deja ni medio proyecto ni staging huerfano.
2. **El valor no se repite en ningun sitio**: ni en la respuesta, ni en el
   error, ni en los logs, ni en outputs, ni en backups. Un detector de
   secretos que imprime el secreto que encontro es peor que no tenerlo.

TODOS los tokens de este archivo son SINTETICOS: se construyen aqui mismo,
no corresponden a ningun sistema real y no autentican nada.
"""
from __future__ import annotations

import base64
import json
import logging
import zipfile
from pathlib import Path

import pytest

from horizun_pbi_mcp.pbip import pbix_to_pbip
from horizun_pbi_mcp.powerbi.errors import PowerBIMCPError
from horizun_pbi_mcp.services import secret_scan

from tests.test_pbix_convert import _escribir_pbix, _layout, _visual


def _jwt_sintetico() -> str:
    """JWT valido en estructura y falso en todo lo demas."""
    def _b64(data: dict) -> str:
        crudo = json.dumps(data, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(crudo).decode("ascii").rstrip("=")

    cabecera = _b64({"alg": "HS256", "typ": "JWT"})
    cuerpo = _b64({"sub": "prueba-sintetica", "name": "Nadie", "iat": 0})
    firma = "ZmlybWFfc2ludGV0aWNhX3F1ZV9ub192YWxpZGFfbmFkYQ"
    return f"{cabecera}.{cuerpo}.{firma}"


#: Valor largo, con mezcla de tipos de caracter, inventado para esta prueba.
CLAVE_SINTETICA = "Ab3xQ9zR7tL2mN8kP5wV1yH4"


# ============================================================== el detector ===
def test_detecta_un_jwt_con_estructura_valida():
    hallazgos = secret_scan.scan_text(
        f'Headers=[Authorization="Bearer {_jwt_sintetico()}"]', file="t.tmdl")

    jwt = [h for h in hallazgos if h["rule"] == "jwt"]
    assert len(jwt) == 1
    assert jwt[0]["confidence"] == secret_scan.HIGH
    assert jwt[0]["classification"] == secret_scan.SECRET


def test_una_cadena_con_puntos_no_es_un_jwt():
    """Sin el prefijo `eyJ` y sin cabecera decodificable no hay hallazgo de
    alta confianza: bloquear por parecido cuesta mas que no bloquear."""
    hallazgos = secret_scan.scan_text("Table.Sort.Column.Value", file="t.tmdl")
    assert [h for h in hallazgos if h["rule"] == "jwt"] == []


@pytest.mark.parametrize("clave", [
    "password", "Password", "apiKey", "api_key", "clientSecret",
    "client_secret", "accessToken", "access_token", "secret", "token",
])
def test_detecta_las_asignaciones_de_credencial(clave):
    hallazgos = secret_scan.scan_text(f'{clave} = "{CLAVE_SINTETICA}"',
                                      file="t.tmdl")

    asignaciones = [h for h in hallazgos if h["rule"] == "secret_assignment"]
    assert len(asignaciones) == 1
    assert asignaciones[0]["confidence"] == secret_scan.HIGH


@pytest.mark.parametrize("valor", [
    "", "null", "<TU_TOKEN>", "{{token}}", "${TOKEN}", "%TOKEN%",
    "changeme", "********", "your-api-key", "#(placeholder)",
])
def test_un_hueco_por_rellenar_no_es_un_secreto(valor):
    hallazgos = secret_scan.scan_text(f'apiKey = "{valor}"', file="t.tmdl")
    assert [h for h in hallazgos if h["rule"] == "secret_assignment"] == []


def test_un_valor_corto_es_baja_confianza_y_no_bloquea():
    resultado = secret_scan.build_result(
        secret_scan.scan_text('token = "abc12345"', file="t.tmdl"))

    assert resultado["status"] == secret_scan.WARNING
    assert resultado["findings"][0]["confidence"] == secret_scan.LOW


def test_base64_que_esconde_una_credencial():
    interior = f'password="{CLAVE_SINTETICA}"'
    codificado = base64.b64encode(interior.encode("utf-8")).decode("ascii")
    hallazgos = secret_scan.scan_text(f"Blob = {codificado}", file="t.tmdl")

    b64 = [h for h in hallazgos if h["rule"] == "base64_encoded_secret"]
    assert len(b64) == 1
    assert b64[0]["confidence"] == secret_scan.HIGH


def test_no_todo_base64_es_un_secreto():
    """Una imagen o un identificador codificado no dispara nada."""
    inocente = base64.b64encode(b"columna,valor\nRegion,Norte\n" * 3).decode()
    hallazgos = secret_scan.scan_text(f"Data = {inocente}", file="t.tmdl")
    assert hallazgos == []


def test_el_base64_no_se_persigue_sin_limite():
    """Doble codificacion: se decodifica UNA vez y ahi se para.

    Sin este tope, un archivo con base64 anidado convierte el escaneo en una
    bomba de descompresion.
    """
    interior = f'password="{CLAVE_SINTETICA}"'
    una = base64.b64encode(interior.encode()).decode()
    dos = base64.b64encode(una.encode()).decode()

    assert secret_scan.scan_text(f"Blob = {dos}", file="t.tmdl") == []
    assert secret_scan.PROFUNDIDAD_BASE64 == 1


def test_un_correo_en_un_bloque_de_credenciales_es_pii_no_secreto():
    hallazgos = secret_scan.scan_text(
        'Username = "persona@ejemplo.com"', file="t.tmdl")

    correo = [h for h in hallazgos if h["rule"] == "personal_email"]
    assert len(correo) == 1
    assert correo[0]["classification"] == secret_scan.PII
    assert secret_scan.build_result(correo)["status"] == secret_scan.WARNING


def test_un_correo_fuera_de_un_bloque_de_configuracion_no_se_reporta():
    assert secret_scan.scan_text("Region Norte,a@b.com,1200",
                                 file="datos.json") == []


def test_el_hallazgo_nunca_lleva_el_valor():
    hallazgos = secret_scan.scan_text(f'token = "{CLAVE_SINTETICA}"',
                                      file="t.tmdl")
    serializado = json.dumps(hallazgos)

    assert CLAVE_SINTETICA not in serializado
    assert "value" not in hallazgos[0]
    assert len(hallazgos[0]["fingerprint"]) == 12


def test_la_huella_es_estable_pero_no_reversible():
    a = secret_scan.fingerprint(CLAVE_SINTETICA)
    b = secret_scan.fingerprint(CLAVE_SINTETICA)

    assert a == b
    assert a != secret_scan.fingerprint(CLAVE_SINTETICA + "x")
    assert CLAVE_SINTETICA not in a


# ========================================================== la contencion ====
def _pbix_con_token(tmp_path: Path, nombre: str = "ConToken.pbix") -> Path:
    """Un .pbix heredado cuyo visual lleva un JWT sintetico incrustado."""
    visual = _visual("visual00000000000001")
    config = json.loads(visual["config"])
    config["singleVisual"]["objects"] = {
        "general": [{"properties": {
            "authorization": {"expr": {"Literal": {
                "Value": f"'Bearer {_jwt_sintetico()}'"}}}}}]}
    visual["config"] = json.dumps(config)
    seccion = _layout()["sections"][0]
    seccion["visualContainers"] = [visual]
    return _escribir_pbix(tmp_path / nombre, layout=_layout(secciones=[seccion]))


def test_un_secreto_de_alta_confianza_impide_publicar(tmp_path):
    origen = _pbix_con_token(tmp_path)

    with pytest.raises(PowerBIMCPError) as exc:
        pbix_to_pbip.convert(origen, tmp_path / "out", include_model=False)

    escaneo = exc.value.details["security_scan"]
    assert escaneo["status"] == secret_scan.BLOCKED
    assert escaneo["high_confidence_count"] >= 1


def test_al_bloquear_no_queda_proyecto_ni_staging(tmp_path):
    origen = _pbix_con_token(tmp_path)
    destino = tmp_path / "out"

    with pytest.raises(PowerBIMCPError):
        pbix_to_pbip.convert(origen, destino, include_model=False)

    residuo = list(destino.rglob("*")) if destino.exists() else []
    assert [p for p in residuo if p.is_file()] == []
    assert [p for p in tmp_path.glob(".hz_stage_*")] == []


def test_el_error_no_repite_el_secreto(tmp_path, caplog):
    origen = _pbix_con_token(tmp_path)
    token = _jwt_sintetico()

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(PowerBIMCPError) as exc:
            pbix_to_pbip.convert(origen, tmp_path / "out", include_model=False)

    envoltura = json.dumps(exc.value.to_dict(), default=str)
    assert token not in envoltura
    assert token not in exc.value.message
    # Ni el token entero ni su cuerpo suelto, que es igual de reutilizable.
    for trozo in token.split("."):
        assert trozo not in envoltura
        assert trozo not in caplog.text


def test_un_proyecto_limpio_se_publica_y_declara_el_escaneo(tmp_path):
    from tests.test_pbix_convert import _escribir_pbix as escribir

    origen = escribir(tmp_path / "Limpio.pbix", layout=_layout())
    resultado = pbix_to_pbip.convert(origen, tmp_path / "out",
                                     include_model=False)

    escaneo = resultado.to_dict()["security_scan"]
    assert escaneo == resultado.security_scan
    assert escaneo["checked"] is True
    assert escaneo["status"] == secret_scan.CLEAN
    assert escaneo["finding_count"] == 0
    assert escaneo["files_scanned"] > 0
    assert (Path(resultado.project_dir) / "Limpio.pbip").exists()


def test_un_hallazgo_de_baja_confianza_avisa_pero_deja_publicar(tmp_path):
    seccion = _layout()["sections"][0]
    seccion["config"] = json.dumps({"token": "abc12345"})
    origen = _escribir_pbix(tmp_path / "Dudoso.pbix",
                            layout=_layout(secciones=[seccion]))

    resultado = pbix_to_pbip.convert(origen, tmp_path / "out",
                                     include_model=False)

    assert resultado.security_scan["status"] == secret_scan.WARNING
    assert any("baja" in w.casefold() for w in resultado.warnings)
    assert (Path(resultado.project_dir) / "Dudoso.pbip").exists()


def test_el_secreto_tampoco_aparece_en_lo_que_queda_en_disco(tmp_path,
                                                             isolated_settings):
    """Ni en outputs/, ni en backups/, ni en el proyecto a medio publicar."""
    origen = _pbix_con_token(tmp_path)
    token = _jwt_sintetico()

    with pytest.raises(PowerBIMCPError):
        pbix_to_pbip.convert(origen, tmp_path / "out", include_model=False)

    for raiz in (isolated_settings.outputs_dir, isolated_settings.backups_dir,
                 tmp_path / "out"):
        for archivo in Path(raiz).rglob("*"):
            if not archivo.is_file() or archivo.suffix.casefold() == ".pbix":
                continue
            contenido = archivo.read_bytes().decode("utf-8", "ignore")
            assert token not in contenido, f"el token sobrevivio en {archivo}"


def test_el_escaneo_temprano_evita_abrir_desktop(tmp_path, monkeypatch):
    """Bloquear despues de abrir Power BI Desktop seria correcto y caro.

    El detector corre sobre el informe EN MEMORIA, antes del staging y antes
    de lanzar Desktop para exportar el modelo.
    """
    from horizun_pbi_mcp.powerbi import desktop_launcher

    def _prohibido(*args, **kwargs):                     # pragma: no cover
        raise AssertionError("no se debe abrir Desktop para un .pbix bloqueado")

    monkeypatch.setattr(desktop_launcher, "open_pbix", _prohibido)
    origen = _pbix_con_token(tmp_path)

    with pytest.raises(PowerBIMCPError):
        pbix_to_pbip.convert(origen, tmp_path / "out", include_model=True)


# ============================ la M nueva, antes de escribirla ================
def test_una_native_query_con_credencial_no_llega_al_disco(tmp_path):
    """El detector tambien vigila la M que genera el propio servidor."""
    from horizun_pbi_mcp.pbip import table_from_source

    with pytest.raises(PowerBIMCPError) as exc:
        table_from_source.agregar_tabla_desde_fuente(
            None, "sqlserver", "Ventas",
            [{"name": "Importe", "type": "double"}],
            server="srv", database="db",
            native_query=f"SELECT * FROM t WITH (password='{CLAVE_SINTETICA}')",
            dry_run=True)

    assert exc.value.details["security_scan"]["status"] == secret_scan.BLOCKED
    assert CLAVE_SINTETICA not in json.dumps(exc.value.to_dict(), default=str)


def test_una_fuente_limpia_declara_su_escaneo(tmp_path):
    from horizun_pbi_mcp.pbip import table_from_source

    salida = table_from_source.agregar_tabla_desde_fuente(
        None, "odata", "Presupuestos",
        [{"name": "Monto", "type": "double"}],
        url="https://ejemplo.local/odata/Presupuestos", dry_run=True)

    assert salida["security_scan"]["status"] == secret_scan.CLEAN
    assert salida["security_scan"]["checked"] is True
