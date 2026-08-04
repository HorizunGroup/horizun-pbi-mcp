---
name: horizun-pbi-setup
description: Instala, actualiza, repara y verifica el runtime local de Horizun PBI MCP cuando se usa como plugin de Codex o Claude. Úsala si el servidor solo muestra pbi_install_runtime/pbi_install_status, si faltan dependencias, DLL o esquemas, o si el usuario pide instalar o diagnosticar el plugin.
---

# Instalar Horizun PBI MCP

El plugin no incluye binarios propietarios. Prepara un entorno Python aislado
y descarga las DLL y esquemas fijados, verificando sus hashes.

## Flujo

1. La instalación empieza automáticamente al cargar el plugin. Llama
   `pbi_install_status` para ver su avance.
2. Si terminó en `failed` y el usuario quiere reintentar, llama
   `pbi_install_runtime` una sola vez.
3. Consulta `pbi_install_status` hasta que indique `ready` o `failed`. No inicies
   procesos adicionales mientras esté `installing`.
4. Si termina en `ready`, pide reiniciar Codex o Claude. Al volver, comprueba que
   `tools/list` expone las 121 tools `pbi_*`, no solo las dos de instalación.
5. Si falla, informa el paso y el mensaje devueltos por el status. No borres el
   runtime ni ocultes el error; una nueva llamada permite reintentar.

## Límites que debes explicar

- No hay un `.exe` propio ni hace falta registrar manualmente un servidor MCP.
- Sí se requiere Windows, Power BI Desktop y Python 3.10 o posterior. Un MCP
  remoto no puede controlar el Desktop ni los archivos PBIP locales.
- Node 20 es opcional: solo añade el validador oficial de informes PBIR.
- Nunca copies DLL, credenciales, `.pbix` o `.pbip` dentro del plugin.
