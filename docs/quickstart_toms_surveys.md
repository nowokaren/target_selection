# Guía rápida: TOMs y surveys locales

Esta guía configura fuentes de candidatos TOM y un survey local para el flujo
de visibilidad y curvas de luz. No requiere LSST. Los TOMs necesitan una cuenta
propia (usuario y contraseña); los archivos del survey deben estar disponibles
localmente.

## 1. Instalar y preparar una configuración

Desde la raíz del repositorio:

```bash
python -m pip install -e '.[credentials]'
python -m pip install jupyterlab
cp configs/local_hsh.toml configs/mi_campana.toml
```

La segunda línea instala `keyring`, necesario para guardar credenciales en el
almacén del sistema. Si no se usa Jupyter, se puede omitir la tercera.

Edite `configs/mi_campana.toml`: actualice fechas, observatorio, cortes y rutas
de archivos para su computadora. Las rutas relativas se resuelven desde el
archivo TOML. No ponga contraseñas en el TOML ni en el notebook.

## 2. Configurar las cuentas de los TOMs

Guarde por separado las credenciales de cada TOM que vaya a consultar. El
comando pide el usuario y la contraseña de forma interactiva; la contraseña no
se muestra en pantalla:

```bash
target-selection auth set bhtom
target-selection auth set opm
```

Se guardan en el almacén del sistema (`keyring`). En servidores sin almacén
compatible, use el backend `netrc` o variables de entorno:

```bash
target-selection auth set bhtom --backend netrc
target-selection auth set opm --backend netrc
```

También se pueden definir `TARGET_SELECTION_BHTOM_USERNAME`,
`TARGET_SELECTION_BHTOM_PASSWORD`, `TARGET_SELECTION_OPM_USERNAME` y
`TARGET_SELECTION_OPM_PASSWORD` en el entorno. No suba esos secretos al
repositorio.

Compruebe que quedaron guardadas:

```bash
target-selection auth test bhtom
target-selection auth test opm
```

Para BHTOM se puede comprobar además el acceso remoto:

```bash
target-selection auth test bhtom --remote
```

La comprobación remota actualmente solo está implementada para BHTOM. La
consulta de candidatos sigue dependiendo de que el servicio esté accesible y
la cuenta tenga permisos. Ajuste `sources.target_providers` y los bloques
`[target_providers.*]` en el TOML para seleccionar/configurar proveedores;
`classification`, `query`, `max_pages` y `base_url` son opciones de BHTOM.
OPM admite `minimum_altitude_deg`, `observing_windows`, `sample_minutes` y
`base_url`.

## 3. Agregar un survey local

Hay dos adaptadores. Use `hsh` si los archivos siguen el formato HSH conocido
(inventario astrométrico más catálogo de coordenadas de eventos). Para otro
formato tabular, use `csv` y declare explícitamente cómo se llaman sus columnas.

### Ejemplo HSH

El TOML de ejemplo ya contiene este bloque; cambie las rutas por las de su
instalación:

```toml
[followup_surveys.casleo_hsh]
adapter = "hsh"
label = "CASLEO/HSH"
provider_name = "HSH"
inventory_path = "../data/hsh_data/image_collection_astro.csv"
target_catalog_path = "../data/hsh_data/hsh_objects.csv"
# Opcional: archivo de fotometría en la misma carpeta que el inventario.
# photometry_path = "hsh_photometry.csv"
```

El inventario HSH aporta épocas/observaciones. El catálogo de eventos aporta
las coordenadas de los targets: no use los centros WCS de las imágenes como
coordenadas de los eventos. Para mostrar puntos de una curva de luz agregue un
CSV de fotometría; el inventario por sí solo permite mostrar la cobertura de
observaciones, no mediciones de magnitud o flujo.

### Ejemplo CSV genérico

Declare el survey bajo `[followup_surveys.nombre]`. Los mapas son
`columna_original = "columna_canónica"`:

```toml
[followup_surveys.mi_survey]
adapter = "csv"
label = "Mi survey"
provider_name = "MiSurvey"
inventory_path = "../data/mi_survey/epochs.csv"

[followup_surveys.mi_survey.inventory_column_map]
event_id = "Target"
fecha_mjd = "mjd"
filtro = "band"
tiempo_exposicion_s = "exptime_s"

# Si existe fotometría, agregue estas opciones. Si no, omita todo este bloque.
# photometry_path = "../data/mi_survey/photometry.csv"

# [followup_surveys.mi_survey.photometry_column_map]
# event_id = "Target"
# fecha_mjd = "mjd"
# filtro = "band"
# magnitud = "magnitude"
# error_magnitud = "magnitude_error"
```

El archivo de épocas necesita al menos el identificador del objeto (`Target`) y
el tiempo (`mjd`); puede incluir `band`, `exptime_s`, `usable`, `RA_deg` y
`Dec_deg`. La fotometría necesita `Target`, `mjd` y una magnitud (`magnitude`)
o un flujo (`flux`); errores y banda son opcionales. `Timestamp` también se
acepta como tiempo fotométrico y se convierte a MJD. Si sus encabezados ya
coinciden con los nombres canónicos, se puede omitir el mapa correspondiente.

Luego agregue el identificador del survey a `sources.followup_surveys`, por
ejemplo:

```toml
[sources]
target_providers = ["bhtom", "opm"]
followup_surveys = ["mi_survey"]
reference_surveys = []
```

La opción `followup_surveys` en `products.monitoring_layers` activa las capas
de todos los surveys seleccionados: sus épocas y, cuando existe, su fotometría.
Para un survey individual, use el `provider_name` en minúsculas (por ejemplo,
`mysurvey`). Para HSH, el nombre de capa es `hsh`.

```toml
[products]
monitoring_report = true
monitoring_layers = ["followup_surveys", "bhtom_photometry", "opm_photometry"]
```

Las curvas y épocas del proveedor aparecen en los PNGs individuales y en el
reporte de monitoreo.

## 4. Validar y ejecutar

Antes de consultar servicios o generar productos, valide el TOML:

```bash
target-selection validate-config --config configs/mi_campana.toml
```

Ejecute el plan con fotometría y curvas de luz:

```bash
target-selection plan --config configs/mi_campana.toml \
  --with-photometry --with-lightcurves
```

Los resultados se guardan en `run.output_dir`. Para obtener candidatos y
revisar tablas, visibilidad y gráficos interactivamente, abra el notebook
`notebooks/workflows/tom_survey_visibility_lightcurves.ipynb` y ajuste allí las
rutas/configuración de su campaña. Las consultas TOM requieren internet y
credenciales; la lectura de inventarios locales no.
