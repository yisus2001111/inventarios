"""Arranca el servidor de inventario: python run.py"""

import os

from inventario import create_app

app = create_app()

if __name__ == "__main__":
    host = os.environ.get("INVENTARIO_HOST", "0.0.0.0")
    port = int(os.environ.get("INVENTARIO_PORT", "5000"))
    if os.environ.get("INVENTARIO_DEBUG") == "1":
        # Modo para modificar el código: se reinicia solo al guardar cambios y muestra
        # los errores con detalle. No usarlo para el trabajo diario.
        print(f"Inventario en http://127.0.0.1:{port} (modo desarrollo)")
        app.run(host="127.0.0.1", port=port, debug=True)
        raise SystemExit
    try:
        from waitress import serve
    except ImportError:
        print(f"Inventario en http://{host}:{port} (servidor de desarrollo)")
        app.run(host=host, port=port)
    else:
        print(f"Inventario en http://{host}:{port}")
        serve(app, host=host, port=port)
