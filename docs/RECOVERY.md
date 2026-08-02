# Guía de recuperación

Qué hacer cuando algo se queda a medias. **Nada de lo que sigue borra datos**: todas las operaciones dejan el original en un journal.

---

## 1. Lo primero: mirar, no tocar

```bash
python scripts/doctor.py
```

Desde un cliente MCP:

```
pbi_health_check          → ¿hay journals pendientes?
pbi_list_pending_journals → cuáles y de qué operación
pbi_inspect_journal       → qué archivos toca y si siguen como el original
```

`pbi_inspect_journal` es **solo lectura**. No restaura nada; te dice si hace falta.

---

## 2. Cómo leer un journal

Cada journal es una carpeta en la raíz de backups del proyecto:

```
<backups>/<nombre>_<hash12>/<fecha>_<request_id>/
    manifest.json     qué operación, cuándo, qué archivos, con su sha256
    files/            copia del original de cada archivo tocado
```

`manifest.json` → `status`:

| Estado | Significa | Acción |
|---|---|---|
| `committed` | Terminó bien | Ninguna |
| `rolled_back` | Falló y se revirtió | Ninguna, salvo que haya conflictos |
| `compensated` | Se deshizo algo ya confirmado | Ninguna |
| `open` | **El proceso murió a medias** | Revisar |
| `unreadable` | El manifiesto no se puede leer | Revisar a mano |

Y por archivo, `outcome`:

| Outcome | Significa |
|---|---|
| `restored` | Devuelto a su estado original |
| `unchanged` | Nunca se llegó a escribir |
| `rollback_conflict` | **Cambió por fuera después**; no se tocó, a propósito |
| `rollback_failed` | Se intentó restaurar y falló |

---

## 3. Journal `open`: recuperación manual

Significa que el proceso murió entre la escritura y el cierre. El original está en `files/`.

1. Cierra Power BI Desktop.
2. `pbi_inspect_journal` sobre ese journal. Mira `matches_original` de cada archivo:
   - `true` → ese archivo ya está como al principio.
   - `false` → tiene nuestra escritura a medias, o un cambio externo.
3. Copia desde `files/<ruta relativa>` sobre `<proyecto>/<ruta relativa>`.
4. Vuelve a inspeccionar: `matches_original` debe ser `true` en todos.

**No hay restauración automática al arrancar**, a propósito: reanudar solo una operación que el usuario quizá ya deshizo a mano puede ser peor que dejarlo quieto.

---

## 4. `rollback_conflict`: no es un fallo

Alguien modificó el archivo **después** de que lo escribiéramos. El rollback lo respetó en lugar de pisarlo.

Decide tú:

- **Quedarte con el cambio externo** → nada que hacer.
- **Volver al original** → cópialo desde `files/`.

---

## 5. Situaciones concretas

| Síntoma | Qué pasó | Solución |
|---|---|---|
| `project_open_in_desktop` | Desktop tiene el proyecto abierto, o no se pudo descartar | Ciérralo del todo y repite. Es intencionado |
| `stale_session` | El puerto cambió o lo ocupa otro proceso | `pbi_list_desktop_models` y `pbi_select_model` |
| `plan_token_stale` | El proyecto cambió desde que se calculó el plan | Regenera el plan |
| `request_id_conflict` | Mismo `request_id`, otros argumentos | Usa uno nuevo |
| `dual_mode_not_safely_available` | `mode="both"` | Elige `live` o `pbip` |
| `rollback_incomplete` | La reversión no quedó limpia | Sigue el §3 con el journal del error |
| `bulk_partially_applied` | Se escribió el disco y falló lo vivo, sin poder compensar | `details.journal` trae los originales |
| `.tmp` dentro del `.pbip` | No debería ocurrir desde 1A | Es basura: bórralo. El original está intacto |
| Los cambios no se ven en Desktop | PBIR se carga al abrir | Cierra y reabre el informe |
| Cambios de modelo perdidos | Con `mode="live"` no se persisten sin Ctrl+S | Vuelve a aplicarlos y guarda |

---

## 6. Volver a un estado conocido

```
pbi_backup_pbip_project(mode="folder", scope="both")
```

Crea una copia completa con manifiesto de hashes. Para restaurarla, cierra Desktop y copia la carpeta de vuelta.

Los backups y journals **nunca se purgan solos**, y los que ya tuvieras en tu proyecto no se tocan.

---

## 7. Qué nunca ocurre

- No se escribe fuera del proyecto activo.
- No se escribe PBIR si Desktop puede tener el proyecto abierto.
- No se sobrescribe un JSON que no parsea.
- No se pisa un cambio externo durante un rollback.
- No se reporta éxito si la reversión no quedó limpia.
