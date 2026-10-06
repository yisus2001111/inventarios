"""Vales de material para prácticas: préstamo a alumnos y devolución."""

import re
import secrets
import socket

import qrcode
import qrcode.image.svg
from flask import (Blueprint, Response, abort, flash, g, jsonify, redirect, render_template,
                   request, url_for)
from markupsafe import Markup

from . import db as base
from . import evidencias, red
from .auth import requiere
from .db import ErrorInventario, ahora, get_db
from .utiles import entero, respuesta_csv

POR_PAGINA = 50
ESTADOS = {"solicitado": "Por entregar", "abierto": "Entregado, pendiente de devolver",
           "cerrado": "Cerrado", "rechazado": "Rechazado"}
CAMPOS = ("alumno", "matricula", "materia", "maestro", "practica", "observaciones",
          "regreso_estimado")
# El campo «alumno» guarda el nombre de quien pide, sea alumno o empleado.
OBLIGATORIOS = {
    "alumno": {"alumno": "nombre del alumno", "materia": "materia",
               "maestro": "nombre del maestro", "practica": "nombre de la práctica"},
    "empleado": {"alumno": "nombre del empleado", "practica": "materia o trabajo a realizar"},
}
SOLICITANTES = ("alumno", "empleado")


def motivo_de(vale):
    """Texto que acompaña al vale en el historial de movimientos."""
    if vale["solicitante"] == "empleado":
        return f"{vale['alumno']} (empleado) · {vale['practica']}"
    return f"{vale['alumno']} · {vale['practica']}"

bp = Blueprint("vales", __name__, url_prefix="/vales")


def folio(vale_id):
    return f"V-{vale_id:05d}"


def codigo_de(texto):
    """El campo de material admite «CODIGO — Nombre» (autocompletado) o solo el código."""
    return (texto or "").split(" — ")[0].strip()


def pendientes_por_producto(db, producto_id):
    """Unidades de un producto que están prestadas en vales abiertos."""
    return db.execute(
        """SELECT COALESCE(SUM(i.cantidad - i.devuelto), 0) FROM vale_items i
           JOIN vales v ON v.id = i.vale_id
           WHERE v.estado = 'abierto' AND i.producto_id = ?""",
        (producto_id,),
    ).fetchone()[0]


LARGO_MAXIMO = 150      # caracteres por campo de texto
MAX_RENGLONES = 30


def validar(db, datos, renglones, solo_disponible=False, solicitante="alumno"):
    """Revisa los datos del vale. Devuelve [(producto, cantidad), ...] sin repetir productos.

    `renglones` es una lista de (texto_material, cantidad); los renglones vacíos se ignoran.
    Con `solo_disponible` se rechaza material desactivado o sin existencia suficiente
    (se usa en las solicitudes de alumnos, que todavía no descuentan inventario).
    """
    faltan = [texto for campo, texto in OBLIGATORIOS[solicitante].items()
              if not datos.get(campo)]
    if faltan:
        raise ErrorInventario("Falta: " + ", ".join(faltan) + ".")
    if datos.get("regreso_estimado") and not re.fullmatch(r"\d{2}:\d{2}",
                                                            datos["regreso_estimado"]):
        raise ErrorInventario("La hora estimada de regreso no es válida.")
    if any(len(datos.get(c, "")) > LARGO_MAXIMO for c in CAMPOS):
        raise ErrorInventario(f"Los campos de texto admiten máximo {LARGO_MAXIMO} caracteres.")

    productos, cantidades = {}, {}  # producto_id -> fila / cantidad (suma repetidos)
    for texto, cantidad in renglones:
        if not (texto or "").strip():
            continue  # renglón vacío
        codigo = codigo_de(texto)
        producto = db.execute("SELECT * FROM productos WHERE codigo = ?", (codigo,)).fetchone()
        if producto is None:
            raise ErrorInventario(f"No existe material con número de inventario «{codigo}».")
        n = entero(cantidad)
        if n is None or n <= 0:
            raise ErrorInventario(f"La cantidad de «{producto['nombre']}» debe ser mayor que cero.")
        productos[producto["id"]] = producto
        cantidades[producto["id"]] = cantidades.get(producto["id"], 0) + n
    if not cantidades:
        raise ErrorInventario("Agrega al menos un material al vale.")
    if len(cantidades) > MAX_RENGLONES:
        raise ErrorInventario(f"Un vale admite máximo {MAX_RENGLONES} materiales distintos.")
    if solo_disponible:
        for pid, n in cantidades.items():
            p = productos[pid]
            if not p["activo"] or p["stock"] <= 0:
                raise ErrorInventario(f"«{p['nombre']}» no está disponible por ahora.")
            if n > p["stock"]:
                raise ErrorInventario(f"De «{p['nombre']}» solo hay {p['stock']} disponibles.")
    return [(productos[pid], n) for pid, n in cantidades.items()]


def insertar(db, datos, renglones, **columnas):
    nombres = list(CAMPOS) + list(columnas)
    cur = db.execute(
        f"INSERT INTO vales ({', '.join(nombres)}) VALUES ({', '.join('?' * len(nombres))})",
        [datos.get(c, "") for c in CAMPOS] + list(columnas.values()),
    )
    for producto, n in renglones:
        db.execute("INSERT INTO vale_items (vale_id, producto_id, cantidad) VALUES (?, ?, ?)",
                   (cur.lastrowid, producto["id"], n))
    return cur.lastrowid


def descontar(db, vale_id, usuario):
    """Saca del inventario el material del vale (al entregarlo)."""
    vale = db.execute("SELECT * FROM vales WHERE id = ?", (vale_id,)).fetchone()
    motivo = motivo_de(vale)
    for item in db.execute("SELECT producto_id, cantidad FROM vale_items WHERE vale_id = ? "
                           "ORDER BY id", (vale_id,)).fetchall():
        base.registrar_movimiento(db, item["producto_id"], "vale", item["cantidad"], motivo,
                                  usuario, vale_id=vale_id)


def crear_vale(db, datos, renglones, usuario, archivos=None, solicitante="alumno"):
    """Vale capturado en el mostrador: se entrega y descuenta en el momento.

    Todo ocurre en una sola transacción: si algún renglón no es válido no se registra nada.
    """
    renglones = validar(db, datos, renglones, solicitante=solicitante)
    fecha = ahora()
    vale_id = insertar(db, datos, renglones, fecha=fecha, usuario=usuario, estado="abierto",
                       origen="mostrador", entregado_en=fecha, entregado_por=usuario,
                       solicitante=solicitante)
    evidencias.guardar(db, vale_id, archivos or {})
    descontar(db, vale_id, usuario)
    return vale_id


def crear_solicitud(db, datos, renglones, archivos=None, solicitante="alumno"):
    """Vale llenado por el alumno: queda en espera y no toca el inventario.

    Devuelve (vale_id, token). El token es la clave secreta con la que el alumno
    consulta el estado de su solicitud.
    """
    renglones = validar(db, datos, renglones, solo_disponible=True, solicitante=solicitante)
    token = secrets.token_urlsafe(16)
    vale_id = insertar(db, datos, renglones, fecha=ahora(), estado="solicitado",
                       origen="alumno", token=token, solicitante=solicitante)
    evidencias.guardar(db, vale_id, archivos or {})
    return vale_id, token


def entregar(db, vale, cantidades, usuario):
    """El encargado entrega una solicitud, pudiendo ajustar cantidades (0 = no se entrega)."""
    if vale["estado"] != "solicitado":
        raise ErrorInventario("Esta solicitud ya fue atendida.")
    items = db.execute("SELECT id, cantidad FROM vale_items WHERE vale_id = ?",
                       (vale["id"],)).fetchall()
    finales = {i["id"]: cantidades.get(i["id"], i["cantidad"]) for i in items}
    if any(n is None or n < 0 for n in finales.values()):
        raise ErrorInventario("Las cantidades a entregar deben ser números enteros no negativos.")
    if not any(finales.values()):
        raise ErrorInventario("No se entregaría nada; si no procede, rechaza la solicitud.")
    for item_id, n in finales.items():
        if n == 0:
            db.execute("DELETE FROM vale_items WHERE id = ?", (item_id,))
        else:
            db.execute("UPDATE vale_items SET cantidad = ? WHERE id = ?", (n, item_id))
    db.execute("UPDATE vales SET estado = 'abierto', usuario = ?, entregado_en = ?, "
               "entregado_por = ? WHERE id = ?", (usuario, ahora(), usuario, vale["id"]))
    descontar(db, vale["id"], usuario)


def rechazar(db, vale, motivo, usuario):
    if vale["estado"] != "solicitado":
        raise ErrorInventario("Esta solicitud ya fue atendida.")
    db.execute("UPDATE vales SET estado = 'rechazado', motivo_rechazo = ?, cerrado_en = ?, "
               "cerrado_por = ?, usuario = ? WHERE id = ?",
               (motivo.strip()[:LARGO_MAXIMO], ahora(), usuario, usuario, vale["id"]))


def devolver(db, vale, devoluciones, usuario):
    """Registra la devolución de material. `devoluciones`: {item_id: cantidad}.

    Devuelve el total de piezas devueltas. Si ya no queda nada pendiente, cierra el vale.
    """
    if vale["estado"] != "abierto":
        raise ErrorInventario("Este vale no tiene material pendiente de devolver.")
    items = {i["id"]: i for i in db.execute(
        """SELECT i.*, p.nombre FROM vale_items i JOIN productos p ON p.id = i.producto_id
           WHERE i.vale_id = ?""", (vale["id"],))}
    total = 0
    for item_id, cantidad in devoluciones.items():
        item = items.get(item_id)
        if item is None or not cantidad:
            continue
        pendiente = item["cantidad"] - item["devuelto"]
        if cantidad < 0 or cantidad > pendiente:
            raise ErrorInventario(
                f"De «{item['nombre']}» quedan {pendiente} por devolver; no se pueden devolver {cantidad}."
            )
        db.execute("UPDATE vale_items SET devuelto = devuelto + ? WHERE id = ?", (cantidad, item_id))
        base.registrar_movimiento(db, item["producto_id"], "devolucion", cantidad,
                                  motivo_de(vale), usuario,
                                  vale_id=vale["id"])
        total += cantidad
    queda = db.execute("SELECT COALESCE(SUM(cantidad - devuelto), 0) FROM vale_items "
                       "WHERE vale_id = ?", (vale["id"],)).fetchone()[0]
    if queda == 0:
        cerrar(db, vale["id"], usuario)
    return total


def eliminar_vale(db, vale_id):
    """Borra un vale por completo (para limpiar pruebas) y deshace su efecto en el inventario.

    Lo que el vale tenga fuera (entregado y no devuelto, incluido lo consumido) regresa a la
    existencia; se borran también sus movimientos del historial.
    """
    vale = db.execute("SELECT estado FROM vales WHERE id = ?", (vale_id,)).fetchone()
    if vale is None:
        raise ErrorInventario("El vale no existe.")
    if vale["estado"] in ("abierto", "cerrado"):
        for item in db.execute("SELECT producto_id, cantidad, devuelto FROM vale_items "
                               "WHERE vale_id = ?", (vale_id,)).fetchall():
            db.execute("UPDATE productos SET stock = stock + ? WHERE id = ?",
                       (item["cantidad"] - item["devuelto"], item["producto_id"]))
    db.execute("DELETE FROM movimientos WHERE vale_id = ?", (vale_id,))
    db.execute("DELETE FROM vale_archivos WHERE vale_id = ?", (vale_id,))
    db.execute("DELETE FROM vale_items WHERE vale_id = ?", (vale_id,))
    db.execute("DELETE FROM vales WHERE id = ?", (vale_id,))


def hora_entrada(db, vale):
    """Hora en que regresó todo el material; vacío si no ha regresado o no regresó completo."""
    if vale["estado"] != "cerrado" or not vale["cerrado_en"]:
        return ""
    falta = db.execute("SELECT COALESCE(SUM(cantidad - devuelto), 0) FROM vale_items "
                       "WHERE vale_id = ?", (vale["id"],)).fetchone()[0]
    return "" if falta else vale["cerrado_en"]


def cerrar(db, vale_id, usuario):
    db.execute("UPDATE vales SET estado = 'cerrado', cerrado_en = ?, cerrado_por = ? "
               "WHERE id = ?", (ahora(), usuario, vale_id))


# ----------------------------------------------------------------------- vistas

def obtener_vale(vale_id):
    vale = get_db().execute("SELECT * FROM vales WHERE id = ?", (vale_id,)).fetchone()
    if vale is None:
        abort(404)
    return vale


def consulta_vales():
    estado = request.args.get("estado", "pendientes")
    q = request.args.get("q", "").strip()
    desde = request.args.get("desde", "")
    hasta = request.args.get("hasta", "")
    solicitante = request.args.get("solicitante", "")
    condiciones, params = [], []
    if solicitante in SOLICITANTES:
        condiciones.append("v.solicitante = ?")
        params.append(solicitante)
    if estado == "pendientes":
        condiciones.append("v.estado IN ('solicitado', 'abierto')")
    elif estado in ESTADOS:
        condiciones.append("v.estado = ?")
        params.append(estado)
    if q:
        condiciones.append(
            """(v.alumno LIKE ? OR v.matricula LIKE ? OR v.materia LIKE ? OR v.maestro LIKE ?
                OR v.practica LIKE ? OR EXISTS (SELECT 1 FROM vale_items i
                    JOIN productos p ON p.id = i.producto_id
                    WHERE i.vale_id = v.id AND (p.codigo LIKE ? OR p.nombre LIKE ?)))""")
        params += [f"%{q}%"] * 7
        if q.upper().startswith("V-") and entero(q[2:]) is not None:
            condiciones[-1] = f"({condiciones[-1]} OR v.id = ?)"
            params.append(entero(q[2:]))
    if desde:
        condiciones.append("v.fecha >= ?")
        params.append(desde)
    if hasta:
        condiciones.append("v.fecha <= ?")
        params.append(hasta + " 23:59:59")
    where = ("WHERE " + " AND ".join(condiciones)) if condiciones else ""
    return where, params, dict(estado=estado, q=q, desde=desde, hasta=hasta,
                               solicitante=solicitante)


@bp.route("/")
def lista():
    db = get_db()
    where, params, filtros = consulta_vales()
    pagina = max(entero(request.args.get("pagina"), 1), 1)
    total = db.execute(f"SELECT COUNT(*) FROM vales v {where}", params).fetchone()[0]
    paginas = max((total + POR_PAGINA - 1) // POR_PAGINA, 1)
    pagina = min(pagina, paginas)
    vales = db.execute(
        f"""SELECT v.*,
                   (SELECT GROUP_CONCAT(i.cantidad || ' × ' || p.nombre, ', ')
                      FROM vale_items i JOIN productos p ON p.id = i.producto_id
                     WHERE i.vale_id = v.id) AS materiales,
                   (SELECT COALESCE(SUM(i.cantidad - i.devuelto), 0)
                      FROM vale_items i WHERE i.vale_id = v.id) AS pendiente
            FROM vales v {where}
            ORDER BY v.estado = 'solicitado' DESC, v.id DESC LIMIT ? OFFSET ?""",
        params + [POR_PAGINA, (pagina - 1) * POR_PAGINA],
    ).fetchall()
    return render_template("vales.html", vales=vales, total=total, pagina=pagina,
                           paginas=paginas, folio=folio, **filtros)


def sugerencias(db):
    """Valores usados antes, para autocompletar el formulario."""
    def distintos(campo):
        return [r[0] for r in db.execute(
            f"SELECT {campo} FROM vales GROUP BY {campo} ORDER BY MAX(id) DESC LIMIT 300")]
    return {
        "materias": distintos("materia"),
        "maestros": distintos("maestro"),
        "practicas": distintos("practica"),
        "materiales": db.execute(
            "SELECT codigo, nombre, stock, unidad FROM productos WHERE activo = 1 ORDER BY nombre"
        ).fetchall(),
    }


@bp.route("/nuevo", methods=["GET", "POST"])
@requiere("operador")
def nuevo():
    db = get_db()
    form = request.form
    renglones = list(zip(form.getlist("material"), form.getlist("cantidad")))
    solicitante = request.values.get("solicitante")
    solicitante = solicitante if solicitante in SOLICITANTES else "alumno"
    if request.method == "POST":
        datos = {c: form.get(c, "").strip() for c in CAMPOS}
        try:
            # En el mostrador el encargado ve la credencial, así que la foto es opcional;
            # a los empleados solo se les pide la firma.
            archivos = evidencias.recoger(db, form, request.files, exigir=("firma",),
                                          solicitante=solicitante)
            vale_id = crear_vale(db, datos, renglones, g.usuario["usuario"], archivos,
                                 solicitante=solicitante)
            db.commit()
        except ErrorInventario as e:
            db.rollback()
            flash(str(e), "error")
        else:
            flash(f"Vale {folio(vale_id)} registrado. El material se descontó del inventario.", "ok")
            return redirect(url_for("vales.detalle", vale_id=vale_id))
    renglones = [r for r in renglones if r[0].strip()] or [("", "1")]
    return render_template("vale_form.html", form=form, renglones=renglones,
                           solicitante=solicitante,
                           pedir=evidencias.requeridos(db, solicitante), **sugerencias(db))


@bp.route("/<int:vale_id>")
def detalle(vale_id):
    vale = obtener_vale(vale_id)
    items = get_db().execute(
        """SELECT i.*, p.codigo, p.nombre, p.unidad, p.stock FROM vale_items i
           JOIN productos p ON p.id = i.producto_id WHERE i.vale_id = ? ORDER BY i.id""",
        (vale_id,),
    ).fetchall()
    return render_template("vale.html", vale=vale, items=items, folio=folio, estados=ESTADOS,
                           evidencias=evidencias.de_vale(get_db(), vale_id),
                           hora_entrada=hora_entrada(get_db(), vale))


@bp.route("/<int:vale_id>/<any(firma, credencial):tipo>")
def archivo(vale_id, tipo):
    """Imagen de la firma o credencial; solo para el personal con sesión iniciada."""
    fila = get_db().execute("SELECT mime, datos FROM vale_archivos WHERE vale_id = ? "
                            "AND tipo = ? ORDER BY id DESC", (vale_id, tipo)).fetchone()
    if fila is None:
        abort(404)
    respuesta = Response(fila["datos"], mimetype=fila["mime"])
    respuesta.headers["X-Content-Type-Options"] = "nosniff"
    respuesta.headers["Cache-Control"] = "private, max-age=3600"
    return respuesta


@bp.route("/<int:vale_id>/entregar", methods=["POST"])
@requiere("operador")
def entregar_vale(vale_id):
    db = get_db()
    vale = obtener_vale(vale_id)
    cantidades = {}
    for clave, valor in request.form.items():
        if clave.startswith("entregar_"):
            cantidades[entero(clave[len("entregar_"):])] = entero(valor or 0)
    try:
        entregar(db, vale, cantidades, g.usuario["usuario"])
        db.commit()
    except ErrorInventario as e:
        db.rollback()
        flash(str(e), "error")
    else:
        flash(f"Material entregado. El vale {folio(vale_id)} queda pendiente de devolver.", "ok")
    return redirect(url_for("vales.detalle", vale_id=vale_id))


@bp.route("/<int:vale_id>/rechazar", methods=["POST"])
@requiere("operador")
def rechazar_vale(vale_id):
    db = get_db()
    try:
        rechazar(db, obtener_vale(vale_id), request.form.get("motivo", ""), g.usuario["usuario"])
        db.commit()
    except ErrorInventario as e:
        flash(str(e), "error")
    else:
        flash("Solicitud rechazada. El alumno lo verá en su celular.", "ok")
    return redirect(url_for("vales.detalle", vale_id=vale_id))


@bp.route("/<int:vale_id>/eliminar", methods=["POST"])
@requiere("admin")
def eliminar(vale_id):
    db = get_db()
    obtener_vale(vale_id)
    eliminar_vale(db, vale_id)
    db.commit()
    flash(f"Vale {folio(vale_id)} eliminado; su material se regresó a la existencia.", "ok")
    return redirect(url_for("vales.lista", estado="todos"))


@bp.route("/solicitudes.json")
@requiere("operador")
def contador_solicitudes():
    n = get_db().execute("SELECT COUNT(*) FROM vales WHERE estado = 'solicitado'").fetchone()[0]
    return jsonify(solicitudes=n)


def url_publica():
    """Dirección de la página para alumnos, tal como la deben abrir desde su celular.

    Si en Configuración hay una dirección pública (por ejemplo la del túnel de Cloudflare)
    se usa esa. Si no, y el encargado entra como «localhost», esa dirección no sirve en
    otro equipo, así que se sustituye por la IP de esta computadora en la red local.
    """
    publica = base.leer_ajuste(get_db(), "direccion_publica")
    if publica:
        return publica + url_for("publico.solicitud")
    url = red.direccion_actual() + url_for("publico.solicitud")
    host = request.host.split(":")[0]
    if host in ("localhost", "127.0.0.1", "::1"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("10.255.255.255", 1))  # no envía nada; solo elige la interfaz
                ip = s.getsockname()[0]
            url = url.replace(request.host.split(":")[0], ip, 1)
        except OSError:
            pass
    return url


@bp.route("/qr")
@requiere("operador")
def qr():
    url = url_publica()
    imagen = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=12)
    return render_template("qr.html", url=url, svg=Markup(imagen.to_string(encoding="unicode")))


@bp.route("/<int:vale_id>/devolucion", methods=["POST"])
@requiere("operador")
def devolucion(vale_id):
    db = get_db()
    vale = obtener_vale(vale_id)
    devoluciones = {}
    for clave, valor in request.form.items():
        if clave.startswith("devolver_"):
            n = entero(valor or 0)
            if n is None:
                flash("Las cantidades a devolver deben ser números enteros.", "error")
                return redirect(url_for("vales.detalle", vale_id=vale_id))
            devoluciones[entero(clave[len("devolver_"):])] = n
    try:
        total = devolver(db, vale, devoluciones, g.usuario["usuario"])
        db.commit()
    except ErrorInventario as e:
        db.rollback()
        flash(str(e), "error")
    else:
        if total == 0:
            flash("No se indicó ninguna cantidad a devolver.", "error")
        else:
            cerrado = obtener_vale(vale_id)["estado"] == "cerrado"
            flash(f"Devolución registrada ({total} pza)."
                  + (" Se devolvió todo; el vale quedó cerrado." if cerrado else ""), "ok")
    return redirect(url_for("vales.detalle", vale_id=vale_id))


@bp.route("/<int:vale_id>/cerrar", methods=["POST"])
@requiere("operador")
def cerrar_vale(vale_id):
    db = get_db()
    vale = obtener_vale(vale_id)
    if vale["estado"] != "abierto":
        flash("Solo se puede cerrar un vale entregado y pendiente de devolver.", "error")
    else:
        cerrar(db, vale_id, g.usuario["usuario"])
        db.commit()
        flash("Vale cerrado. Lo que no se devolvió queda como consumido.", "ok")
    return redirect(url_for("vales.detalle", vale_id=vale_id))


@bp.route("/exportar.csv")
def exportar():
    where, params, _ = consulta_vales()
    filas = get_db().execute(
        f"""SELECT v.*, p.codigo, p.nombre AS material, i.cantidad, i.devuelto
            FROM vales v JOIN vale_items i ON i.vale_id = v.id
            JOIN productos p ON p.id = i.producto_id {where} ORDER BY v.id, i.id""",
        params,
    ).fetchall()
    return respuesta_csv(
        "vales",
        ["folio", "fecha", "solicitante", "nombre", "matricula", "materia", "maestro", "practica",
         "numero_inventario", "material", "cantidad", "devuelto", "no_devuelto", "estado",
         "origen", "hora_salida", "entregado_por", "hora_entrada", "regreso_estimado",
         "observaciones", "motivo_rechazo"],
        [(folio(f["id"]), f["fecha"], f["solicitante"], f["alumno"], f["matricula"],
          f["materia"], f["maestro"],
          f["practica"], f["codigo"], f["material"], f["cantidad"], f["devuelto"],
          f["cantidad"] - f["devuelto"], ESTADOS[f["estado"]], f["origen"],
          f["entregado_en"] or "", f["entregado_por"] or "", hora_entrada(get_db(), f),
          f["regreso_estimado"], f["observaciones"], f["motivo_rechazo"])
         for f in filas],
    )
