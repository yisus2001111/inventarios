import io

import pytest

from inventario import create_app


@pytest.fixture
def app(tmp_path):
    return create_app({"TESTING": True, "CSRF_ENABLED": False, "SECRET_KEY": "test",
                       "DATABASE": str(tmp_path / "test.db")})


@pytest.fixture
def client(app):
    """Cliente con la sesión del administrador inicial ya abierta."""
    c = app.test_client()
    c.post("/configuracion-inicial", data={
        "usuario": "admin", "nombre": "Admin", "contrasena": "secreta1",
        "confirmacion": "secreta1",
    })
    return c


def crear_usuario(client, usuario, rol):
    return client.post("/usuarios/nuevo", data={
        "usuario": usuario, "nombre": usuario.title(), "rol": rol,
        "contrasena": "clave123", "confirmacion": "clave123",
    }, follow_redirects=True)


def entrar(app, usuario, contrasena="clave123"):
    c = app.test_client()
    c.post("/login", data={"usuario": usuario, "contrasena": contrasena})
    return c


def nuevo(client, codigo="A-1", stock=10, minimo=2):
    return client.post("/productos/nuevo", data={
        "codigo": codigo, "nombre": f"Producto {codigo}", "categoria": "General",
        "stock_inicial": stock, "stock_minimo": minimo,
    }, follow_redirects=True)


def mover(client, pid, tipo, cantidad):
    return client.post(f"/productos/{pid}/movimiento", data={
        "tipo": tipo, "cantidad": cantidad, "motivo": "prueba",
    }, follow_redirects=True)


def test_alta_de_producto_registra_movimiento_inicial(client):
    r = nuevo(client)
    assert "Producto dado de alta" in r.get_data(as_text=True)
    assert "Alta inicial" in r.get_data(as_text=True)


def test_codigo_duplicado(client):
    nuevo(client)
    r = nuevo(client)
    assert "Ya existe un producto" in r.get_data(as_text=True)


def test_alta_y_baja_actualizan_existencia(client):
    nuevo(client, stock=10)
    assert "Existencia actual: 15" in mover(client, 1, "alta", 5).get_data(as_text=True)
    assert "Existencia actual: 12" in mover(client, 1, "baja", 3).get_data(as_text=True)


def test_baja_mayor_a_existencia_se_rechaza(client):
    nuevo(client, stock=4)
    r = mover(client, 1, "baja", 5)
    assert "No hay existencia suficiente" in r.get_data(as_text=True)
    assert "Existencia actual" not in r.get_data(as_text=True)


@pytest.mark.parametrize("cantidad", ["0", "-3", "abc", ""])
def test_cantidad_invalida(client, cantidad):
    nuevo(client)
    r = mover(client, 1, "alta", cantidad)
    assert "mayor que cero" in r.get_data(as_text=True)


def test_movimiento_rapido_por_codigo(client):
    nuevo(client, codigo="X-9", stock=1)
    r = client.post("/movimiento", data={"codigo": "X-9", "tipo": "alta", "cantidad": 2},
                    follow_redirects=True)
    assert "Existencia actual: 3" in r.get_data(as_text=True)
    r = client.post("/movimiento", data={"codigo": "NOPE", "tipo": "alta", "cantidad": 2})
    assert "No existe ningún producto" in r.get_data(as_text=True)


def test_desactivar_requiere_existencia_cero(client):
    nuevo(client, stock=2)
    r = client.post("/productos/1/activo", data={"activar": "0"}, follow_redirects=True)
    assert "primero da de baja" in r.get_data(as_text=True)
    mover(client, 1, "baja", 2)
    r = client.post("/productos/1/activo", data={"activar": "0"}, follow_redirects=True)
    assert "Producto desactivado" in r.get_data(as_text=True)
    assert "desactivado" in mover(client, 1, "alta", 1).get_data(as_text=True)


def test_busqueda_y_bajo_minimo(client):
    nuevo(client, codigo="A-1", stock=10, minimo=2)
    nuevo(client, codigo="B-2", stock=1, minimo=5)
    html = client.get("/?estado=bajo").get_data(as_text=True)
    assert "B-2" in html and "A-1" not in html
    html = client.get("/?q=A-1").get_data(as_text=True)
    assert "A-1" in html and "B-2" not in html


def test_paginacion_con_800_productos(client):
    csv = "codigo;nombre;stock\n" + "\n".join(f"P{i:04d};Art {i};1" for i in range(800))
    r = client.post("/importar", data={"archivo": (io.BytesIO(csv.encode()), "inv.csv")},
                    content_type="multipart/form-data")
    assert "800 creados" in r.get_data(as_text=True)
    html = client.get("/").get_data(as_text=True)
    assert "Página 1 de 16" in html
    assert "<span>800</span>unidades" in html


def test_importar_actualiza_sin_tocar_existencia(client):
    nuevo(client, codigo="A-1", stock=10)
    csv = "codigo,nombre,stock,stock_minimo\nA-1,Nombre nuevo,999,4\n,sin codigo,1,0\n"
    r = client.post("/importar", data={"archivo": (io.BytesIO(csv.encode()), "inv.csv")},
                    content_type="multipart/form-data")
    texto = r.get_data(as_text=True)
    assert "0 creados, 1 actualizados, 1 con error" in texto
    html = client.get("/productos/1").get_data(as_text=True)
    assert "Nombre nuevo" in html and "<span>10</span>" in html


def test_exportar_csv(client):
    nuevo(client)
    mover(client, 1, "baja", 1)
    r = client.get("/exportar/productos.csv")
    assert r.mimetype == "text/csv" and "A-1" in r.get_data(as_text=True)
    r = client.get("/exportar/movimientos.csv?tipo=baja")
    lineas = r.get_data(as_text=True).strip().splitlines()
    assert len(lineas) == 2 and ",baja,1,9," in lineas[1]


# ------------------------------------------------------------------ usuarios


def test_sin_usuarios_pide_configuracion_inicial(app):
    c = app.test_client()
    r = c.get("/")
    assert r.status_code == 302 and "/configuracion-inicial" in r.location


def test_configuracion_inicial_solo_una_vez(client, app):
    r = app.test_client().post("/configuracion-inicial", data={
        "usuario": "intruso", "contrasena": "secreta1", "confirmacion": "secreta1"})
    assert r.status_code == 302
    assert "intruso" not in client.get("/usuarios").get_data(as_text=True)


def test_sin_sesion_redirige_a_login(client, app):
    anonimo = app.test_client()
    for url in ["/", "/movimientos", "/exportar/productos.csv", "/productos/nuevo"]:
        r = anonimo.get(url)
        assert r.status_code == 302 and "/login" in r.location
    r = anonimo.post("/productos/nuevo", data={"codigo": "Z", "nombre": "Z"})
    assert r.status_code == 302 and "/login" in r.location


def test_login_incorrecto(client, app):
    crear_usuario(client, "luis", "operador")
    c = app.test_client()
    r = c.post("/login", data={"usuario": "luis", "contrasena": "mala"})
    assert "incorrectos" in r.get_data(as_text=True)
    assert c.get("/").status_code == 302


def test_operador_mueve_pero_no_administra(client, app):
    nuevo(client, stock=5)
    crear_usuario(client, "luis", "operador")
    op = entrar(app, "luis")
    r = mover(op, 1, "baja", 2)
    assert "Existencia actual: 3" in r.get_data(as_text=True)
    assert "luis" in op.get("/movimientos").get_data(as_text=True)
    assert op.get("/productos/nuevo").status_code == 403
    assert op.post("/productos/1/editar", data={"codigo": "X", "nombre": "X"}).status_code == 403
    assert op.post("/productos/1/activo", data={"activar": "0"}).status_code == 403
    assert op.get("/importar").status_code == 403
    assert op.get("/usuarios").status_code == 403
    html = op.get("/productos/1").get_data(as_text=True)
    assert "Editar datos" not in html and "Registrar" in html


def test_consulta_solo_ve(client, app):
    nuevo(client, stock=5)
    crear_usuario(client, "vero", "consulta")
    c = entrar(app, "vero")
    assert c.get("/").status_code == 200
    assert c.get("/exportar/productos.csv").status_code == 200
    assert c.post("/productos/1/movimiento", data={"tipo": "baja", "cantidad": 1}).status_code == 403
    assert c.post("/movimiento", data={"codigo": "A-1", "tipo": "baja", "cantidad": 1}).status_code == 403
    html = c.get("/productos/1").get_data(as_text=True)
    assert "Registrar" not in html and "<span>5</span>" in html


def test_usuario_desactivado_pierde_acceso(client, app):
    crear_usuario(client, "luis", "operador")
    op = entrar(app, "luis")
    assert op.get("/").status_code == 200
    client.post("/usuarios/2", data={"nombre": "Luis", "rol": "operador"})  # sin "activo"
    assert op.get("/").status_code == 302
    assert entrar(app, "luis").get("/").status_code == 302


def test_no_se_puede_quitar_el_ultimo_admin(client):
    r = client.post("/usuarios/1", data={"nombre": "Admin", "rol": "operador", "activo": "1"},
                    follow_redirects=True)
    assert "al menos un administrador" in r.get_data(as_text=True)


def test_cambiar_contrasena(client, app):
    r = client.post("/mi-contrasena", data={"actual": "secreta1", "contrasena": "nueva123",
                                            "confirmacion": "nueva123"}, follow_redirects=True)
    assert "Contraseña actualizada" in r.get_data(as_text=True)
    assert entrar(app, "admin", "secreta1").get("/").status_code == 302
    assert entrar(app, "admin", "nueva123").get("/").status_code == 200


def test_login_no_redirige_fuera(client, app):
    crear_usuario(client, "luis", "operador")
    c = app.test_client()
    r = c.post("/login?siguiente=//evil.example", data={"usuario": "luis", "contrasena": "clave123"})
    assert r.location == "/"


def test_csrf_rechaza_formularios_sin_token(tmp_path):
    app = create_app({"TESTING": True, "SECRET_KEY": "test", "DATABASE": str(tmp_path / "c.db")})
    c = app.test_client()
    r = c.post("/configuracion-inicial", data={"usuario": "admin", "contrasena": "secreta1",
                                              "confirmacion": "secreta1"})
    assert r.status_code == 400
    html = c.get("/configuracion-inicial").get_data(as_text=True)
    token = html.split('name="csrf" value="')[1].split('"')[0]
    r = c.post("/configuracion-inicial", data={"usuario": "admin", "contrasena": "secreta1",
                                              "confirmacion": "secreta1", "csrf": token})
    assert r.status_code == 302


def test_clave_secreta_se_genera_y_persiste(tmp_path, monkeypatch):
    monkeypatch.delenv("INVENTARIO_SECRET_KEY", raising=False)
    db = str(tmp_path / "x.db")
    a = create_app({"DATABASE": db}).config["SECRET_KEY"]
    b = create_app({"DATABASE": db}).config["SECRET_KEY"]
    assert a == b and len(a) == 64 and (tmp_path / ".clave_secreta").exists()
