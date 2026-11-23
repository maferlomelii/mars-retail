# Mars · Retail Intelligence (prototipo Reto Tec AD2007B)

Plataforma de KPIs de inventario y venta en tienda. Lee **el Excel de Mars directo de Google Drive**
(tal cual llega, sin convertirlo) y muestra una vista por tipo de usuario. Usuario `demo`, contraseña `demo1234`.

## Pantallas
| Pantalla | Usuario | Qué muestra |
|---|---|---|
| 📊 Resumen | Dirección | Resumen en palabras, 7 KPIs con semáforo, visitadas vs. no visitadas, cadenas, semana a semana |
| 🗺️ Regiones y tiendas | Mandos medios | Mapa de calor por estado, ranking de tiendas por urgencia, Pareto del inventario estancado |
| 🚨 Alertas | Operaciones | Alertas por reglas y lista de acciones por tienda-producto (descargable) |
| 🧪 Calidad del dato | Todos | Tiendas reportadas por cadena y semana, venta sospechosa, negativos |
| 📄 Reporte | Dirección | PDF semanal de una página |

## Archivos
| Archivo | Qué es |
|---|---|
| `app.py` | La app (pantallas, login, lectura de Drive) |
| `motor.py` | Cálculos: limpia el Excel, estatus, KPIs, urgencia, alertas, calidad |
| `datos_demo.py` | Datos ficticios con el formato de Mars (modo demo) |
| `report.py` | Reporte PDF |
| `marca.yaml` | **Metas, umbrales, pesos y colores** (se cambian aquí, no en app.py) |
| `.streamlit/config.toml` | Colores y tipografías |
| `config.example.yaml` | Usuario demo |

## Cómo se actualiza
Reemplazar el Excel en Drive (clic derecho → Administrar versiones → Subir nueva versión). La app revisa cada
minuto si cambió y recalcula sola. El Excel nuevo debe tener las mismas columnas.

## Secretos (Streamlit Cloud → Settings → Secrets)
```
DRIVE_FILE_ID = "el id del Excel (lo que va entre /d/ y /view en el link)"
GOOGLE_SERVICE_ACCOUNT_JSON = '''pega aquí todo el contenido del .json de la cuenta de servicio'''
```
Sin secretos, la app funciona en **modo demo** con datos ficticios.

## Importante
- El repo debe ser **privado**: la base de Mars es confidencial.
- **Nunca** subas a GitHub el Excel ni el `.json` de la cuenta de servicio (`.gitignore` ya los excluye).
