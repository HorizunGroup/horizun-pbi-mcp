"""El barrido del arbol antes de publicar: lo que se mira y lo que se omite.

`scan_text` -las reglas- ya tiene sus pruebas. Lo que faltaba defender es el
recorrido: que un archivo que NO se pudo leer se declare omitido en vez de
pasar por limpio, que un UTF-16 se decodifique en lugar de descartarse, y que
un archivo enorme se trunque diciendolo.

Importa mas de lo que parece: en un detector que bloquea la publicacion, un
archivo saltado en silencio es exactamente un secreto que se publica.
"""
from __future__ import annotations

from pathlib import Path

from horizun_pbi_mcp.services import secret_scan

#: Un JWT de mentira, con la forma que la regla reconoce.
JWT = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
       "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4ifQ."
       "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c")


def _arbol(tmp_path: Path) -> Path:
    raiz = tmp_path / "proyecto"
    (raiz / "Demo.SemanticModel").mkdir(parents=True)
    (raiz / "Demo.pbip").write_text("{}", encoding="utf-8")
    return raiz


def test_un_secreto_en_el_arbol_se_encuentra(tmp_path):
    raiz = _arbol(tmp_path)
    (raiz / "Demo.SemanticModel" / "expressions.tmdl").write_text(
        f'Headers = [Authorization = "Bearer {JWT}"]', encoding="utf-8")

    resultado = secret_scan.scan_tree(raiz)

    assert resultado["status"] == secret_scan.BLOCKED
    assert any(h["rule"] == "jwt" for h in resultado["findings"])
    # El valor NO viaja: solo la huella.
    assert JWT not in str(resultado)


def test_un_archivo_en_utf16_tambien_se_lee(tmp_path):
    """Descartarlo por no decodificar seria saltarse justo donde mirar."""
    raiz = _arbol(tmp_path)
    (raiz / "config.json").write_bytes(
        f'{{"token": "{JWT}"}}'.encode("utf-16"))

    resultado = secret_scan.scan_tree(raiz)

    assert resultado["files_scanned"] >= 1
    assert any(h["rule"] == "jwt" for h in resultado["findings"])
    assert not resultado.get("skipped_files"), (
        "se omitio un archivo que si se podia leer")


def test_lo_que_no_se_pudo_leer_se_declara_omitido(tmp_path, monkeypatch):
    """Un archivo saltado en silencio es un secreto publicado en silencio."""
    raiz = _arbol(tmp_path)
    problematico = raiz / "raro.tmdl"
    problematico.write_text("hola", encoding="utf-8")

    original = Path.read_bytes

    def _falla(self, *a, **k):
        if self.name == "raro.tmdl":
            raise OSError("bloqueado por otro proceso")
        return original(self, *a, **k)

    monkeypatch.setattr(Path, "read_bytes", _falla)
    resultado = secret_scan.scan_tree(raiz)

    omitidos = {o["file"] for o in resultado["skipped_files"]}
    assert "raro.tmdl" in omitidos
    assert resultado["skipped_files"][0]["reason"]


def test_un_archivo_binario_no_cuenta_como_revisado(tmp_path):
    raiz = _arbol(tmp_path)
    (raiz / "imagen.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)

    resultado = secret_scan.scan_tree(raiz)

    assert all("imagen.png" not in str(o) for o in resultado["findings"])


def test_un_archivo_enorme_se_trunca_y_se_dice(tmp_path, monkeypatch):
    """Truncar sin decirlo es afirmar que se miro entero."""
    monkeypatch.setattr(secret_scan, "MAX_BYTES_POR_ARCHIVO", 64)
    raiz = _arbol(tmp_path)
    (raiz / "grande.tmdl").write_text("x" * 500, encoding="utf-8")

    resultado = secret_scan.scan_tree(raiz)

    assert "grande.tmdl" in resultado["truncated_files"]
    assert resultado["warnings"], "se trunco sin avisar"


def test_los_archivos_extra_se_revisan_ademas_del_arbol(tmp_path):
    """El `.pbip` de la raiz queda fuera del arbol y tambien puede llevarlo."""
    raiz = _arbol(tmp_path)
    aparte = tmp_path / "fuera.json"
    aparte.write_text(f'{{"token": "{JWT}"}}', encoding="utf-8")

    resultado = secret_scan.scan_tree(raiz, extra=[aparte])

    assert resultado["status"] == secret_scan.BLOCKED
    assert any(str(aparte) in h["file"] for h in resultado["findings"])


def test_un_extra_que_no_existe_no_rompe_el_barrido(tmp_path):
    raiz = _arbol(tmp_path)
    resultado = secret_scan.scan_tree(raiz, extra=[tmp_path / "no_existe.json"])
    assert resultado["status"] == secret_scan.CLEAN


def test_un_arbol_limpio_no_bloquea(tmp_path):
    raiz = _arbol(tmp_path)
    (raiz / "Demo.SemanticModel" / "model.tmdl").write_text(
        "table Ventas\n\tcolumn Fecha\n", encoding="utf-8")

    resultado = secret_scan.scan_tree(raiz)

    assert resultado["status"] == secret_scan.CLEAN
    assert resultado["findings"] == []
    assert resultado["files_scanned"] >= 2
