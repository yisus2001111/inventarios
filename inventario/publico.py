"""Páginas para alumnos (sin cuenta): llenar la solicitud de material y ver su estado."""

from flask import (Blueprint, abort, flash, redirect, render_template, request, session,
                   url_for)

from . import vales
from .db import ErrorInventario, get_db

bp = Blueprint("publico", __name__, url_prefix="/solicitud")

# Cuántas solicitudes en espera puede tener a la vez un mismo celular (evita abusos).
MAX_EN_ESPERA = 3
RECORDAR = 10  # solicitudes recientes que se muestran en «Mis solicitudes»


def mis_tokens():
    return session.get("mis_vales", [])


def mis_solicitudes(db):
    tokens = mis_tokens()
    if not tokens:
        return []
    filas = db.execute(
        f"SELECT id, token, estado, practica, fecha FROM vales "
        f"WHERE token IN ({', '.join('?' * len(tokens))}) ORDER BY id DESC",
        tokens,
    ).fetchall()
    return filas


@bp.route("/", methods=["GET", "POST"])
def solicitud():
    db = get_db()
    form = request.form
    if request.method == "POST":
        datos = {c: form.get(c, "").strip() for c in vales.CAMPOS}
        renglones = list(zip(form.getlist("material"), form.getlist("cantidad")))
        en_espera = sum(1 for s in mis_solicitudes(db) if s["estado"] == "solicitado")
        try:
            if en_espera >= MAX_EN_ESPERA:
                raise ErrorInventario(
                    f"Ya tienes {en_espera} solicitudes en espera. Pasa al mostrador a que "
                    "te las entreguen antes de pedir más.")
            vale_id, token = vales.crear_solicitud(db, datos, renglones)
            db.commit()
        except ErrorInventario as e:
            db.rollback()
            flash(str(e), "error")
        else:
            session["mis_vales"] = ([token] + mis_tokens())[:RECORDAR]
            session.permanent = True
            # Recordar los datos del alumno para su próxima solicitud.
            session["alumno"] = {c: datos[c] for c in ("alumno", "matricula")}
            return redirect(url_for("publico.estado", token=token))

    materiales = [
        {"c": p["codigo"], "n": p["nombre"], "s": p["stock"], "u": p["unidad"]}
        for p in db.execute("SELECT codigo, nombre, stock, unidad FROM productos "
                            "WHERE activo = 1 AND stock > 0 ORDER BY nombre")
    ]
    seleccion = [{"c": vales.codigo_de(m), "q": q}
                 for m, q in zip(form.getlist("material"), form.getlist("cantidad")) if m.strip()]
    previo = session.get("alumno", {})
    return render_template(
        "solicitud.html", form=form, previo=previo, materiales=materiales,
        seleccion=seleccion, mis=mis_solicitudes(db), folio=vales.folio,
        **{k: v for k, v in vales.sugerencias(db).items() if k != "materiales"},
    )


@bp.route("/<token>")
def estado(token):
    db = get_db()
    vale = db.execute("SELECT * FROM vales WHERE token = ?", (token,)).fetchone()
    if vale is None:
        abort(404)
    items = db.execute(
        """SELECT i.*, p.codigo, p.nombre, p.unidad FROM vale_items i
           JOIN productos p ON p.id = i.producto_id WHERE i.vale_id = ? ORDER BY i.id""",
        (vale["id"],),
    ).fetchall()
    return render_template("solicitud_estado.html", vale=vale, items=items, folio=vales.folio)
