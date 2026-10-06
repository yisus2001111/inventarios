"""Aplicación web de control de inventario interno."""

import csv
import io
import logging
import os
import secrets
from datetime import timedelta

from flask import (Flask, Response, abort, flash, g, redirect, render_template,
                   request, url_for)
from markupsafe import escape

from . import auth, evidencias, publico, vales
from . import db as base
from .auth import requiere
from .db import ErrorInventario, get_db
from .utiles import entero, respuesta_csv

POR_PAGINA = 50
INSTITUCION = "UES San Luis Río Colorado"
LOGO_MAXIMO = 2 * 1024 * 1024
NOMBRES_TIPO = {"alta": "Alta", "baja": "Baja", "vale": "Vale", "devolucion": "Devolución"}


def clave_secreta(ruta_db):
    """Clave para firmar las sesiones.

    Si no se define INVENTARIO_SECRET_KEY, se genera una aleatoria la primera vez y
    se guarda junto a la base de datos, para que las sesiones sobrevivan a reinicios.
    """
    if os.environ.get("INVENTARIO_SECRET_KEY"):
        return os.environ["INVENTARIO_SECRET_KEY"]
    ruta = os.path.join(os.path.dirname(os.path.abspath(ruta_db)), ".clave_secreta")
    try:
        with open(ruta, encoding="ascii") as f:
            clave = f.read().strip()
        if clave:
            return clave
    except FileNotFoundError:
        pass
    clave = secrets.token_hex(32)
    with open(ruta, "w", encoding="ascii") as f:
        f.write(clave)
    return clave


def responsable():
    return g.usuario["usuario"]


def create_app(config=None):
    app = Flask(__name__)
    app.config.update(
        DATABASE=os.environ.get(
            "INVENTARIO_DB", os.path.join(app.root_path, "..", "inventario.db")
        ),
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        MAX_CONTENT_LENGTH=12 * 1024 * 1024,  # fotos de credencial y logo
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    if config:
        app.config.update(config)
    if not app.config.get("SECRET_KEY"):
        app.config["SECRET_KEY"] = clave_secreta(app.config["DATABASE"])

    app.teardown_appcontext(base.cerrar_db)
    with app.app_context():
        base.init_db()

    app.register_blueprint(auth.bp)
    app.register_blueprint(vales.bp)
    app.register_blueprint(publico.bp)
    app.before_request(auth.antes_de_cada_peticion)

    @app.context_processor
    def utilidades_plantillas():
        return {"puede": auth.puede, "csrf_token": auth.token_csrf, "usuario": g.get("usuario"),
                "lista_ubicaciones": lista_ubicaciones,
                "solicitudes_en_espera": solicitudes_en_espera,
                "institucion": base.leer_ajuste(get_db(), "institucion", INSTITUCION),
                "version_logo": base.leer_ajuste(get_db(), "version_logo"),
                "hay_logo": base.leer_ajuste(get_db(), "logo_tipo") is not None,
                "nombres_tipo": NOMBRES_TIPO, "folio": vales.folio}

    def solicitudes_en_espera():
        return get_db().execute(
            "SELECT COUNT(*) FROM vales WHERE estado = 'solicitado'").fetchone()[0]

    def lista_ubicaciones():
        return [r[0] for r in get_db().execute(
            "SELECT DISTINCT ubicacion FROM productos WHERE ubicacion <> '' ORDER BY ubicacion"
        )]

    @app.errorhandler(400)
    def solicitud_invalida(e):
        return render_template("error.html", titulo="Solicitud no válida",
                               mensaje=e.description), 400

    # Los errores inesperados se guardan con todo su detalle en errores.log (junto a la
    # base de datos) para poder diagnosticarlos.
    ruta_log = os.path.join(os.path.dirname(os.path.abspath(app.config["DATABASE"])),
                            "errores.log")
    registro = logging.FileHandler(ruta_log, encoding="utf-8", delay=True)
    registro.setLevel(logging.ERROR)
    registro.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    app.logger.addHandler(registro)

    @app.after_request
    def encabezados_seguridad(respuesta):
        respuesta.headers.setdefault("X-Content-Type-Options", "nosniff")
        respuesta.headers.setdefault("X-Frame-Options", "DENY")
        respuesta.headers.setdefault("Referrer-Policy", "same-origin")
        return respuesta

    @app.errorhandler(500)
    def error_interno(e):
        # Página sin plantillas: debe mostrarse aunque la falla venga de una plantilla.
        original = getattr(e, "original_exception", None) or e
        detalle = f"{type(original).__name__}: {original}"
        return (f"""<!doctype html><html lang="es"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Error</title>
<body style="font-family:system-ui,sans-serif;max-width:640px;margin:40px auto;padding:0 16px">
<h1 style="color:#5d1a2c">Ocurrió un error</h1>
<p>Si acabas de actualizar el programa, <strong>ciérralo y vuelve a abrirlo</strong>
(en la ventana negra presiona <kbd>Ctrl</kbd>+<kbd>C</kbd> y luego <code>python run.py</code>).</p>
<p>Si el problema sigue, envía el archivo <code>errores.log</code> que está junto a
<code>inventario.db</code>.</p>
<p style="color:#626a73;font-size:.9em">Detalle: {escape(detalle)}</p>
<p><a href="/">Volver al inicio</a></p>""", 500)

    @app.errorhandler(413)
    def demasiado_grande(_e):
        return render_template("error.html", titulo="Archivo demasiado grande",
                               mensaje="La foto o el archivo enviado pesa demasiado. "
                                       "Intenta con una imagen más pequeña."), 413

    @app.errorhandler(403)
    def prohibido(_e):
        return render_template("error.html", titulo="Sin permiso",
                               mensaje="Tu usuario no tiene permiso para hacer esto."), 403

    # ------------------------------------------------------------------ productos

    @app.route("/")
    def index():
        db = get_db()
        q = request.args.get("q", "").strip()
        categoria = request.args.get("categoria", "")
        ubicacion = request.args.get("ubicacion", "")
        estado = request.args.get("estado", "activos")
        pagina = max(entero(request.args.get("pagina"), 1), 1)

        condiciones, params = [], []
        if q:
            condiciones.append("(codigo LIKE ? OR nombre LIKE ? OR ubicacion LIKE ?)")
            params += [f"%{q}%"] * 3
        if categoria:
            condiciones.append("categoria = ?")
            params.append(categoria)
        if ubicacion:
            condiciones.append("ubicacion = ?")
            params.append(ubicacion)
        if estado == "activos":
            condiciones.append("activo = 1")
        elif estado == "inactivos":
            condiciones.append("activo = 0")
        elif estado == "bajo":
            condiciones.append("activo = 1 AND stock <= stock_minimo")
        where = ("WHERE " + " AND ".join(condiciones)) if condiciones else ""

        total = db.execute(f"SELECT COUNT(*) FROM productos {where}", params).fetchone()[0]
        paginas = max((total + POR_PAGINA - 1) // POR_PAGINA, 1)
        pagina = min(pagina, paginas)
        productos = db.execute(
            f"SELECT * FROM productos {where} ORDER BY nombre COLLATE NOCASE "
            f"LIMIT ? OFFSET ?",
            params + [POR_PAGINA, (pagina - 1) * POR_PAGINA],
        ).fetchall()

        resumen = db.execute(
            """SELECT COUNT(*) AS productos,
                      COALESCE(SUM(stock), 0) AS unidades,
                      COALESCE(SUM(stock <= stock_minimo), 0) AS bajo_minimo
               FROM productos WHERE activo = 1"""
        ).fetchone()
        vales_abiertos = db.execute(
            "SELECT COUNT(*) FROM vales WHERE estado = 'abierto'").fetchone()[0]
        categorias = [r[0] for r in db.execute(
            "SELECT DISTINCT categoria FROM productos WHERE categoria <> '' ORDER BY categoria"
        )]
        return render_template(
            "index.html", productos=productos, resumen=resumen, categorias=categorias,
            vales_abiertos=vales_abiertos,
            q=q, categoria=categoria, ubicacion=ubicacion, estado=estado, pagina=pagina, paginas=paginas,
            total=total,
        )

    @app.route("/productos/nuevo", methods=["GET", "POST"])
    @requiere("admin")
    def producto_nuevo():
        form = request.form
        if request.method == "POST":
            db = get_db()
            try:
                stock = entero(form.get("stock_inicial") or 0)
                minimo = entero(form.get("stock_minimo") or 0)
                if stock is None or minimo is None:
                    raise ErrorInventario("Las cantidades deben ser números enteros.")
                pid = base.crear_producto(
                    db, form.get("codigo"), form.get("nombre"),
                    form.get("categoria", ""), form.get("ubicacion", ""),
                    form.get("unidad", "pza"), stock, minimo,
                    responsable=responsable(),
                )
                db.commit()
            except ErrorInventario as e:
                db.rollback()
                flash(str(e), "error")
            else:
                flash("Producto dado de alta.", "ok")
                return redirect(url_for("producto_detalle", pid=pid))
        return render_template("producto_form.html", producto=None, form=form)

    def obtener_producto(pid):
        producto = get_db().execute("SELECT * FROM productos WHERE id = ?", (pid,)).fetchone()
        if producto is None:
            abort(404)
        return producto

    @app.route("/productos/<int:pid>")
    def producto_detalle(pid):
        producto = obtener_producto(pid)
        db = get_db()
        movimientos = db.execute(
            "SELECT * FROM movimientos WHERE producto_id = ? ORDER BY id DESC LIMIT 200",
            (pid,),
        ).fetchall()
        ubicaciones = db.execute(
            "SELECT * FROM cambios_ubicacion WHERE producto_id = ? ORDER BY id DESC LIMIT 100",
            (pid,),
        ).fetchall()
        return render_template("producto.html", producto=producto, movimientos=movimientos,
                               cambios_ubicacion=ubicaciones,
                               prestado=vales.pendientes_por_producto(db, pid))

    @app.route("/productos/<int:pid>/editar", methods=["GET", "POST"])
    @requiere("admin")
    def producto_editar(pid):
        producto = obtener_producto(pid)
        form = request.form
        if request.method == "POST":
            db = get_db()
            codigo = form.get("codigo", "").strip()
            nombre = form.get("nombre", "").strip()
            minimo = entero(form.get("stock_minimo") or 0)
            error = None
            if not codigo or not nombre:
                error = "El código y el nombre son obligatorios."
            elif minimo is None or minimo < 0:
                error = "El stock mínimo debe ser un entero no negativo."
            elif db.execute("SELECT 1 FROM productos WHERE codigo = ? AND id <> ?",
                            (codigo, pid)).fetchone():
                error = f"Ya existe otro producto con el código «{codigo}»."
            if error:
                flash(error, "error")
            else:
                db.execute(
                    """UPDATE productos SET codigo=?, nombre=?, categoria=?,
                              unidad=?, stock_minimo=? WHERE id=?""",
                    (codigo, nombre, form.get("categoria", "").strip(),
                     form.get("unidad", "").strip() or "pza", minimo, pid),
                )
                base.cambiar_ubicacion(db, pid, form.get("ubicacion", ""), responsable())
                db.commit()
                flash("Cambios guardados.", "ok")
                return redirect(url_for("producto_detalle", pid=pid))
        return render_template("producto_form.html", producto=producto, form=form)

    @app.route("/productos/<int:pid>/activo", methods=["POST"])
    @requiere("admin")
    def producto_activo(pid):
        producto = obtener_producto(pid)
        activar = request.form.get("activar") == "1"
        if not activar and producto["stock"] > 0:
            flash("Para desactivar un producto primero da de baja toda su existencia.", "error")
        elif not activar and vales.pendientes_por_producto(get_db(), pid) > 0:
            flash("No se puede desactivar: hay unidades prestadas en vales sin devolver.", "error")
        else:
            db = get_db()
            db.execute("UPDATE productos SET activo = ? WHERE id = ?", (int(activar), pid))
            db.commit()
            flash("Producto reactivado." if activar else "Producto desactivado.", "ok")
        return redirect(url_for("producto_detalle", pid=pid))

    @app.route("/productos/<int:pid>/ubicacion", methods=["POST"])
    @requiere("operador")
    def producto_ubicacion(pid):
        obtener_producto(pid)
        db = get_db()
        nueva = request.form.get("ubicacion", "").strip()
        if base.cambiar_ubicacion(db, pid, nueva, responsable()):
            db.commit()
            flash(f"Ubicación cambiada a «{nueva or 'sin ubicación'}».", "ok")
        else:
            flash("La ubicación no cambió.", "error")
        return redirect(url_for("producto_detalle", pid=pid))

    @app.route("/productos/mover", methods=["POST"])
    @requiere("operador")
    def productos_mover():
        """Cambia la ubicación de varios productos seleccionados en la lista."""
        db = get_db()
        ids = [i for i in (entero(v) for v in request.form.getlist("ids")) if i is not None]
        nueva = request.form.get("ubicacion", "").strip()
        if not ids:
            flash("Marca al menos un producto para moverlo.", "error")
        elif not nueva:
            flash("Escribe la nueva ubicación.", "error")
        else:
            try:
                cambiados = sum(base.cambiar_ubicacion(db, i, nueva, responsable()) for i in ids)
            except ErrorInventario as e:
                db.rollback()
                flash(str(e), "error")
            else:
                db.commit()
                flash(f"{cambiados} producto{'' if cambiados == 1 else 's'} "
                      f"movido{'' if cambiados == 1 else 's'} a «{nueva}».", "ok")
        destino = request.form.get("volver", "")
        if not destino.startswith("/") or destino.startswith("//"):
            destino = url_for("index")
        return redirect(destino)

    # ---------------------------------------------------------------- movimientos

    def aplicar_movimiento(pid):
        db = get_db()
        if request.form.get("tipo") not in ("alta", "baja"):
            flash("Tipo de movimiento no válido.", "error")
            return False
        try:
            nuevo = base.registrar_movimiento(
                db, pid, request.form.get("tipo"), entero(request.form.get("cantidad")),
                request.form.get("motivo", ""), responsable(),
            )
            db.commit()
        except ErrorInventario as e:
            db.rollback()
            flash(str(e), "error")
            return False
        verbo = "Alta" if request.form.get("tipo") == "alta" else "Baja"
        flash(f"{verbo} registrada. Existencia actual: {nuevo}.", "ok")
        return True

    @app.route("/productos/<int:pid>/movimiento", methods=["POST"])
    @requiere("operador")
    def producto_movimiento(pid):
        obtener_producto(pid)
        aplicar_movimiento(pid)
        return redirect(url_for("producto_detalle", pid=pid))

    @app.route("/movimiento", methods=["GET", "POST"])
    @requiere("operador")
    def movimiento_rapido():
        """Alta/baja tecleando o escaneando el código del producto."""
        if request.method == "POST":
            codigo = request.form.get("codigo", "").strip()
            fila = get_db().execute(
                "SELECT id FROM productos WHERE codigo = ?", (codigo,)
            ).fetchone()
            if fila is None:
                flash(f"No existe ningún producto con el código «{codigo}».", "error")
            elif aplicar_movimiento(fila["id"]):
                return redirect(url_for("movimiento_rapido", tipo=request.form.get("tipo")))
        ultimos = get_db().execute(
            """SELECT m.*, p.codigo, p.nombre, p.unidad FROM movimientos m
               JOIN productos p ON p.id = m.producto_id ORDER BY m.id DESC LIMIT 10"""
        ).fetchall()
        return render_template("movimiento.html", ultimos=ultimos,
                               form=request.form or request.args)

    def consulta_movimientos():
        tipo = request.args.get("tipo", "")
        desde = request.args.get("desde", "")
        hasta = request.args.get("hasta", "")
        q = request.args.get("q", "").strip()
        condiciones, params = [], []
        if tipo in base.TIPOS:
            condiciones.append("m.tipo = ?")
            params.append(tipo)
        if desde:
            condiciones.append("m.fecha >= ?")
            params.append(desde)
        if hasta:
            condiciones.append("m.fecha <= ?")
            params.append(hasta + " 23:59:59")
        if q:
            condiciones.append("(p.codigo LIKE ? OR p.nombre LIKE ? OR m.responsable LIKE ?"
                               " OR m.motivo LIKE ?)")
            params += [f"%{q}%"] * 4
            if q.upper().startswith("V-") and entero(q[2:]) is not None:
                condiciones[-1] = f"({condiciones[-1]} OR m.vale_id = ?)"
                params.append(entero(q[2:]))
        where = ("WHERE " + " AND ".join(condiciones)) if condiciones else ""
        sql = f"""SELECT m.*, p.codigo, p.nombre, p.unidad FROM movimientos m
                  JOIN productos p ON p.id = m.producto_id {where}"""
        filtros = dict(tipo=tipo, desde=desde, hasta=hasta, q=q)
        return sql, params, filtros

    @app.route("/movimientos")
    def movimientos():
        db = get_db()
        sql, params, filtros = consulta_movimientos()
        pagina = max(entero(request.args.get("pagina"), 1), 1)
        total = db.execute(f"SELECT COUNT(*) FROM ({sql})", params).fetchone()[0]
        paginas = max((total + POR_PAGINA - 1) // POR_PAGINA, 1)
        pagina = min(pagina, paginas)
        filas = db.execute(f"{sql} ORDER BY m.id DESC LIMIT ? OFFSET ?",
                           params + [POR_PAGINA, (pagina - 1) * POR_PAGINA]).fetchall()
        return render_template("movimientos.html", movimientos=filas, total=total,
                               pagina=pagina, paginas=paginas, **filtros)

    # ------------------------------------------------------- exportar / importar

    @app.route("/exportar/productos.csv")
    def exportar_productos():
        campos = ["codigo", "nombre", "categoria", "ubicacion", "unidad",
                  "stock", "stock_minimo", "activo"]
        filas = get_db().execute(
            f"SELECT {', '.join(campos)} FROM productos ORDER BY codigo"
        ).fetchall()
        return respuesta_csv("productos", campos, [tuple(f) for f in filas])

    @app.route("/exportar/movimientos.csv")
    def exportar_movimientos():
        sql, params, _ = consulta_movimientos()
        filas = get_db().execute(f"{sql} ORDER BY m.id", params).fetchall()
        return respuesta_csv(
            "movimientos",
            ["fecha", "codigo", "nombre", "tipo", "vale", "cantidad", "existencia_resultante",
             "motivo", "responsable"],
            [(f["fecha"], f["codigo"], f["nombre"], NOMBRES_TIPO[f["tipo"]],
              vales.folio(f["vale_id"]) if f["vale_id"] else "", f["cantidad"],
              f["stock_resultante"], f["motivo"], f["responsable"]) for f in filas],
        )

    @app.route("/movimientos/eliminar", methods=["POST"])
    @requiere("admin")
    def eliminar_movimientos():
        db = get_db()
        ids = [i for i in (entero(v) for v in request.form.getlist("ids")) if i is not None]
        corregir = request.form.get("corregir") == "1"
        # Las líneas de un vale (préstamo o devolución) eliminan el vale completo.
        vales_ids = sorted({f[0] for f in db.execute(
            f"SELECT vale_id FROM movimientos WHERE vale_id IS NOT NULL "
            f"AND id IN ({', '.join('?' * len(ids))})", ids)}) if ids else []
        try:
            n = base.eliminar_movimientos(db, ids, corregir_existencia=corregir)
            for vale_id in vales_ids:
                vales.eliminar_vale(db, vale_id, regresar_material=corregir)
            db.commit()
        except ErrorInventario as e:
            db.rollback()
            flash(str(e) + " No se eliminó nada.", "error")
        else:
            partes = []
            if n:
                partes.append(f"{n} registro{'' if n == 1 else 's'}")
            if vales_ids:
                partes.append(("vale " if len(vales_ids) == 1 else "vales ")
                              + ", ".join(vales.folio(v) for v in vales_ids)
                              + " (su folio queda disponible)")
            if partes:
                flash("Se eliminó: " + " y ".join(partes)
                      + (". Existencia corregida." if corregir else "."), "ok")
            else:
                flash("Marca al menos un registro para eliminarlo.", "error")
        destino = request.form.get("volver", "")
        if not destino.startswith("/") or destino.startswith("//"):
            destino = url_for("movimientos")
        return redirect(destino)

    # ------------------------------------------------------------- configuración

    @app.route("/configuracion", methods=["GET", "POST"])
    @requiere("admin")
    def configuracion():
        db = get_db()
        if request.method == "POST" and request.form.get("accion") == "borrar_credenciales":
            n = evidencias.borrar_credenciales_de_cerrados(db)
            db.commit()
            flash(f"Se eliminaron {n} foto{'' if n == 1 else 's'} de credencial de vales "
                  "cerrados o rechazados.", "ok")
            return redirect(url_for("configuracion"))
        if request.method == "POST":
            for tipo in evidencias.TIPOS:
                base.guardar_ajuste(db, f"pedir_{tipo}",
                                    "1" if request.form.get(f"pedir_{tipo}") else "0")
            institucion = request.form.get("institucion", "").strip()[:120]
            base.guardar_ajuste(db, "institucion", institucion or INSTITUCION)
            archivo = request.files.get("logo")
            if request.form.get("quitar_logo"):
                db.execute("DELETE FROM ajustes WHERE clave IN ('logo', 'logo_tipo')")
            elif archivo and archivo.filename:
                datos = archivo.read(LOGO_MAXIMO + 1)
                tipo = evidencias.tipo_imagen(datos)
                if len(datos) > LOGO_MAXIMO:
                    flash("El logo debe pesar menos de 2 MB.", "error")
                    return redirect(url_for("configuracion"))
                if tipo is None:
                    flash("El logo debe ser una imagen PNG, JPG o WEBP.", "error")
                    return redirect(url_for("configuracion"))
                base.guardar_ajuste(db, "logo", datos)
                base.guardar_ajuste(db, "logo_tipo", tipo)
            base.guardar_ajuste(db, "version_logo", secrets.token_hex(4))
            db.commit()
            flash("Configuración guardada.", "ok")
            return redirect(url_for("configuracion"))
        fotos = db.execute("SELECT COUNT(*) FROM vale_archivos a JOIN vales v ON v.id = a.vale_id "
                           "WHERE a.tipo = 'credencial' AND v.estado IN ('cerrado', 'rechazado')"
                           ).fetchone()[0]
        return render_template("configuracion.html", pedir=evidencias.requeridos(db),
                               fotos_cerradas=fotos)

    @app.route("/logo")
    def logo():
        db = get_db()
        datos = base.leer_ajuste(db, "logo")
        if not datos:
            abort(404)
        respuesta = Response(datos, mimetype=base.leer_ajuste(db, "logo_tipo"))
        respuesta.headers["X-Content-Type-Options"] = "nosniff"
        respuesta.headers["Cache-Control"] = "public, max-age=86400"
        return respuesta

    @app.route("/importar", methods=["GET", "POST"])
    @requiere("admin")
    def importar():
        if request.method == "GET":
            return render_template("importar.html", resultado=None)

        archivo = request.files.get("archivo")
        if not archivo or not archivo.filename:
            flash("Selecciona un archivo CSV.", "error")
            return render_template("importar.html", resultado=None)

        datos = archivo.read()
        try:
            texto = datos.decode("utf-8-sig")
        except UnicodeDecodeError:
            texto = datos.decode("latin-1")
        try:
            dialecto = csv.Sniffer().sniff(texto[:4096], delimiters=",;\t")
        except csv.Error:
            dialecto = csv.excel
        lector = csv.DictReader(io.StringIO(texto), dialect=dialecto)
        lector.fieldnames = [(c or "").strip().lower() for c in (lector.fieldnames or [])]
        columnas = set(lector.fieldnames)
        if "codigo" not in columnas:
            flash("El archivo debe tener al menos la columna «codigo».", "error")
            return render_template("importar.html", resultado=None)
        editables = [c for c in ("nombre", "categoria", "unidad", "stock_minimo")
                     if c in columnas]

        db = get_db()
        resultado = {"creados": 0, "actualizados": 0, "errores": []}
        for n, fila in enumerate(lector, start=2):
            fila = {k: (v or "").strip() for k, v in fila.items() if k}
            if not any(fila.values()):
                continue
            stock = entero(fila.get("stock") or 0)
            minimo = entero(fila.get("stock_minimo") or 0)
            if stock is None or minimo is None:
                resultado["errores"].append(f"Fila {n}: cantidades no numéricas.")
                continue
            existente = db.execute("SELECT id FROM productos WHERE codigo = ?",
                                   (fila.get("codigo", ""),)).fetchone()
            try:
                if existente:
                    # Solo se actualizan los datos descriptivos de las columnas que
                    # trae el archivo; la existencia cambia únicamente con altas/bajas.
                    if "nombre" in columnas and not fila.get("nombre"):
                        raise ErrorInventario("el nombre no puede quedar vacío.")
                    valores = {"nombre": fila.get("nombre"), "categoria": fila.get("categoria"),
                               "unidad": fila.get("unidad") or "pza", "stock_minimo": minimo}
                    if editables:
                        db.execute(
                            f"UPDATE productos SET {', '.join(c + ' = ?' for c in editables)}"
                            f" WHERE id = ?",
                            [valores[c] for c in editables] + [existente["id"]],
                        )
                    if "ubicacion" in columnas:
                        base.cambiar_ubicacion(db, existente["id"], fila.get("ubicacion"),
                                               responsable())
                    resultado["actualizados"] += 1
                else:
                    base.crear_producto(
                        db, fila.get("codigo"), fila.get("nombre"), fila.get("categoria", ""),
                        fila.get("ubicacion", ""), fila.get("unidad") or "pza", stock, minimo,
                        responsable=responsable(), motivo="Importación inicial",
                    )
                    resultado["creados"] += 1
            except ErrorInventario as e:
                resultado["errores"].append(f"Fila {n}: {e}")
        db.commit()
        return render_template("importar.html", resultado=resultado)

    @app.route("/plantilla.csv")
    @requiere("admin")
    def plantilla():
        return respuesta_csv(
            "plantilla_inventario",
            ["codigo", "nombre", "categoria", "ubicacion", "unidad", "stock", "stock_minimo"],
            [("A-001", "Tóner HP 85A", "Consumibles", "Almacén 1 / Estante B", "pza", 12, 3)],
        )

    return app
