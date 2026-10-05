"""Funciones auxiliares compartidas."""

import csv
import io
from datetime import datetime

from flask import Response


def entero(valor, defecto=None):
    try:
        return int(str(valor).strip())
    except (TypeError, ValueError):
        return defecto


def respuesta_csv(nombre, encabezados, filas):
    salida = io.StringIO()
    salida.write("﻿")  # BOM para que Excel respete los acentos
    writer = csv.writer(salida)
    writer.writerow(encabezados)
    writer.writerows(filas)
    sello = datetime.now().strftime("%Y%m%d")
    return Response(
        salida.getvalue(), mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={nombre}_{sello}.csv"},
    )
