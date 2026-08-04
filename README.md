# Selección de targets MOP con cobertura Rubin

Este proyecto cruza los targets visibles de MOP con la cobertura de un Data Preview/Data Release de Rubin y genera tablas, mapas del cielo y reportes gráficos por target. La ejecución validada actualmente es **DP2**; los perfiles DP0.1, DP0.2 y DP1 pueden requerir ajustar colecciones o tablas según la instancia del RSP.

## Archivos del proyecto

- `mop_lsst.ipynb`: punto de entrada; se ejecuta en orden.
- `target_selection_pipeline.py`: consultas, caché, tablas, mapas y reportes.
- `data_release_config.py`: configuración de DP0.1, DP0.2, DP1 y DP2.

Los tres archivos deben permanecer en la misma carpeta.

## Requisitos

1. Una cuenta con acceso al Rubin Science Platform (RSP) y al DataRelease elegido.
2. Ejecutar el notebook dentro del entorno científico del RSP.
3. Clonar este repositorio e instalarlo dentro del entorno RSP:

```bash
git clone https://github.com/nowokaren/target_selection.git
cd target_selection
python -m pip install -e .
```

La instalación descarga automáticamente la versión probada de `mop_api` desde GitHub. Las bibliotecas `lsst.*` no se instalan con pip: son provistas por el entorno científico del RSP.

## Uso rápido

Abrir `mop_lsst.ipynb` y modificar la celda **Configuración y conexiones**:

```python
DATA_RELEASE_NAME = "DP2"  # DP0.1, DP0.2, DP1 o DP2
START_DATE = "2026-08-01"
END_DATE = "2026-08-15"
OUTPUT_DIR = Path("outputs")
OBSERVATORY = "El Leoncito"
```

Luego reiniciar el kernel y ejecutar **Run All**. La primera ejecución puede tardar por las consultas TAP, Butler y MOP; las siguientes reutilizan cachés.

- `max_workers=4`: concurrencia para consultas.
- `reuse_cache=True`: reutiliza descargas y consultas anteriores.
- `overwrite_target_plots=False`: conserva reportes vigentes.

## Outputs

Para DP2, una corrida queda en:

```text
outputs/
├── photometry/                         # Fotometría MOP, un CSV por target
├── mop_event_cache/                    # Caché de datos MOP
└── YYYY-MM-DD_to_YYYY-MM-DD/
    ├── manifest.json                   # Configuración de la corrida
    ├── plot_errors.csv                 # Errores aislados de reportes
    ├── tables/
    │   ├── visible_targets_daily.csv   # Visibilidad por fecha
    │   ├── visible_summary.csv         # Visibilidad + parámetros MOP
    │   ├── coverage_raw.csv            # Filas Rubin visita/detector
    │   ├── coverage_summary.csv        # Cobertura y visitas por banda
    │   ├── combined_targets.csv        # Tabla completa MOP + Rubin
    │   ├── target_summary.csv          # Resumen científico
    │   └── target_summary.png          # Tabla resumen visual
    ├── sky_plots/                      # Mapas de targets
    └── targets/
        ├── <Target>_target_report.png  # Reporte individual
        └── report_versions.json        # Control de caché
```

Para otros DataReleases se agrega una carpeta con el nombre del release.

### Qué tabla consultar

- Lista final y todas las propiedades: `combined_targets.csv`.
- Visitas por filtro, puntos MOP, `t_E`, `t_0` y `u_0`: `target_summary.csv`.
- Visitas/detectores sin agregar: `coverage_raw.csv`.
- Fotometría completa: `outputs/photometry/<Target>.csv`.

Los conteos `n_visits_<filtro>` representan `visitId` únicos. Sólo se crea un reporte individual si existe al menos un coadd que contenga la posición.

## Repetir o actualizar una corrida

- Misma configuración: `reuse_cache=True`.
- Forzar datos nuevos: `reuse_cache=False`.
- Regenerar todos los PNG: `overwrite_target_plots=True`.
- Después de modificar código, reiniciar el kernel o ejecutar nuevamente la celda de imports.

## Compartir el proyecto

Este proyecto debe publicarse en un repositorio Git separado de `mop_api`. El `.gitignore` excluye `outputs/`, cachés y checkpoints. Para compartir resultados concretos, comprimir sólo la carpeta de la corrida correspondiente.

La dependencia declarada en `pyproject.toml` fija `mop_api` al commit probado `44b4831`. Para adoptar una versión nueva de esa API hay que actualizar explícitamente ese hash y volver a ejecutar las pruebas.

## Desarrollo y pruebas

```bash
python -m pip install -e ".[test]"
pytest
```

GitHub Actions ejecuta estas pruebas automáticamente en cada push y pull request. Las pruebas unitarias no requieren acceso a Rubin; la ejecución completa del notebook sí requiere RSP.
