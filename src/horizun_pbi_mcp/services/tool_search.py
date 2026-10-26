"""Pure bilingual search over the full registered-source catalog, not tool access."""
from __future__ import annotations

import re
import unicodedata

GROUPS = (
    "measure measures medida medidas",
    "table tables tabla tablas",
    "column columns columna columnas campo campos field fields",
    "visual visuals chart charts graph grafico graficos grafica graficas",
    "validate validation validar validacion verificar comprobar",
    "load import cargar carga importar importacion",
    "file files archivo archivos fichero ficheros",
    "create crear crear construir generar generate",
    "open abrir activate activar",
    "project proyecto proyectos",
    "page pages pagina paginas hoja hojas",
    "delete remove borrar eliminar suprimir",
    "update edit editar actualizar modificar cambiar",
    "list listar enumerate enumerar",
    "refresh refrescar actualizar recargar",
    "relationship relationships relacion relaciones",
    "backup respaldo copia recuperar recovery recover rollback revertir",
    "search find buscar encontrar",
)
STOP_WORDS = set("de del el la los las en un una y con para como quiero que the of in a an and how to".split())
TASK_WORDS = {
    "pbi_add_table_from_file": "load import csv excel json html",
    "pbi_create_page_from_spec": "visual chart",
}


def normalize(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.casefold())
                   if unicodedata.category(c) != "Mn")


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", normalize(text)))


def _aliases(word: str) -> set[str]:
    alternatives = {word}
    for group in GROUPS:
        words = set(group.split())
        if word in words or (len(word) >= 3 and any(w.startswith(word) for w in words)):
            alternatives.update(words)
    return alternatives


def find_tools(catalog: dict[str, str], query: str, active: set[str], profile: str,
               full_hint: str) -> dict:
    words = _words(query) - STOP_WORDS
    scored = []
    for name, description in catalog.items():
        identifiers = _words(name + " " + TASK_WORDS.get(name, ""))
        body = _words(description)
        score = 0
        for word in words:
            alternatives = _aliases(word)
            if identifiers & alternatives:
                score += 10
            elif len(word) >= 3 and any(word in token for token in identifiers):
                score += 6
            elif body & alternatives:
                score += 1
        if normalize(query.strip()) == normalize(name):
            score += 1000
        if words and score:
            scored.append((-score, name not in active, name, description))
    scored.sort()
    matches = [
        {"name": name,
         "description": " ".join(description.split("\n\n", 1)[0].split())[:240],
         "in_active_profile": name in active,
         **({"activation": full_hint} if name not in active else {})}
        for _, _, name, description in scored[:8]
    ]
    return {"profile": profile, "matches": matches, "total_matches": len(scored),
            "truncated": len(scored) > len(matches), "full_profile": full_hint}
