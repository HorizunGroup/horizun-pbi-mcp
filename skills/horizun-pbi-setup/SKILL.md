---
name: horizun-pbi-setup
description: Instala, actualiza, repara y verifica el runtime local de Horizun PBI MCP cuando se usa como plugin de Codex o Claude. Úsala si el servidor solo muestra pbi_install_runtime/pbi_install_status, si faltan dependencias, DLL o esquemas, si el plugin no arranca, o si el usuario pide instalar o diagnosticar el plugin.
---

# Instalar Horizun PBI MCP

El plugin no incluye binarios propietarios. Prepara un entorno Python aislado
y descarga las DLL y esquemas fijados, verificando sus hashes. **La meta es
que la persona no pelee con dependencias: las resuelves TÚ**, con el
instalador de un pegado y los remedios de esta skill.

## El prompt de instalación (lo único que la persona pega)

Cuando alguien diga "instálame el MCP de Power BI" (o llegue con este prompt),
este es el runbook completo. Todo es a nivel usuario; nunca pidas
administrador.

1. **Corre el instalador de un pegado** en PowerShell:
   `irm https://raw.githubusercontent.com/HorizunGroup/horizun-pbi-mcp/main/scripts/instalar.ps1 | iex`
   Instala Python real, Git, Node (opcional), ajusta la política de ejecución
   del usuario y registra el plugin. Es idempotente: repetirlo es seguro.
2. **Atiende sus `[PENDIENTE]`**: cada uno trae el remedio o el id exacto de
   paquete user-scope para pedirle a TI. Resuélvelos tú cuando sea posible
   (p. ej. reabrir la terminal por PATH viejo) y repite el paso 1.
3. Con el plugin registrado, la instalación del runtime empieza sola al
   cargar el plugin. Llama `pbi_install_status` para ver el avance.
4. Si terminó en `failed`, llama `pbi_install_runtime` UNA vez: relanzar
   REANUDA desde el paso caído (las descargas van verificadas por hash y los
   pasos de red reintentan solos 3 veces).
5. En `ready`, pide reiniciar Codex o Claude. Al volver, comprueba que
   `tools/list` expone las tools `pbi_*` completas, no solo las dos de
   instalación.
6. Si falla, informa el paso y el mensaje del status. No borres el runtime ni
   ocultes el error.

## Síntomas de campo y su remedio (medidos en sesión real, 2026-08-12)

- **El plugin no aparece ni da error** → `python` era el alias de la
  Microsoft Store. Desde 1.5.1 el lanzador (`launch.cmd`) lo esquiva solo
  (`py -3` primero); si no hay NINGÚN Python real, su stderr trae el comando
  (`winget install -e --id Python.Python.3.12 --scope user`).
- **"Git is required for local sessions"** → lo exige Claude Code:
  `winget install -e --id Git.Git --scope user`.
- **"running scripts is disabled"** →
  `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned -Force`.
- **Comando recién instalado "no reconocido"** → PATH viejo en esa terminal:
  ciérrala y ábrela de nuevo (o reinicia el editor completo).
- **Descarga muere a mitad** → carrera DNS IPv6 conocida contra
  nuget.org/developer.microsoft.com; ya se reintenta solo. Si agota los
  intentos, relanzar reanuda: nada descargado se repite.
- **Sin permisos ni para winget** → entrega a TI la lista que imprime el
  instalador (`Python.Python.3.12`, `Git.Git`, `OpenJS.NodeJS.LTS`, todos
  user-scope). Esa impresión ES el ticket.

## Límites que debes explicar

- No hay un `.exe` propio ni hace falta registrar manualmente un servidor MCP.
- Sí se requiere Windows, Power BI Desktop y Python 3.10 o posterior. Un MCP
  remoto no puede controlar el Desktop ni los archivos PBIP locales.
- Node 20 es opcional: solo añade el validador oficial de informes PBIR.
- Nunca copies DLL, credenciales, `.pbix` o `.pbip` dentro del plugin.
- Cuenta compartida del equipo: funciona, pero avisa del riesgo de
  desactivación temporal o permanente.
