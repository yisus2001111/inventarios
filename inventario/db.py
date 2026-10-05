"""Acceso a la base de datos SQLite del inventario."""

import sqlite3
from datetime import datetime

from flask import current_app, g

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

CREATE TABLE IF NOT EXISTS movimientos (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    producto_id      INTEGER NOT NULL REFERENCES productos(id),
    tipo             TEXT    NOT NULL CHECK (tipo IN ('alta', 'baja')),
    cantidad         INTEGER NOT NULL CHECK (cantidad > 0),
    stock_resultante INTEGER NOT NULL,
    motivo           TEXT    NOT NULL DEFAULT '',
    responsable      TEXT    NOT NULL DEFAULT '',
    fecha            TEXT    NOT NULL
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
CREATE INDEX IF NOT EXISTS idx_mov_producto ON movimientos(producto_id);
CREATE INDEX IF NOT EXISTS idx_mov_fecha ON movimientos(fecha);
"""


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
    get_db().executescript(ESQUEMA)


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


def registrar_movimiento(db, producto_id, tipo, cantidad, motivo="", responsable=""):
    """Registra una alta (entrada) o baja (salida) y actualiza la existencia.

    Devuelve la existencia resultante. Lanza ErrorInventario si la operación no es válida.
    """
    if tipo not in ("alta", "baja"):
        raise ErrorInventario("Tipo de movimiento no válido.")
    if cantidad is None or cantidad <= 0:
        raise ErrorInventario("La cantidad debe ser mayor que cero.")

    producto = db.execute("SELECT * FROM productos WHERE id = ?", (producto_id,)).fetchone()
    if producto is None:
        raise ErrorInventario("El producto no existe.")
    if not producto["activo"]:
        raise ErrorInventario("El producto está desactivado; reactívalo para moverlo.")

    if tipo == "alta":
        nuevo = producto["stock"] + cantidad
    else:
        if cantidad > producto["stock"]:
            raise ErrorInventario(
                f"No hay existencia suficiente: hay {producto['stock']} "
                f"{producto['unidad']} y se intentó dar de baja {cantidad}."
            )
        nuevo = producto["stock"] - cantidad

    db.execute("UPDATE productos SET stock = ? WHERE id = ?", (nuevo, producto_id))
    db.execute(
        """INSERT INTO movimientos (producto_id, tipo, cantidad, stock_resultante,
                                    motivo, responsable, fecha)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (producto_id, tipo, cantidad, nuevo, motivo.strip(), responsable.strip(), ahora()),
    )
    return nuevo


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
