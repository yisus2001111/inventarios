# Inventario · UES San Luis Río Colorado

Aplicación web para el control de inventario y vales de material de laboratorio: alta de productos,
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
- **Historial** de movimientos con filtros por tipo (alta, baja, vale, devolución), fecha y
  texto. Los préstamos aparecen como **Vale V-00001** (con enlace al vale) y sus
  devoluciones como **Devolución V-00001**, no como altas o bajas.
- **Eliminar registros de prueba** (solo administrador): desde el historial se marcan
  líneas y se eliminan, opcionalmente deshaciendo su efecto en la existencia. Si se marca
  una línea de un vale, se elimina el vale completo (también se puede desde el propio vale);
  su material regresa a la existencia y **su folio vuelve a estar disponible**: el siguiente
  vale toma el número libre más bajo.
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

### El alumno llena su vale desde el celular

1. En **Vales → Código QR para alumnos** imprime el cartel y pégalo en el mostrador.
2. El alumno escanea el QR (debe estar en la misma red que la computadora del programa),
   llena nombre, matrícula, materia, maestro y práctica, y busca y agrega el material.
   No necesita cuenta. Solo ve el material con existencia disponible.
3. Al enviar recibe un **folio** (`V-00001`, …) y una página que muestra el estado de su
   vale y se actualiza sola: *en espera*, *entregado* o *rechazado*. El celular recuerda
   sus solicitudes recientes y su nombre para la próxima vez.
4. En la computadora del encargado aparece un **aviso** y un contador junto a **Vales**.
   Abre la solicitud, revisa o ajusta las cantidades (0 = no se entrega) y presiona
   **Entregar material**. Hasta ese momento no se descuenta nada del inventario.
   Si no procede, la **rechaza** indicando el motivo, que el alumno ve en su celular.

Para evitar abusos, un mismo celular puede tener máximo 3 solicitudes en espera.

### Firma y foto de la credencial

Al llenar el vale, el alumno **firma con el dedo** en la pantalla y **toma una foto de su
credencial** (el celular abre la cámara; la foto se reduce automáticamente antes de
enviarse). Al entregar, el encargado ve la credencial y la firma en el vale para comparar.
La firma aparece también en el vale impreso.

En el mostrador (**+ Nuevo vale**) el alumno firma en la pantalla de la computadora o
tableta; la foto de la credencial ahí es opcional, porque el encargado la tiene a la vista.

En **Configuración** se puede dejar de pedir la firma o la foto, y **borrar las fotos de
credenciales** de vales ya cerrados o rechazados (son datos personales; las firmas se
conservan). Solo el personal con sesión iniciada puede ver estas imágenes.

### Vales de empleados

Los empleados piden material desde la misma página (enlace «¿Eres empleado?», o
directamente en `/solicitud/empleado`) con su **nombre**, la **materia o trabajo a
realizar**, el **material**, una **hora estimada de regreso** (opcional) y su **firma**. La **hora de salida** se registra
sola al entregar el material y la **hora de entrada** cuando se devuelve completo. En el
mostrador se capturan con **+ Vale de empleado**. La lista de vales se puede filtrar por
alumnos o empleados, y el CSV incluye las horas de salida y entrada.

### Captura en el mostrador

Para alumnos sin celular, el encargado puede seguir capturando el vale en
**Vales → + Nuevo vale**; ese vale se entrega y descuenta en el momento.

### Devoluciones

Cuando el alumno regresa, abre el vale, indica cuánto devuelve de cada material y
presiona **Registrar devolución**. Al devolver todo, el vale se cierra solo. Para
consumibles que no regresan, usa **Cerrar sin devolver el resto**.

La lista de **Vales** muestra primero las solicitudes por entregar y luego los vales
pendientes de devolver. Permite buscar por folio, alumno, matrícula, maestro, materia,
práctica o material, filtrar por estado y fechas, y exportar a CSV. Cada vale se puede
**imprimir** (con líneas de firma) si se necesita.

Permisos: operadores y administradores entregan, rechazan y capturan vales y
devoluciones; el rol de consulta solo puede verlos.

## Logo y nombre de la institución

En **Configuración** (solo administrador) se cambia el nombre que aparece en el
encabezado y se sube el **logo oficial** (PNG, JPG o WEBP, máximo 2 MB; mejor PNG con fondo
transparente). El logo aparece junto al nombre de usuario, en la página de los alumnos, en
el cartel del QR y en los vales impresos. Se guarda dentro de `inventario.db`.

Los colores siguen la paleta institucional de la UES: vino (Pantone 490 C) y dorado.

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

## Actualizar a una versión nueva

1. **Cierra el programa**: en la ventana donde corre presiona `Ctrl`+`C`.
   Si actualizas con el programa abierto, se mezclan la versión vieja y la nueva y
   algunas pantallas marcan error.
2. Descarga la versión nueva (ZIP o `git pull`). Si usas el ZIP, copia a la carpeta nueva
   tus archivos `inventario.db` y `.clave_secreta`.
3. Activa el entorno y ejecuta `pip install -r requirements.txt` (por si hay librerías nuevas).
4. Vuelve a abrirlo con `python run.py`. Los cambios a la base de datos se aplican solos.

Si aparece una página de «Ocurrió un error», el detalle se guarda en `errores.log`, junto a
`inventario.db`.

## Respaldos

Todo está en `inventario.db` (productos, movimientos, vales y usuarios). Para respaldar, copia ese archivo (idealmente con la
aplicación detenida) o usa **Exportar CSV**.

## Ver y modificar el código

El programa está escrito en **Python** (con Flask) y las pantallas en **HTML/CSS**. Se
recomienda abrir la carpeta con [Visual Studio Code](https://code.visualstudio.com/)
(*Archivo → Abrir carpeta*).

| Archivo / carpeta                  | Qué contiene                                                    |
|------------------------------------|-----------------------------------------------------------------|
| `run.py`                           | Arranca el programa                                             |
| `inventario/app.py`                | Productos, altas/bajas, historial, importar/exportar, configuración |
| `inventario/vales.py`              | Vales: captura, entrega, devoluciones, folios, código QR        |
| `inventario/publico.py`            | Páginas para alumnos y empleados (sin cuenta)                   |
| `inventario/evidencias.py`         | Firma y foto de credencial                                      |
| `inventario/auth.py`               | Usuarios, contraseñas, permisos e inicio de sesión              |
| `inventario/db.py`                 | Estructura de la base de datos y migraciones                    |
| `inventario/templates/`            | Pantallas (HTML). Por ejemplo `vale.html`, `solicitud.html`     |
| `inventario/static/style.css`      | Colores y diseño (la paleta está al inicio, en `:root`)         |
| `tests/test_app.py`                | Pruebas automáticas                                             |

Para trabajar en el código, arranca el programa en **modo desarrollo**: se reinicia solo
cada vez que guardas un archivo y muestra los errores con detalle (solo en esa computadora):

```bat
set INVENTARIO_DEBUG=1
python run.py
```

Para el uso diario ciérralo y ábrelo normal (sin `INVENTARIO_DEBUG`). Antes de modificar,
**haz una copia de `inventario.db`**. Después de cambiar algo, corre las pruebas.

## Pruebas

```bash
pip install pytest
python -m pytest
```

## Nota de seguridad

Tras **5 contraseñas incorrectas** para un usuario desde la misma computadora o celular
(o 20 en total) se bloquean los intentos por **15 minutos**.

Está pensada para usarse dentro de la red de la oficina. Las contraseñas se guardan
cifradas (hash), pero la conexión es HTTP sin cifrar, así que no la publiques en Internet
sin ponerla detrás de HTTPS.
