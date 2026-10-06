"""Acceso a la base de datos SQLite del inventario."""

import sqlite3
from datetime import datetime

from flask import current_app, g

# Estados de un vale:
#   solicitado: lo llenó el alumno y espera a que el encargado entregue el material
#   abierto:    material entregado, pendiente de devolver
#   cerrado:    devuelto (o el resto se dio por consumido)
#   rechazado:  el encargado no autorizó la solicitud
VALES_DDL = """CREATE TABLE IF NOT EXISTS {nombre} (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    alumno         TEXT    NOT NULL,
    matricula      TEXT    NOT NULL DEFAULT '',
    materia        TEXT    NOT NULL,
    maestro        TEXT    NOT NULL,
    practica       TEXT    NOT NULL,
    observaciones  TEXT    NOT NULL DEFAULT '',
    fecha          TEXT    NOT NULL,
    usuario        TEXT    NOT NULL DEFAULT '',
    estado         TEXT    NOT NULL DEFAULT 'abierto'
                   CHECK (estado IN ('solicitado', 'abierto', 'cerrado', 'rechazado')),
    origen         TEXT    NOT NULL DEFAULT 'mostrador',
    token          TEXT    UNIQUE,
    entregado_en   TEXT,
    entregado_por  TEXT,
    cerrado_en     TEXT,
    cerrado_por    TEXT,
    motivo_rechazo TEXT    NOT NULL DEFAULT ''
);"""

# Tipos de movimiento: alta y baja (manuales), vale (préstamo) y devolucion (de un vale).
TIPOS_ENTRADA = ("alta", "devolucion")
TIPOS = ("alta", "baja", "vale", "devolucion")
MOVIMIENTOS_DDL = """CREATE TABLE IF NOT EXISTS {nombre} (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    producto_id      INTEGER NOT NULL REFERENCES productos(id),
    tipo             TEXT    NOT NULL CHECK (tipo IN ('alta', 'baja', 'vale', 'devolucion')),
    cantidad         INTEGER NOT NULL CHECK (cantidad > 0),
    stock_resultante INTEGER NOT NULL,
    motivo           TEXT    NOT NULL DEFAULT '',
    responsable      TEXT    NOT NULL DEFAULT '',
    fecha            TEXT    NOT NULL,
    vale_id          INTEGER REFERENCES vales(id)
);"""

ESQUEMA = """
CREATE TABLE IF NOT EXISTS productos (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo        TEXT    NOT NULL UNIQUE,
    nombre        TEXT    NOT NULL,
    categoria     TEXT    NOT NULL DEFAULT '',
    ubicacion     TEXT    NOT NULL DEFAULT '',
    unidad        TEXT    NOT NULL DEFAULT 'pza',
    stock         INTEGER NOT NULL DEFAULT 0 CHECK (stock >= 0),
    stock_minimo  INTEGER NOT NULL DEFAULT 0 CHECK (stock_minimo >= 0),
    activo        INTEGER NOT NULL DEFAULT 1,
    creado_en     TEXT    NOT NULL
);

{movimientos}

CREATE TABLE IF NOT EXISTS vale_archivos (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    vale_id  INTEGER NOT NULL REFERENCES vales(id),
    tipo     TEXT    NOT NULL CHECK (tipo IN ('firma', 'credencial')),
    mime     TEXT    NOT NULL,
    datos    BLOB    NOT NULL,
    fecha    TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_archivos_vale ON vale_archivos(vale_id);

CREATE TABLE IF NOT EXISTS ajustes (
    clave  TEXT PRIMARY KEY,
    valor  BLOB
);

CREATE TABLE IF NOT EXISTS usuarios (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario     TEXT    NOT NULL UNIQUE,
    nombre      TEXT    NOT NULL DEFAULT '',
    rol         TEXT    NOT NULL CHECK (rol IN ('admin', 'operador', 'consulta')),
    contrasena  TEXT    NOT NULL,
    activo      INTEGER NOT NULL DEFAULT 1,
    creado_en   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS cambios_ubicacion (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    producto_id  INTEGER NOT NULL REFERENCES productos(id),
    anterior     TEXT    NOT NULL,
    nueva        TEXT    NOT NULL,
    usuario      TEXT    NOT NULL,
    fecha        TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ubic_producto ON cambios_ubicacion(producto_id);
{vales}
CREATE TABLE IF NOT EXISTS vale_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    vale_id      INTEGER NOT NULL REFERENCES vales(id),
    producto_id  INTEGER NOT NULL REFERENCES productos(id),
    cantidad     INTEGER NOT NULL CHECK (cantidad > 0),
    devuelto     INTEGER NOT NULL DEFAULT 0 CHECK (devuelto >= 0 AND devuelto <= cantidad)
);

CREATE INDEX IF NOT EXISTS idx_vale_items_vale ON vale_items(vale_id);
CREATE INDEX IF NOT EXISTS idx_vale_items_producto ON vale_items(producto_id);
CREATE INDEX IF NOT EXISTS idx_vales_fecha ON vales(fecha);
CREATE INDEX IF NOT EXISTS idx_mov_producto ON movimientos(producto_id);
CREATE INDEX IF NOT EXISTS idx_mov_fecha ON movimientos(fecha);
CREATE INDEX IF NOT EXISTS idx_mov_vale ON movimientos(vale_id);
""".replace("{vales}", VALES_DDL.format(nombre="vales")).replace(
    "{movimientos}", MOVIMIENTOS_DDL.format(nombre="movimientos"))


class ErrorInventario(Exception):
    """Error de validación de una operación de inventario."""


def ahora():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def cerrar_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    migrar_vales(db)
    migrar_movimientos(db)
    db.executescript(ESQUEMA)


def migrar_vales(db):
    """Actualiza la tabla de vales de versiones anteriores (sin solicitudes de alumnos).

    SQLite no permite modificar un CHECK, así que se reconstruye la tabla conservando
    todos los datos.
    """
    fila = db.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'vales'"
                      ).fetchone()
    if fila is None or "solicitado" in fila[0]:
        return
    columnas = ("id, alumno, matricula, materia, maestro, practica, observaciones, fecha, "
                "usuario, estado, cerrado_en, cerrado_por")
    db.execute("PRAGMA foreign_keys = OFF")
    try:
        db.executescript(f"""
            BEGIN;
            {VALES_DDL.format(nombre="vales_nueva")}
            INSERT INTO vales_nueva ({columnas}, entregado_en, entregado_por)
                SELECT {columnas}, fecha, usuario FROM vales;
            DROP TABLE vales;
            ALTER TABLE vales_nueva RENAME TO vales;
            COMMIT;
        """)
    finally:
        db.execute("PRAGMA foreign_keys = ON")


def crear_producto(db, codigo, nombre, categoria="", ubicacion="", unidad="pza",
                   stock_inicial=0, stock_minimo=0, responsable="", motivo="Alta inicial"):
    """Crea un producto y, si trae existencia inicial, registra el movimiento de alta."""
    codigo = (codigo or "").strip()
    nombre = (nombre or "").strip()
    if not codigo or not nombre:
        raise ErrorInventario("El código y el nombre son obligatorios.")
    if stock_inicial < 0 or stock_minimo < 0:
        raise ErrorInventario("Las cantidades no pueden ser negativas.")
    if db.execute("SELECT 1 FROM productos WHERE codigo = ?", (codigo,)).fetchone():
        raise ErrorInventario(f"Ya existe un producto con el código «{codigo}».")

    fecha = ahora()
    cur = db.execute(
        """INSERT INTO productos (codigo, nombre, categoria, ubicacion, unidad,
                                  stock, stock_minimo, creado_en)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (codigo, nombre, categoria.strip(), ubicacion.strip(), unidad.strip() or "pza",
         stock_inicial, stock_minimo, fecha),
    )
    producto_id = cur.lastrowid
    if stock_inicial > 0:
        db.execute(
            """INSERT INTO movimientos (producto_id, tipo, cantidad, stock_resultante,
                                        motivo, responsable, fecha)
               VALUES (?, 'alta', ?, ?, ?, ?, ?)""",
            (producto_id, stock_inicial, stock_inicial, motivo, responsable.strip(), fecha),
        )
    return producto_id


def migrar_movimientos(db):
    """Agrega los tipos «vale» y «devolucion» a movimientos de versiones anteriores.

    Los movimientos que antes se guardaban como baja/alta de un vale se reclasifican
    y se ligan a su vale a partir del folio escrito en el motivo.
    """
    fila = db.execute("SELECT sql FROM sqlite_master WHERE type = 'table' "
                      "AND name = 'movimientos'").fetchone()
    if fila is None or "devolucion" in fila[0]:
        return
    columnas = ("id, producto_id, tipo, cantidad, stock_resultante, motivo, responsable, "
                "fecha")
    db.execute("PRAGMA foreign_keys = OFF")
    try:
        db.executescript(f"""
            BEGIN;
            {MOVIMIENTOS_DDL.format(nombre="movimientos_nueva")}
            INSERT INTO movimientos_nueva ({columnas}) SELECT {columnas} FROM movimientos;
            UPDATE movimientos_nueva SET tipo = 'vale',
                   vale_id = CAST(substr(motivo, 8, 5) AS INTEGER)
             WHERE tipo = 'baja' AND motivo LIKE 'Vale V-%';
            UPDATE movimientos_nueva SET tipo = 'devolucion',
                   vale_id = CAST(substr(motivo, 19, 5) AS INTEGER)
             WHERE tipo = 'alta' AND motivo LIKE 'Devolución vale V-%';
            DROP TABLE movimientos;
            ALTER TABLE movimientos_nueva RENAME TO movimientos;
            COMMIT;
        """)
    finally:
        db.execute("PRAGMA foreign_keys = ON")


def registrar_movimiento(db, producto_id, tipo, cantidad, motivo="", responsable="",
                         vale_id=None):
    """Registra una entrada (alta, devolución) o salida (baja, vale) y actualiza la existencia.

    Devuelve la existencia resultante. Lanza ErrorInventario si la operación no es válida.
    """
    if tipo not in TIPOS:
        raise ErrorInventario("Tipo de movimiento no válido.")
    if cantidad is None or cantidad <= 0:
        raise ErrorInventario("La cantidad debe ser mayor que cero.")

    producto = db.execute("SELECT * FROM productos WHERE id = ?", (producto_id,)).fetchone()
    if producto is None:
        raise ErrorInventario("El producto no existe.")
    if not producto["activo"]:
        raise ErrorInventario("El producto está desactivado; reactívalo para moverlo.")

    if tipo in TIPOS_ENTRADA:
        nuevo = producto["stock"] + cantidad
    else:
        if cantidad > producto["stock"]:
            raise ErrorInventario(
                f"No hay existencia suficiente de «{producto['nombre']}»: hay "
                f"{producto['stock']} {producto['unidad']} y se intentó sacar {cantidad}."
            )
        nuevo = producto["stock"] - cantidad

    db.execute("UPDATE productos SET stock = ? WHERE id = ?", (nuevo, producto_id))
    db.execute(
        """INSERT INTO movimientos (producto_id, tipo, cantidad, stock_resultante,
                                    motivo, responsable, fecha, vale_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (producto_id, tipo, cantidad, nuevo, motivo.strip(), responsable.strip(), ahora(),
         vale_id),
    )
    return nuevo


def eliminar_movimientos(db, ids, corregir_existencia=True):
    """Borra movimientos manuales (para limpiar pruebas). Devuelve cuántos se borraron.

    Con `corregir_existencia` se deshace su efecto en la existencia del producto. Los
    movimientos de vales no se borran aquí: se eliminan junto con su vale.
    """
    if not ids:
        return 0
    filas = db.execute(
        f"""SELECT m.*, p.nombre, p.stock FROM movimientos m
            JOIN productos p ON p.id = m.producto_id
            WHERE m.id IN ({', '.join('?' * len(ids))}) AND m.vale_id IS NULL
            ORDER BY m.id DESC""",
        list(ids),
    ).fetchall()
    for m in filas:
        if corregir_existencia:
            efecto = m["cantidad"] if m["tipo"] in TIPOS_ENTRADA else -m["cantidad"]
            stock = db.execute("SELECT stock FROM productos WHERE id = ?",
                               (m["producto_id"],)).fetchone()[0]
            if stock - efecto < 0:
                raise ErrorInventario(
                    f"No se puede deshacer la {m['tipo']} de {m['cantidad']} de «{m['nombre']}»: "
                    f"la existencia quedaría negativa (hay {stock}).")
            db.execute("UPDATE productos SET stock = ? WHERE id = ?",
                       (stock - efecto, m["producto_id"]))
        db.execute("DELETE FROM movimientos WHERE id = ?", (m["id"],))
    return len(filas)


def leer_ajuste(db, clave, defecto=None):
    fila = db.execute("SELECT valor FROM ajustes WHERE clave = ?", (clave,)).fetchone()
    return defecto if fila is None else fila[0]


def guardar_ajuste(db, clave, valor):
    db.execute("INSERT INTO ajustes (clave, valor) VALUES (?, ?) "
               "ON CONFLICT (clave) DO UPDATE SET valor = excluded.valor", (clave, valor))


def cambiar_ubicacion(db, producto_id, nueva, usuario):
    """Cambia la ubicación de un producto y deja registro. Devuelve True si cambió."""
    nueva = (nueva or "").strip()
    fila = db.execute("SELECT ubicacion FROM productos WHERE id = ?", (producto_id,)).fetchone()
    if fila is None:
        raise ErrorInventario("El producto no existe.")
    if fila["ubicacion"] == nueva:
        return False
    db.execute("UPDATE productos SET ubicacion = ? WHERE id = ?", (nueva, producto_id))
    db.execute(
        """INSERT INTO cambios_ubicacion (producto_id, anterior, nueva, usuario, fecha)
           VALUES (?, ?, ?, ?, ?)""",
        (producto_id, fila["ubicacion"], nueva, usuario, ahora()),
    )
    return True
