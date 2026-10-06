"""Firma del alumno y foto de su credencial, guardadas con cada vale."""

import base64
import binascii

from .db import ErrorInventario, ahora, leer_ajuste

TIPOS = ("firma", "credencial")
MAXIMO = {"firma": 600 * 1024, "credencial": 3 * 1024 * 1024}
NOMBRES = {"firma": "la firma", "credencial": "la foto de la credencial"}


def tipo_imagen(datos):
    """Reconoce PNG, JPG y WEBP por su firma binaria."""
    if datos.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if datos.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if datos[:4] == b"RIFF" and datos[8:12] == b"WEBP":
        return "image/webp"
    return None


def requeridos(db, solicitante="alumno"):
    """Qué evidencias pide la universidad (se cambia en Configuración).

    A los empleados solo se les pide la firma.
    """
    pedidas = {t: leer_ajuste(db, f"pedir_{t}", "1") == "1" for t in TIPOS}
    if solicitante == "empleado":
        pedidas["credencial"] = False
    return pedidas


def leer(form, files, tipo):
    """Obtiene la imagen enviada en el formulario, o None si no se envió.

    El navegador manda la firma (y la foto ya reducida) como «data URL» en un campo
    oculto; si el teléfono no pudo reducir la foto, llega como archivo normal.
    """
    datos = None
    texto = (form.get(tipo) or "").strip()
    if texto.startswith("data:") and "," in texto:
        try:
            datos = base64.b64decode(texto.split(",", 1)[1], validate=True)
        except (binascii.Error, ValueError):
            raise ErrorInventario(f"No se pudo leer {NOMBRES[tipo]}; inténtalo de nuevo.")
    elif files and files.get(f"{tipo}_archivo") and files[f"{tipo}_archivo"].filename:
        datos = files[f"{tipo}_archivo"].read(MAXIMO[tipo] + 1)
    if not datos:
        return None
    if len(datos) > MAXIMO[tipo]:
        raise ErrorInventario(f"La imagen de {NOMBRES[tipo]} es demasiado grande.")
    mime = tipo_imagen(datos)
    if mime is None:
        raise ErrorInventario(f"{NOMBRES[tipo].capitalize()} debe ser una imagen (JPG o PNG).")
    return mime, datos


def recoger(db, form, files, exigir=TIPOS, solicitante="alumno"):
    """Lee las evidencias del formulario y revisa que estén las obligatorias.

    `exigir` limita cuáles se pueden exigir (en el mostrador la credencial es opcional
    porque el encargado la tiene a la vista).
    """
    pedidas = requeridos(db, solicitante)
    archivos = {}
    for tipo in TIPOS:
        if solicitante == "empleado" and tipo == "credencial":
            continue
        imagen = leer(form, files, tipo)
        if imagen:
            archivos[tipo] = imagen
        elif pedidas[tipo] and tipo in exigir:
            raise ErrorInventario(
                f"Falta la firma del {solicitante}." if tipo == "firma"
                else "Falta la foto de la credencial del alumno.")
    return archivos


def guardar(db, vale_id, archivos):
    for tipo, (mime, datos) in archivos.items():
        db.execute("INSERT INTO vale_archivos (vale_id, tipo, mime, datos, fecha) "
                   "VALUES (?, ?, ?, ?, ?)", (vale_id, tipo, mime, datos, ahora()))


def de_vale(db, vale_id):
    """Qué evidencias tiene un vale: {tipo: fecha}."""
    return {f["tipo"]: f["fecha"] for f in db.execute(
        "SELECT tipo, fecha FROM vale_archivos WHERE vale_id = ?", (vale_id,))}


def borrar_credenciales_de_cerrados(db):
    """Elimina las fotos de credencial de vales ya cerrados o rechazados (datos personales).

    Las firmas se conservan como constancia. Devuelve cuántas fotos se borraron.
    """
    cur = db.execute(
        """DELETE FROM vale_archivos WHERE tipo = 'credencial' AND vale_id IN
           (SELECT id FROM vales WHERE estado IN ('cerrado', 'rechazado'))""")
    return cur.rowcount
