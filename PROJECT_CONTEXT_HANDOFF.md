# Contexto actual del proyecto

Repositorio: `/home/karennowo/target_selection`.

## Estado de notebooks y documentación

Los notebooks activos están centralizados en `notebooks/`, agrupados por `analysis/`, `tutorials/`, `visualization/` y `workflows/`; los históricos están en `notebooks/archive/`. El índice `notebooks/README.md` describe su propósito. Los productos de análisis, corridas y cachés están bajo el único directorio raíz `outputs/`.

La documentación vigente se encuentra en `docs/`. `mkdocs.yml` navega las páginas actuales. Las fuentes de los diagramas están en `docs/assets/` y sus PNG/PDF en `docs/assets/diagrams/`. No hacer push ni crear tags sin aprobación explícita.

## Prueba del planificador solicitada

La usuaria pidió planificar las noches 2026-10-16/17, 2026-10-17/18 y 2026-10-18/19 desde El Leoncito. En la CLI, `--start-date 2026-10-16 --end-date 2026-10-18` incluye las tres noches. Los resultados de la prueba se guardan en `outputs/seasonal_plan/2026-10-16_to_2026-10-18/`.

Una primera corrida sin fotometría cargó 877 candidatos, calculó 2631 filas de visibilidad (58, 54 y 51 seleccionadas por noche) y produjo cero objetivos finales: es un control operativo, no un plan científico suficiente. Durante esa corrida se detectó que la tabla OGLE EWS publica IDs sin prefijo `OGLE-`; se corrigió la normalización antes de construir URLs y filtrar temporada. También se corrigió la procedencia de candidatos únicos en `target_selection.planning._merge_candidate_rows`, necesaria para que `collect_photometry` consulte sus proveedores. Hay pruebas de regresión en `tests/test_ogle_paths.py` y `tests/test_planning_sources.py`.

La nueva corrida ya tiene `observations.csv`, `priority_assessment.csv`, `selected_targets.csv`, gráficos y PDF en esa carpeta. No se ha interpretado científicamente aquí: comprobar las categorías, la procedencia de observaciones, las noches cubiertas y los errores de proveedores antes de utilizar el plan. Preservar resultados científicos previos bajo `outputs/`; no borrar datos históricos por la limpieza de nombres.

## Estado de Git

La consolidación de notebooks, rutas de caché y documentación estaba pendiente de commit al iniciar esta revisión. El próximo commit se identifica como transitorio: fija un checkpoint verificable del árbol actual, pero todavía hay que cerrar las diferencias entre el planificador de CLI y las configuraciones `AnalysisConfig`, revisar el script estacional duplicado y auditar rutas heredadas en scripts puntuales. El `AGENTS.md` local se redactará después de acordar que documente este árbol de trabajo actual.
