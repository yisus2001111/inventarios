# Inventario interno

Aplicación web sencilla para llevar el control de inventario interno: alta de productos,
**altas (entradas)** y **bajas (salidas)** de existencia con historial de quién, cuándo y por qué.

Funciona en el navegador; se instala en una computadora de la oficina y cualquier equipo
de la red puede usarla. Los datos se guardan en un único archivo SQLite (`inventario.db`).

## Funciones

- **Vales digitales de material para prácticas** (reemplazan el vale en papel): folio,
  fecha y hora automáticas, alumno, matrícula, materia, maestro, práctica y la lista de
  material con cantidad, nombre y número de inventario. Control de devoluciones.

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
- **Cambiar de lugar**: cambiar la ubicación de un producto o de varios a la vez
  (marcándolos en la lista), con historial de cada cambio (de dónde, a dónde, quién y cuándo).
- **Usuarios con contraseña y permisos**: cada movimiento queda firmado con el usuario que lo hizo.

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
| `INVENTARIO_SECRET_KEY`  | (se genera sola)         | Clave para firmar las sesiones           |

Si no defines `INVENTARIO_SECRET_KEY`, la primera vez se genera una clave aleatoria y se
guarda en el archivo `.clave_secreta` junto a la base de datos. No lo compartas.

## Usuarios y permisos

La primera vez que abras el programa te pedirá crear la cuenta del **administrador**.
Después, desde el menú **Usuarios**, el administrador da de alta a los demás:

| Rol               | Ver y exportar | Vales, altas / bajas y ubicación  | Productos, importar y usuarios |
|-------------------|:--------------:|:---------------------------------:|:------------------------------:|
| **Consulta**      | ✔              |                                   |                                |
| **Operador**      | ✔              | ✔                                 |                                |
| **Administrador** | ✔              | ✔                                 | ✔                              |

- Sin iniciar sesión no se puede ver ni modificar nada.
- Cada quien cambia su contraseña haciendo clic en su nombre (arriba a la derecha).
- Si alguien olvida su contraseña, un administrador le asigna una nueva desde **Usuarios**.
- Para quitarle el acceso a alguien, desmarca «Usuario activo»; su historial se conserva.
- Siempre debe quedar al menos un administrador activo.
- La sesión se cierra sola después de 12 horas.

## Vales de material

1. El encargado entra a **Vales → + Nuevo vale** y captura los datos del alumno, la
   materia, el maestro y la práctica. Materias, maestros y prácticas se autocompletan con
   los que ya se usaron.
2. Agrega el material: escribe el nombre o el número de inventario (o escanéalo con un
   lector de código de barras; cada lectura agrega un renglón).
3. **Registrar vale**: se asigna folio (`V-00001`, …) y el material se descuenta del inventario.
   No se permite prestar más de lo que hay en existencia.
4. Cuando el alumno regresa, abre el vale, indica cuánto devuelve de cada material y
   presiona **Registrar devolución**. Al devolver todo, el vale se cierra solo.
   Para consumibles que no regresan, usa **Cerrar sin devolver el resto**.

La lista de **Vales** muestra por defecto los pendientes de devolver, y permite buscar por
folio, alumno, matrícula, maestro, materia, práctica o material, filtrar por fechas y
exportar a CSV. Cada vale se puede **imprimir** (con líneas de firma) si se necesita.

Permisos: operadores y administradores capturan vales y devoluciones; el rol de consulta
solo puede verlos.

## Cargar tu inventario actual

1. Entra a **Importar** y descarga la plantilla.
2. Llénala en Excel (columnas `codigo`, `nombre`, `categoria`, `ubicacion`, `unidad`,
   `stock`, `stock_minimo`) y guárdala como CSV.
3. Súbela. Los códigos nuevos se crean con su existencia inicial (registrada como alta);
   los que ya existen solo actualizan sus datos descriptivos.

### Actualizar datos desde Excel (ubicaciones, nombres, categorías…)

1. **Exportar CSV** en la pantalla de productos.
2. Cambia en Excel lo que necesites (sin tocar la columna `codigo`) y guárdalo como CSV.
3. Súbelo en **Importar**. Solo se actualizan las columnas que traiga el archivo, así que
   también puedes subir uno con solo `codigo` y `ubicacion`. La existencia nunca se cambia
   desde la importación.

## Respaldos

Todo está en `inventario.db` (productos, movimientos, vales y usuarios). Para respaldar, copia ese archivo (idealmente con la
aplicación detenida) o usa **Exportar CSV**.

## Pruebas

```bash
pip install pytest
python -m pytest
```

## Nota de seguridad

Está pensada para usarse dentro de la red de la oficina. Las contraseñas se guardan
cifradas (hash), pero la conexión es HTTP sin cifrar, así que no la publiques en Internet
sin ponerla detrás de HTTPS.
