"""Usuarios, inicio de sesión y permisos por rol."""

import functools
import hmac
import secrets

from flask import (Blueprint, abort, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from .db import ahora, get_db

# Cada rol incluye los permisos de los anteriores.
ROLES = {
    "consulta": (0, "Consulta (solo ver)"),
    "operador": (1, "Operador (altas, bajas y ubicaciones)"),
    "admin": (2, "Administrador"),
}
LONGITUD_MINIMA = 6

bp = Blueprint("auth", __name__)


def nivel(rol):
    return ROLES.get(rol, (-1, ""))[0]


def puede(rol):
    """¿El usuario actual tiene al menos el rol indicado?"""
    usuario = g.get("usuario")
    return usuario is not None and nivel(usuario["rol"]) >= nivel(rol)


def requiere(rol):
    def decorador(vista):
        @functools.wraps(vista)
        def envoltura(*args, **kwargs):
            if not puede(rol):
                abort(403)
            return vista(*args, **kwargs)
        return envoltura
    return decorador


def token_csrf():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def hay_usuarios():
    return get_db().execute("SELECT 1 FROM usuarios LIMIT 1").fetchone() is not None


def antes_de_cada_peticion():
    """Carga el usuario de la sesión y protege todas las páginas."""
    from flask import current_app

    g.usuario = None
    uid = session.get("uid")
    if uid is not None:
        g.usuario = get_db().execute(
            "SELECT * FROM usuarios WHERE id = ? AND activo = 1", (uid,)
        ).fetchone()
        if g.usuario is None:
            session.clear()

    if request.method == "POST" and current_app.config.get("CSRF_ENABLED", True):
        enviado = request.form.get("csrf", "")
        esperado = session.get("csrf", "")
        if not esperado or not hmac.compare_digest(enviado, esperado):
            abort(400, "La sesión expiró o el formulario no es válido. Recarga la página.")

    if request.endpoint == "static":
        return None
    if not hay_usuarios():
        if request.endpoint != "auth.configuracion_inicial":
            return redirect(url_for("auth.configuracion_inicial"))
        return None
    publica = (request.endpoint or "").startswith("publico.")
    if g.usuario is None and request.endpoint != "auth.login" and not publica:
        return redirect(url_for("auth.login", siguiente=request.full_path))
    return None


def validar_contrasena(contrasena, confirmacion):
    if len(contrasena) < LONGITUD_MINIMA:
        return f"La contraseña debe tener al menos {LONGITUD_MINIMA} caracteres."
    if contrasena != confirmacion:
        return "Las contraseñas no coinciden."
    return None


def crear_usuario(db, usuario, nombre, rol, contrasena):
    db.execute(
        """INSERT INTO usuarios (usuario, nombre, rol, contrasena, creado_en)
           VALUES (?, ?, ?, ?, ?)""",
        (usuario, nombre, rol, generate_password_hash(contrasena), ahora()),
    )


def iniciar_sesion(usuario_id):
    session.clear()
    session["uid"] = usuario_id
    session.permanent = True


# ----------------------------------------------------------------------- vistas

@bp.route("/configuracion-inicial", methods=["GET", "POST"])
def configuracion_inicial():
    """Solo disponible mientras no exista ningún usuario: crea el primer administrador."""
    if hay_usuarios():
        return redirect(url_for("index"))
    form = request.form
    if request.method == "POST":
        usuario = form.get("usuario", "").strip().lower()
        error = (validar_contrasena(form.get("contrasena", ""), form.get("confirmacion", ""))
                 if usuario else "El nombre de usuario es obligatorio.")
        if error:
            flash(error, "error")
        else:
            db = get_db()
            crear_usuario(db, usuario, form.get("nombre", "").strip(), "admin",
                          form["contrasena"])
            db.commit()
            uid = db.execute("SELECT id FROM usuarios WHERE usuario = ?", (usuario,)).fetchone()[0]
            iniciar_sesion(uid)
            flash("Administrador creado. Ya puedes dar de alta a los demás usuarios.", "ok")
            return redirect(url_for("auth.usuarios"))
    return render_template("configuracion_inicial.html", form=form)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.usuario is not None:
        return redirect(url_for("index"))
    if request.method == "POST":
        nombre = request.form.get("usuario", "").strip().lower()
        fila = get_db().execute(
            "SELECT * FROM usuarios WHERE usuario = ? AND activo = 1", (nombre,)
        ).fetchone()
        if fila and check_password_hash(fila["contrasena"], request.form.get("contrasena", "")):
            iniciar_sesion(fila["id"])
            siguiente = request.args.get("siguiente", "")
            # Solo se permite volver a una ruta interna.
            if not siguiente.startswith("/") or siguiente.startswith("//"):
                siguiente = url_for("index")
            return redirect(siguiente)
        flash("Usuario o contraseña incorrectos.", "error")
    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/mi-contrasena", methods=["GET", "POST"])
def mi_contrasena():
    if request.method == "POST":
        f = request.form
        if not check_password_hash(g.usuario["contrasena"], f.get("actual", "")):
            error = "La contraseña actual no es correcta."
        else:
            error = validar_contrasena(f.get("contrasena", ""), f.get("confirmacion", ""))
        if error:
            flash(error, "error")
        else:
            db = get_db()
            db.execute("UPDATE usuarios SET contrasena = ? WHERE id = ?",
                       (generate_password_hash(f["contrasena"]), g.usuario["id"]))
            db.commit()
            flash("Contraseña actualizada.", "ok")
            return redirect(url_for("index"))
    return render_template("mi_contrasena.html")


@bp.route("/usuarios")
@requiere("admin")
def usuarios():
    filas = get_db().execute(
        "SELECT * FROM usuarios ORDER BY activo DESC, usuario"
    ).fetchall()
    return render_template("usuarios.html", usuarios=filas, roles=ROLES)


@bp.route("/usuarios/nuevo", methods=["GET", "POST"])
@requiere("admin")
def usuario_nuevo():
    form = request.form
    if request.method == "POST":
        db = get_db()
        usuario = form.get("usuario", "").strip().lower()
        rol = form.get("rol", "")
        if not usuario:
            error = "El nombre de usuario es obligatorio."
        elif rol not in ROLES:
            error = "Selecciona un rol válido."
        elif db.execute("SELECT 1 FROM usuarios WHERE usuario = ?", (usuario,)).fetchone():
            error = f"El usuario «{usuario}» ya existe."
        else:
            error = validar_contrasena(form.get("contrasena", ""), form.get("confirmacion", ""))
        if error:
            flash(error, "error")
        else:
            crear_usuario(db, usuario, form.get("nombre", "").strip(), rol, form["contrasena"])
            db.commit()
            flash(f"Usuario «{usuario}» creado.", "ok")
            return redirect(url_for("auth.usuarios"))
    return render_template("usuario_form.html", u=None, form=form, roles=ROLES)


def admins_activos(db, excepto=None):
    return db.execute(
        "SELECT COUNT(*) FROM usuarios WHERE rol = 'admin' AND activo = 1 AND id <> ?",
        (excepto or 0,),
    ).fetchone()[0]


@bp.route("/usuarios/<int:uid>", methods=["GET", "POST"])
@requiere("admin")
def usuario_editar(uid):
    db = get_db()
    u = db.execute("SELECT * FROM usuarios WHERE id = ?", (uid,)).fetchone()
    if u is None:
        abort(404)
    form = request.form
    if request.method == "POST":
        rol = form.get("rol", "")
        activo = 1 if form.get("activo") else 0
        nueva = form.get("contrasena", "")
        error = None
        if rol not in ROLES:
            error = "Selecciona un rol válido."
        elif (u["rol"] == "admin" and (rol != "admin" or not activo)
              and admins_activos(db, excepto=uid) == 0):
            error = "Debe quedar al menos un administrador activo."
        elif nueva:
            error = validar_contrasena(nueva, form.get("confirmacion", ""))
        if error:
            flash(error, "error")
        else:
            db.execute("UPDATE usuarios SET nombre = ?, rol = ?, activo = ? WHERE id = ?",
                       (form.get("nombre", "").strip(), rol, activo, uid))
            if nueva:
                db.execute("UPDATE usuarios SET contrasena = ? WHERE id = ?",
                           (generate_password_hash(nueva), uid))
            db.commit()
            flash("Usuario actualizado.", "ok")
            return redirect(url_for("auth.usuarios"))
    return render_template("usuario_form.html", u=u, form=form, roles=ROLES)
