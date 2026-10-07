"""Restablece la contraseña de un usuario cuando nadie puede entrar al programa.

Se ejecuta en la computadora donde está instalado el inventario (quien tiene acceso a
ella es quien puede hacerlo):

    python restablecer_contrasena.py

Muestra los usuarios, pide cuál y la contraseña nueva. Además reactiva la cuenta y
quita el bloqueo por intentos fallidos. Con la opción --admin la cuenta queda como
administrador (útil si ya no queda ningún administrador activo).

Al escribir la contraseña no se ve nada en pantalla (es normal). Con --visible sí se ve,
por si la ventana no deja escribir de forma oculta.
"""

import getpass
import sys

from werkzeug.security import generate_password_hash

from inventario import create_app
from inventario.auth import LONGITUD_MINIMA, ROLES, validar_contrasena
from inventario.db import get_db


def main(argv):
    hacer_admin = "--admin" in argv
    if "--visible" in argv:
        pedir = input
        aviso = "se verá al escribir"
    else:
        pedir = getpass.getpass
        aviso = "no se ve al escribir; escríbela y presiona Enter"
    app = create_app()
    with app.app_context():
        db = get_db()
        usuarios = db.execute("SELECT * FROM usuarios ORDER BY usuario").fetchall()
        if not usuarios:
            print("Todavía no hay usuarios. Abre el programa en el navegador para crear "
                  "el administrador.")
            return 1

        print("Usuarios registrados:\n")
        for u in usuarios:
            estado = "" if u["activo"] else "  (desactivado)"
            print(f"  - {u['usuario']:<20} {ROLES[u['rol']][1]}{estado}")
        print()
        nombre = input("Usuario al que le quieres poner contraseña nueva: ").strip().lower()
        usuario = next((u for u in usuarios if u["usuario"] == nombre), None)
        if usuario is None:
            print(f"No existe el usuario «{nombre}».")
            return 1

        while True:
            nueva = pedir(f"Contraseña nueva (mínimo {LONGITUD_MINIMA} caracteres, {aviso}): ")
            error = validar_contrasena(nueva, pedir("Repítela: "))
            if not error:
                break
            print(error)

        rol = "admin" if hacer_admin else usuario["rol"]
        db.execute("UPDATE usuarios SET contrasena = ?, activo = 1, rol = ? WHERE id = ?",
                   (generate_password_hash(nueva), rol, usuario["id"]))
        db.execute("DELETE FROM intentos_login WHERE clave LIKE ?", (f"%|{nombre}",))
        db.commit()
        print(f"\nListo: «{nombre}» ya puede entrar con su contraseña nueva"
              + (" como administrador." if hacer_admin else "."))
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
