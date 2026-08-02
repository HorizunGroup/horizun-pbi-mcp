"""Bitacora de cambios en outputs/change_log.md (Fase 11)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional

from config import get_settings
from logging_config import get_logger
from utils.file_utils import iso_now

log = get_logger("changelog")


def record_change(
    tool: str,
    summary: str,
    files: Optional[List[str]] = None,
    backup: Optional[str] = None,
) -> None:
    """Agrega una entrada a outputs/change_log.md (best-effort)."""
    settings = get_settings()
    path = settings.outputs_dir / "change_log.md"
    lines = [f"\n## {iso_now()} — `{tool}`", "", summary]
    if files:
        lines.append("")
        lines.append("**Archivos modificados:**")
        for f in files:
            lines.append(f"- `{f}`")
    if backup:
        lines.append("")
        lines.append(f"**Backup:** `{backup}`")
    lines.append("")
    try:
        settings.outputs_dir.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("# Change log — Horizun PBI MCP\n", encoding="utf-8")
        with path.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
    except OSError as exc:  # pragma: no cover
        log.warning("No se pudo escribir change_log.md: %s", exc)
