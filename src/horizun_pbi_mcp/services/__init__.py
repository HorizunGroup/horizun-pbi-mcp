"""Servicios transversales de seguridad de PowerBI-MCP (Fase 1A).

Estos modulos existen para contener riesgos, no para anadir funcionalidad:

- `paths`         : ninguna ruta de lectura/escritura sale del proyecto activo.
- `dax_guard`     : solo se ejecutan consultas reconocidas como de solo lectura.
- `project_state` : no se escribe PBIR si Power BI Desktop puede tenerlo abierto.
- `txn`           : escritura multiarchivo con journal, verificacion y rollback.

Ninguno de ellos cambia la firma de las 34 tools.
"""
