# Inventario interno

Aplicación web sencilla para llevar el control de inventario interno: alta de productos,
**altas (entradas)** y **bajas (salidas)** de existencia con historial de quién, cuándo y por qué.

Funciona en el navegador; se instala en una computadora de la oficina y cualquier equipo
de la red puede usarla. Los datos se guardan en un único archivo SQLite (`inventario.db`).

## Funciones

- **Productos**: código, nombre, categoría, ubicación, unidad, existencia y stock mínimo.
  Búsqueda, filtro por categoría y paginación (pensado para cientos o miles de artículos).
- **Altas y bajas**: cada movimiento guarda cantidad, motivo, responsable, fecha y la
  existencia resultante. No se permite dar de baja más de lo que hay.
- **Alta / baja rápida**: se teclea o escanea el código (compatible con lector de código de barras).
- **Alertas de stock bajo**: los productos en o por debajo del mínimo se resaltan y se pueden filtrar.
- **Historial** de movimientos con filtros por tipo, fecha y texto.
- **Importar CSV** para cargar el inventario inicial (por ejemplo, desde Excel) y
  **exportar CSV** de productos y movimientos.
- **Desactivar** productos que ya no se usan (solo si su existencia es 0), sin perder su historial.

La existencia solo cambia mediante movimientos de alta/baja, así siempre queda registro
de cada cambio.

## Instalación

Requiere Python 3.9 o superior.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate     Linux/Mac: source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

Abre <http://localhost:5000>. Desde otros equipos de la red usa la IP de la computadora
donde corre, por ejemplo `http://192.168.1.20:5000`.

### Configuración (variables de entorno, opcionales)

| Variable                 | Por defecto              | Uso                                      |
|--------------------------|--------------------------|------------------------------------------|
| `INVENTARIO_DB`          | `inventario.db`          | Ruta del archivo de base de datos        |
| `INVENTARIO_PORT`        | `5000`                   | Puerto                                   |
| `INVENTARIO_HOST`        | `0.0.0.0`                | Interfaz de red                          |
| `INVENTARIO_SECRET_KEY`  | `cambia-esta-clave`      | Clave para firmar la sesión; cámbiala    |

## Cargar tu inventario actual

1. Entra a **Importar** y descarga la plantilla.
2. Llénala en Excel (columnas `codigo`, `nombre`, `categoria`, `ubicacion`, `unidad`,
   `stock`, `stock_minimo`) y guárdala como CSV.
3. Súbela. Los códigos nuevos se crean con su existencia inicial (registrada como alta);
   los que ya existen solo actualizan sus datos descriptivos.

## Respaldos

Todo está en `inventario.db`. Para respaldar, copia ese archivo (idealmente con la
aplicación detenida) o usa **Exportar CSV**.

## Pruebas

```bash
pip install pytest
python -m pytest
```

## Nota de seguridad

La aplicación no tiene inicio de sesión: está pensada para una red interna de confianza.
No la expongas a Internet sin agregar autenticación.
