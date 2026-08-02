"""Fixtures de PowerBI-MCP.

Estrategia hibrida (ver README.md de esta carpeta):

- `synthetic/` : fixtures 100% inventados, VERSIONADOS. Son la base de todas
  las pruebas reproducibles.
- `local/`     : copia de solo lectura de un proyecto real, IGNORADA por git.
  Opcional; solo para comprobar compatibilidad con PBIR generado por Power BI
  Desktop de verdad. Ninguna prueba obligatoria depende de ella.
"""
