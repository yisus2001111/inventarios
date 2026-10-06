import base64
import io

import pytest

from inventario import create_app


PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360f8cf000000030101007a4d6f0f0000000049454e44ae426082")
FIRMA = "data:image/png;base64," + base64.b64encode(PNG_1PX).decode()
CREDENCIAL = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0fotojpeg").decode()


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
    assert len(lineas) == 2 and ",Baja,,1,9," in lineas[1]


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


# --------------------------------------------------------------- ubicaciones


def test_cambiar_ubicacion_deja_historial(client, app):
    nuevo(client)
    crear_usuario(client, "luis", "operador")
    op = entrar(app, "luis")
    r = op.post("/productos/1/ubicacion", data={"ubicacion": "Bodega 2"}, follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "Ubicación cambiada a «Bodega 2»" in html
    assert "Cambios de ubicación" in html and "luis" in html
    r = op.post("/productos/1/ubicacion", data={"ubicacion": "Bodega 2"}, follow_redirects=True)
    assert "no cambió" in r.get_data(as_text=True)


def test_consulta_no_cambia_ubicacion(client, app):
    nuevo(client)
    crear_usuario(client, "vero", "consulta")
    c = entrar(app, "vero")
    assert c.post("/productos/1/ubicacion", data={"ubicacion": "X"}).status_code == 403
    assert c.post("/productos/mover", data={"ids": ["1"], "ubicacion": "X"}).status_code == 403
    assert "Cambiar ubicación" not in c.get("/productos/1").get_data(as_text=True)


def test_mover_varios(client):
    for codigo in ("A-1", "A-2", "A-3"):
        nuevo(client, codigo=codigo)
    r = client.post("/productos/mover", data={"ids": ["1", "3"], "ubicacion": "Estante C",
                                              "volver": "/?estado=todos"})
    assert r.location == "/?estado=todos"
    html = client.get("/?ubicacion=Estante+C").get_data(as_text=True)
    assert "A-1" in html and "A-3" in html and "A-2" not in html
    r = client.post("/productos/mover", data={"ubicacion": "X"}, follow_redirects=True)
    assert "Marca al menos un producto" in r.get_data(as_text=True)
    r = client.post("/productos/mover", data={"ids": ["1"], "volver": "//evil.example"})
    assert r.location == "/"


def test_editar_registra_cambio_de_ubicacion(client):
    nuevo(client)
    client.post("/productos/1/editar", data={"codigo": "A-1", "nombre": "Producto A-1",
                                             "ubicacion": "Almacén 3", "stock_minimo": 2})
    html = client.get("/productos/1").get_data(as_text=True)
    assert "Almacén 3" in html and "Cambios de ubicación" in html


def test_importar_solo_codigo_y_ubicacion_no_borra_lo_demas(client):
    nuevo(client, codigo="A-1", minimo=7)
    csv = "codigo;ubicacion\nA-1;Pasillo 4\n"
    r = client.post("/importar", data={"archivo": (io.BytesIO(csv.encode()), "u.csv")},
                    content_type="multipart/form-data")
    assert "1 actualizados, 0 con error" in r.get_data(as_text=True)
    html = client.get("/productos/1").get_data(as_text=True)
    assert "Pasillo 4" in html and "Producto A-1" in html and "General" in html
    assert "mínimo 7" in html and "Cambios de ubicación" in html


# --------------------------------------------------------------------- vales

DATOS_VALE = {"alumno": "María López", "matricula": "A0123", "materia": "Química I",
              "maestro": "Dr. Ramírez", "practica": "Titulación ácido-base"}


def vale(client, renglones, **extra):
    data = dict(DATOS_VALE, firma=FIRMA)
    data.update(extra)
    data["material"] = [r[0] for r in renglones]
    data["cantidad"] = [str(r[1]) for r in renglones]
    return client.post("/vales/nuevo", data=data, follow_redirects=True)


def existencia(client, pid):
    html = client.get(f"/productos/{pid}").get_data(as_text=True)
    return int(html.split('class="existencia')[1].split("<span>")[1].split("</span>")[0])


def test_vale_descuenta_y_muestra_datos(client):
    nuevo(client, codigo="LAB-1", stock=10)
    nuevo(client, codigo="LAB-2", stock=5)
    r = vale(client, [("LAB-1 — Producto LAB-1", 3), ("LAB-2", 2), ("", 1)])
    html = r.get_data(as_text=True)
    assert "Vale V-00001 registrado" in html
    for dato in DATOS_VALE.values():
        assert dato in html
    assert "LAB-1" in html and "LAB-2" in html
    assert existencia(client, 1) == 7 and existencia(client, 2) == 3
    assert "Vale V-00001" in client.get("/movimientos").get_data(as_text=True)
    assert "3 prestados en vales" in client.get("/productos/1").get_data(as_text=True)


def test_vale_sin_existencia_no_registra_nada(client):
    nuevo(client, codigo="LAB-1", stock=10)
    nuevo(client, codigo="LAB-2", stock=1)
    r = vale(client, [("LAB-1", 3), ("LAB-2", 5)])
    assert "No hay existencia suficiente" in r.get_data(as_text=True)
    assert existencia(client, 1) == 10 and existencia(client, 2) == 1
    assert "No hay vales" in client.get("/vales/?estado=todos").get_data(as_text=True)


def test_vale_valida_campos_y_material(client):
    nuevo(client, codigo="LAB-1", stock=10)
    r = vale(client, [("LAB-1", 1)], maestro="", practica="")
    assert "Falta: nombre del maestro, nombre de la práctica." in r.get_data(as_text=True)
    r = vale(client, [("NOEXISTE", 1)])
    assert "No existe material" in r.get_data(as_text=True)
    r = vale(client, [("", 1)])
    assert "Agrega al menos un material" in r.get_data(as_text=True)
    # el formulario conserva lo capturado
    assert 'value="María López"' in r.get_data(as_text=True)


def test_renglones_repetidos_se_suman(client):
    nuevo(client, codigo="LAB-1", stock=10)
    vale(client, [("LAB-1", 2), ("LAB-1", 3)])
    assert existencia(client, 1) == 5
    assert "5 × Producto LAB-1" in client.get("/vales/").get_data(as_text=True)


def test_devolucion_parcial_y_total(client):
    nuevo(client, codigo="LAB-1", stock=10)
    vale(client, [("LAB-1", 4)])
    r = client.post("/vales/1/devolucion", data={"devolver_1": "1"}, follow_redirects=True)
    assert "Devolución registrada (1 pza)" in r.get_data(as_text=True)
    assert existencia(client, 1) == 7
    assert "debe 3" in client.get("/vales/").get_data(as_text=True)
    r = client.post("/vales/1/devolucion", data={"devolver_1": "9"}, follow_redirects=True)
    assert "quedan 3 por devolver" in r.get_data(as_text=True)
    r = client.post("/vales/1/devolucion", data={"devolver_1": "3"}, follow_redirects=True)
    assert "el vale quedó cerrado" in r.get_data(as_text=True)
    assert existencia(client, 1) == 10
    assert "No hay vales" in client.get("/vales/").get_data(as_text=True)


def test_cerrar_con_consumibles(client):
    nuevo(client, codigo="REA-1", stock=10)
    vale(client, [("REA-1", 4)])
    r = client.post("/vales/1/cerrar", follow_redirects=True)
    assert "queda como consumido" in r.get_data(as_text=True)
    assert existencia(client, 1) == 6
    r = client.post("/vales/1/devolucion", data={"devolver_1": "1"}, follow_redirects=True)
    assert "no tiene material pendiente" in r.get_data(as_text=True)


def test_no_desactivar_con_material_prestado(client):
    nuevo(client, codigo="LAB-1", stock=2)
    vale(client, [("LAB-1", 2)])
    r = client.post("/productos/1/activo", data={"activar": "0"}, follow_redirects=True)
    assert "prestadas en vales" in r.get_data(as_text=True)


def test_busqueda_y_exportacion_de_vales(client):
    nuevo(client, codigo="LAB-1", stock=10)
    vale(client, [("LAB-1", 1)])
    vale(client, [("LAB-1", 1)], alumno="Pedro Gómez", maestro="Mtra. Ruiz")
    html = client.get("/vales/?q=Ruiz").get_data(as_text=True)
    assert "Pedro Gómez" in html and "María López" not in html
    html = client.get("/vales/?q=V-00001").get_data(as_text=True)
    assert "María López" in html and "Pedro Gómez" not in html
    r = client.get("/vales/exportar.csv?estado=todos")
    lineas = r.get_data(as_text=True).strip().splitlines()
    assert len(lineas) == 3 and "numero_inventario" in lineas[0]
    assert "V-00001" in lineas[1] and "Titulación ácido-base" in lineas[1]


def test_permisos_vales(client, app):
    nuevo(client, codigo="LAB-1", stock=5)
    crear_usuario(client, "luis", "operador")
    crear_usuario(client, "vero", "consulta")
    assert "registrado" in vale(entrar(app, "luis"), [("LAB-1", 1)]).get_data(as_text=True)
    c = entrar(app, "vero")
    assert c.get("/vales/").status_code == 200 and c.get("/vales/1").status_code == 200
    assert c.get("/vales/nuevo").status_code == 403
    assert c.post("/vales/1/devolucion", data={"devolver_1": "1"}).status_code == 403
    assert c.post("/vales/1/cerrar").status_code == 403


# ------------------------------------------------- solicitudes desde el celular


def solicitar(c, renglones, **extra):
    data = dict(DATOS_VALE, firma=FIRMA, credencial=CREDENCIAL)
    data.update(extra)
    data["material"] = [r[0] for r in renglones]
    data["cantidad"] = [str(r[1]) for r in renglones]
    return c.post("/solicitud/", data=data, follow_redirects=True)


def test_alumno_solicita_sin_cuenta_y_no_descuenta(client, app):
    nuevo(client, codigo="LAB-1", stock=10)
    alumno = app.test_client()
    html = alumno.get("/solicitud/").get_data(as_text=True)
    assert "Solicitud de material" in html and "LAB-1" in html
    r = solicitar(alumno, [("LAB-1", 3)])
    html = r.get_data(as_text=True)
    assert "V-00001" in html and "En espera" in html and 'http-equiv="refresh"' in html
    assert existencia(client, 1) == 10
    # el alumno no puede ver nada del sistema interno
    assert alumno.get("/vales/1").status_code == 302
    assert alumno.get("/").status_code == 302
    # aparece al encargado como por entregar
    assert "por entregar" in client.get("/vales/").get_data(as_text=True)
    assert client.get("/vales/solicitudes.json").get_json() == {"solicitudes": 1}


def test_entregar_solicitud_ajustando_cantidades(client, app):
    nuevo(client, codigo="LAB-1", stock=10)
    nuevo(client, codigo="LAB-2", stock=4)
    alumno = app.test_client()
    r = solicitar(alumno, [("LAB-1", 3), ("LAB-2", 2)])
    token = r.request.path.rsplit("/", 1)[1]
    r = client.post("/vales/1/entregar", data={"entregar_1": "2", "entregar_2": "0"},
                    follow_redirects=True)
    assert "Material entregado" in r.get_data(as_text=True)
    assert existencia(client, 1) == 8 and existencia(client, 2) == 4
    html = alumno.get(f"/solicitud/{token}").get_data(as_text=True)
    assert "Material entregado" in html and "2 × Producto LAB-1" in html
    assert "LAB-2" not in html and 'http-equiv="refresh"' not in html
    r = client.post("/vales/1/entregar", data={"entregar_1": "1"}, follow_redirects=True)
    assert "ya fue atendida" in r.get_data(as_text=True)
    # y se devuelve como cualquier vale
    client.post("/vales/1/devolucion", data={"devolver_1": "2"})
    assert existencia(client, 1) == 10


def test_entregar_sin_existencia_falla_sin_cambios(client, app):
    nuevo(client, codigo="LAB-1", stock=3)
    solicitar(app.test_client(), [("LAB-1", 3)])
    vale(client, [("LAB-1", 2)])  # mientras tanto se prestó en mostrador
    r = client.post("/vales/1/entregar", data={"entregar_1": "3"}, follow_redirects=True)
    assert "No hay existencia suficiente" in r.get_data(as_text=True)
    assert existencia(client, 1) == 1
    assert "por entregar" in client.get("/vales/").get_data(as_text=True)


def test_entregar_todo_en_cero_pide_rechazar(client, app):
    nuevo(client, codigo="LAB-1", stock=3)
    solicitar(app.test_client(), [("LAB-1", 1)])
    r = client.post("/vales/1/entregar", data={"entregar_1": "0"}, follow_redirects=True)
    assert "rechaza la solicitud" in r.get_data(as_text=True)


def test_rechazar_solicitud(client, app):
    nuevo(client, codigo="LAB-1", stock=3)
    alumno = app.test_client()
    token = solicitar(alumno, [("LAB-1", 1)]).request.path.rsplit("/", 1)[1]
    client.post("/vales/1/rechazar", data={"motivo": "Falta firma del maestro"})
    html = alumno.get(f"/solicitud/{token}").get_data(as_text=True)
    assert "Solicitud rechazada" in html and "Falta firma del maestro" in html
    assert existencia(client, 1) == 3


def test_solicitud_valida_existencia_y_datos(client, app):
    nuevo(client, codigo="LAB-1", stock=2)
    alumno = app.test_client()
    assert "solo hay 2 disponibles" in solicitar(alumno, [("LAB-1", 5)]).get_data(as_text=True)
    r = solicitar(alumno, [("LAB-1", 1)], alumno="x" * 200)
    assert "máximo 150 caracteres" in r.get_data(as_text=True)
    client.post("/productos/1/movimiento", data={"tipo": "baja", "cantidad": 2})
    assert "no está disponible" in solicitar(alumno, [("LAB-1", 1)]).get_data(as_text=True)


def test_limite_de_solicitudes_en_espera_y_mis_solicitudes(client, app):
    nuevo(client, codigo="LAB-1", stock=50)
    alumno = app.test_client()
    for _ in range(3):
        solicitar(alumno, [("LAB-1", 1)])
    r = solicitar(alumno, [("LAB-1", 1)])
    assert "Ya tienes 3 solicitudes en espera" in r.get_data(as_text=True)
    html = alumno.get("/solicitud/").get_data(as_text=True)
    assert "Mis solicitudes recientes (3)" in html
    assert 'value="María López"' in html  # recuerda el nombre del alumno


def test_token_de_solicitud_no_adivinable(client, app):
    nuevo(client, codigo="LAB-1", stock=5)
    solicitar(app.test_client(), [("LAB-1", 1)])
    assert app.test_client().get("/solicitud/1").status_code == 404


def test_qr_para_alumnos(client, app):
    r = client.get("/vales/qr")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "<svg" in html and "/solicitud/" in html
    crear_usuario(client, "vero", "consulta")
    assert entrar(app, "vero").get("/vales/qr").status_code == 403


def test_migra_base_de_datos_de_version_anterior(tmp_path):
    import sqlite3
    ruta = tmp_path / "vieja.db"
    con = sqlite3.connect(ruta)
    con.executescript("""
        CREATE TABLE productos (id INTEGER PRIMARY KEY AUTOINCREMENT, codigo TEXT NOT NULL UNIQUE,
            nombre TEXT NOT NULL, categoria TEXT NOT NULL DEFAULT '', ubicacion TEXT NOT NULL DEFAULT '',
            unidad TEXT NOT NULL DEFAULT 'pza', stock INTEGER NOT NULL DEFAULT 0,
            stock_minimo INTEGER NOT NULL DEFAULT 0, activo INTEGER NOT NULL DEFAULT 1, creado_en TEXT NOT NULL);
        CREATE TABLE vales (id INTEGER PRIMARY KEY AUTOINCREMENT, alumno TEXT NOT NULL,
            matricula TEXT NOT NULL DEFAULT '', materia TEXT NOT NULL, maestro TEXT NOT NULL,
            practica TEXT NOT NULL, observaciones TEXT NOT NULL DEFAULT '', fecha TEXT NOT NULL,
            usuario TEXT NOT NULL, estado TEXT NOT NULL DEFAULT 'abierto'
            CHECK (estado IN ('abierto', 'cerrado')), cerrado_en TEXT, cerrado_por TEXT);
        CREATE TABLE vale_items (id INTEGER PRIMARY KEY AUTOINCREMENT, vale_id INTEGER NOT NULL REFERENCES vales(id),
            producto_id INTEGER NOT NULL REFERENCES productos(id), cantidad INTEGER NOT NULL CHECK (cantidad > 0),
            devuelto INTEGER NOT NULL DEFAULT 0);
        INSERT INTO productos (codigo, nombre, stock, creado_en) VALUES ('LAB-1', 'Matraz', 5, '2026-01-01');
        INSERT INTO vales (alumno, materia, maestro, practica, fecha, usuario)
            VALUES ('Ana', 'Física', 'Dr. X', 'Péndulo', '2026-01-02 10:00:00', 'luis');
        INSERT INTO vale_items (vale_id, producto_id, cantidad) VALUES (1, 1, 2);
    """)
    con.commit()
    con.close()
    app = create_app({"TESTING": True, "CSRF_ENABLED": False, "SECRET_KEY": "t", "DATABASE": str(ruta)})
    c = app.test_client()
    c.post("/configuracion-inicial", data={"usuario": "admin", "contrasena": "secreta1",
                                          "confirmacion": "secreta1"})
    html = c.get("/vales/1").get_data(as_text=True)
    assert "Ana" in html and "Péndulo" in html and "Matraz" in html
    assert "2026-01-02 10:00:00 por luis" in html  # entregado = fecha original
    c.post("/vales/1/devolucion", data={"devolver_1": "2"})
    assert existencia(c, 1) == 7
    # ya admite solicitudes de alumnos
    assert "V-00002" in solicitar(app.test_client(), [("LAB-1", 1)]).get_data(as_text=True)


# ------------------------------------- vales en el historial, borrado y marca


def test_vale_aparece_como_vale_y_no_como_baja(client):
    nuevo(client, codigo="LAB-1", stock=10)
    vale(client, [("LAB-1", 3)])
    client.post("/vales/1/devolucion", data={"devolver_1": "1"})
    html = client.get("/movimientos").get_data(as_text=True)
    assert 'class="etiqueta prestamo" href="/vales/1">Vale V-00001' in html
    assert 'class="etiqueta devolucion" href="/vales/1">Devolución V-00001' in html
    assert 'etiqueta baja' not in html
    html = client.get("/movimientos?tipo=vale").get_data(as_text=True)
    assert "Vale V-00001" in html and "Devolución V-00001" not in html
    assert "−3" in html
    assert "Vale V-00001" in client.get("/movimientos?q=V-00001").get_data(as_text=True)
    csv = client.get("/exportar/movimientos.csv?tipo=devolucion").get_data(as_text=True)
    assert ",Devolución,V-00001,1," in csv
    # la ficha del producto también lo muestra como vale
    assert "Vale V-00001" in client.get("/productos/1").get_data(as_text=True)


def test_no_se_puede_registrar_vale_desde_alta_baja(client):
    nuevo(client, codigo="LAB-1", stock=10)
    r = client.post("/productos/1/movimiento", data={"tipo": "vale", "cantidad": 1},
                    follow_redirects=True)
    assert "Tipo de movimiento no válido" in r.get_data(as_text=True)
    assert existencia(client, 1) == 10


def test_admin_elimina_registros_y_corrige_existencia(client):
    nuevo(client, codigo="A-1", stock=10)          # movimiento 1: alta inicial 10
    mover(client, 1, "baja", 4)                    # movimiento 2
    mover(client, 1, "alta", 2)                    # movimiento 3
    assert existencia(client, 1) == 8
    r = client.post("/movimientos/eliminar", data={"ids": ["2", "3"], "corregir": "1"},
                    follow_redirects=True)
    assert "Se eliminó: 2 registros. Existencia corregida." in r.get_data(as_text=True)
    assert existencia(client, 1) == 10
    # sin corregir: solo se borra la línea
    client.post("/movimientos/eliminar", data={"ids": ["1"]})
    assert existencia(client, 1) == 10
    assert "Sin movimientos" in client.get("/productos/1").get_data(as_text=True)


def test_eliminar_no_deja_existencia_negativa(client):
    nuevo(client, codigo="A-1", stock=5)
    mover(client, 1, "baja", 4)
    r = client.post("/movimientos/eliminar", data={"ids": ["1"], "corregir": "1"},
                    follow_redirects=True)
    assert "quedaría negativa" in r.get_data(as_text=True)
    assert existencia(client, 1) == 1


def test_eliminar_desde_historial_borra_el_vale_completo(client):
    nuevo(client, codigo="LAB-1", stock=10)
    vale(client, [("LAB-1", 3)])
    client.post("/vales/1/devolucion", data={"devolver_1": "1"})
    assert existencia(client, 1) == 8
    ids = __import__("re").findall(r'name="ids" value="(\d+)" form="borrar" data-vale="V-00001"',
                                   client.get("/movimientos").get_data(as_text=True))
    assert len(ids) == 2   # préstamo y devolución se pueden marcar
    r = client.post("/movimientos/eliminar", data={"ids": ids[:1], "corregir": "1"},
                    follow_redirects=True)
    assert "vale V-00001 (su folio queda disponible)" in r.get_data(as_text=True)
    assert existencia(client, 1) == 10
    assert client.get("/vales/1").status_code == 404
    assert "V-00001" not in client.get("/movimientos").get_data(as_text=True)


def test_admin_elimina_vale_y_regresa_material(client):
    nuevo(client, codigo="LAB-1", stock=10)
    vale(client, [("LAB-1", 4)])
    client.post("/vales/1/devolucion", data={"devolver_1": "1"})
    client.post("/vales/1/cerrar")                 # 3 quedaron como consumidos
    assert existencia(client, 1) == 7
    r = client.post("/vales/1/eliminar", follow_redirects=True)
    assert "Vale V-00001 eliminado" in r.get_data(as_text=True)
    assert existencia(client, 1) == 10
    assert "V-00001" not in client.get("/movimientos").get_data(as_text=True)
    assert client.get("/vales/1").status_code == 404


def test_solo_admin_elimina(client, app):
    nuevo(client, codigo="LAB-1", stock=10)
    vale(client, [("LAB-1", 1)])
    crear_usuario(client, "luis", "operador")
    op = entrar(app, "luis")
    assert op.post("/movimientos/eliminar", data={"ids": ["1"]}).status_code == 403
    assert op.post("/vales/1/eliminar").status_code == 403
    assert "Eliminar" not in op.get("/movimientos").get_data(as_text=True)
    assert op.get("/configuracion").status_code == 403




def test_configuracion_logo_e_institucion(client, app):
    html = client.get("/").get_data(as_text=True)
    assert "UES San Luis Río Colorado" in html and 'class="logo"' not in html
    r = client.post("/configuracion", data={"institucion": "UES San Luis Río Colorado",
                                            "logo": (io.BytesIO(PNG_1PX), "logo.png")},
                    content_type="multipart/form-data", follow_redirects=True)
    assert "Configuración guardada" in r.get_data(as_text=True)
    html = client.get("/").get_data(as_text=True)
    assert 'class="logo" src="/logo?v=' in html
    # el logo se ve también en la página pública de alumnos, sin sesión
    alumno = app.test_client()
    assert 'class="logo"' in alumno.get("/solicitud/").get_data(as_text=True)
    r = alumno.get("/logo")
    assert r.status_code == 200 and r.mimetype == "image/png" and r.data == PNG_1PX
    # se rechazan archivos que no son imagen (por ejemplo SVG o HTML)
    r = client.post("/configuracion", data={"institucion": "X",
                                            "logo": (io.BytesIO(b"<svg onload=alert(1)>"), "l.svg")},
                    content_type="multipart/form-data", follow_redirects=True)
    assert "PNG, JPG o WEBP" in r.get_data(as_text=True)
    client.post("/configuracion", data={"institucion": "UES SLRC", "quitar_logo": "1"})
    html = client.get("/").get_data(as_text=True)
    assert "UES SLRC" in html and 'class="logo"' not in html
    assert alumno.get("/logo").status_code == 404


def test_migra_movimientos_de_vales_anteriores(tmp_path):
    import sqlite3
    ruta = tmp_path / "vieja.db"
    con = sqlite3.connect(ruta)
    con.executescript("""
        CREATE TABLE productos (id INTEGER PRIMARY KEY AUTOINCREMENT, codigo TEXT NOT NULL UNIQUE,
            nombre TEXT NOT NULL, categoria TEXT NOT NULL DEFAULT '', ubicacion TEXT NOT NULL DEFAULT '',
            unidad TEXT NOT NULL DEFAULT 'pza', stock INTEGER NOT NULL DEFAULT 0,
            stock_minimo INTEGER NOT NULL DEFAULT 0, activo INTEGER NOT NULL DEFAULT 1, creado_en TEXT NOT NULL);
        CREATE TABLE movimientos (id INTEGER PRIMARY KEY AUTOINCREMENT,
            producto_id INTEGER NOT NULL REFERENCES productos(id),
            tipo TEXT NOT NULL CHECK (tipo IN ('alta', 'baja')), cantidad INTEGER NOT NULL CHECK (cantidad > 0),
            stock_resultante INTEGER NOT NULL, motivo TEXT NOT NULL DEFAULT '',
            responsable TEXT NOT NULL DEFAULT '', fecha TEXT NOT NULL);
        INSERT INTO productos (codigo, nombre, stock, creado_en) VALUES ('LAB-1', 'Matraz', 9, '2026-01-01');
        INSERT INTO movimientos (producto_id, tipo, cantidad, stock_resultante, motivo, responsable, fecha) VALUES
            (1, 'alta', 10, 10, 'Alta inicial', 'admin', '2026-01-01 09:00:00'),
            (1, 'baja', 2, 8, 'Vale V-00012 · Ana · Péndulo', 'luis', '2026-01-02 10:00:00'),
            (1, 'alta', 1, 9, 'Devolución vale V-00012', 'luis', '2026-01-02 12:00:00');
    """)
    con.commit()
    con.close()
    app = create_app({"TESTING": True, "CSRF_ENABLED": False, "SECRET_KEY": "t", "DATABASE": str(ruta)})
    c = app.test_client()
    c.post("/configuracion-inicial", data={"usuario": "admin", "contrasena": "secreta1",
                                          "confirmacion": "secreta1"})
    html = c.get("/movimientos").get_data(as_text=True)
    assert "Vale V-00012" in html and "Devolución V-00012" in html
    assert 'class="etiqueta alta"' in html and 'etiqueta baja' not in html


def test_error_inesperado_muestra_pagina_y_queda_en_log(tmp_path):
    app = create_app({"CSRF_ENABLED": False, "SECRET_KEY": "t",
                      "DATABASE": str(tmp_path / "e.db")})

    @app.route("/falla")
    def falla():
        raise RuntimeError("algo <salió> mal")

    c = app.test_client()
    c.post("/configuracion-inicial", data={"usuario": "admin", "contrasena": "secreta1",
                                          "confirmacion": "secreta1"})
    r = c.get("/falla")
    html = r.get_data(as_text=True)
    assert r.status_code == 500 and "ciérralo y vuelve a abrirlo" in html
    assert "RuntimeError: algo &lt;salió&gt; mal" in html
    log = (tmp_path / "errores.log").read_text(encoding="utf-8")
    assert "RuntimeError: algo <salió> mal" in log and "Traceback" in log



# ------------------------------------------------- firma y foto de credencial


def test_solicitud_exige_firma_y_credencial(client, app):
    nuevo(client, codigo="LAB-1", stock=5)
    alumno = app.test_client()
    html = alumno.get("/solicitud/").get_data(as_text=True)
    assert 'id="firma-lienzo"' in html and 'capture="environment"' in html
    assert "Falta la firma" in solicitar(alumno, [("LAB-1", 1)], firma="").get_data(as_text=True)
    r = solicitar(alumno, [("LAB-1", 1)], credencial="")
    assert "Falta la foto de la credencial" in r.get_data(as_text=True)
    assert "No hay vales" in client.get("/vales/?estado=todos").get_data(as_text=True)
    r = solicitar(alumno, [("LAB-1", 1)])
    assert "Firma registrada" in r.get_data(as_text=True)


def test_encargado_ve_firma_y_credencial_pero_el_publico_no(client, app):
    nuevo(client, codigo="LAB-1", stock=5)
    alumno = app.test_client()
    solicitar(alumno, [("LAB-1", 1)])
    html = client.get("/vales/1").get_data(as_text=True)
    assert 'src="/vales/1/credencial"' in html and 'src="/vales/1/firma"' in html
    r = client.get("/vales/1/firma")
    assert r.status_code == 200 and r.mimetype == "image/png" and r.data == PNG_1PX
    assert client.get("/vales/1/credencial").mimetype == "image/jpeg"
    # sin sesión no se pueden ver las imágenes
    assert alumno.get("/vales/1/credencial").status_code == 302
    assert alumno.get("/vales/1/firma").status_code == 302


def test_credencial_como_archivo_si_el_telefono_no_la_reduce(client, app):
    nuevo(client, codigo="LAB-1", stock=5)
    data = dict(DATOS_VALE, firma=FIRMA, material=["LAB-1"], cantidad=["1"],
                credencial_archivo=(io.BytesIO(b"\xff\xd8\xff\xe0otrafoto"), "foto.jpg"))
    r = app.test_client().post("/solicitud/", data=data, content_type="multipart/form-data",
                               follow_redirects=True)
    assert "Foto de credencial registrada" in r.get_data(as_text=True)


def test_rechaza_imagenes_invalidas(client, app):
    nuevo(client, codigo="LAB-1", stock=5)
    alumno = app.test_client()
    falsa = "data:image/png;base64," + base64.b64encode(b"<script>alert(1)</script>").decode()
    assert "debe ser una imagen" in solicitar(alumno, [("LAB-1", 1)], firma=falsa).get_data(as_text=True)
    assert "No se pudo leer" in solicitar(alumno, [("LAB-1", 1)], firma="data:image/png;base64,%%%").get_data(as_text=True)


def test_mostrador_pide_firma_y_credencial_opcional(client):
    nuevo(client, codigo="LAB-1", stock=5)
    r = vale(client, [("LAB-1", 1)], firma="")
    assert "Falta la firma del alumno" in r.get_data(as_text=True)
    r = vale(client, [("LAB-1", 1)])          # sin credencial: se permite
    assert "registrado" in r.get_data(as_text=True)
    assert 'src="/vales/1/firma"' in r.get_data(as_text=True)


def test_configuracion_desactiva_firma_y_credencial(client, app):
    nuevo(client, codigo="LAB-1", stock=5)
    client.post("/configuracion", data={"institucion": "UES San Luis Río Colorado"})
    alumno = app.test_client()
    assert 'id="firma-lienzo"' not in alumno.get("/solicitud/").get_data(as_text=True)
    r = solicitar(alumno, [("LAB-1", 1)], firma="", credencial="")
    assert "En espera" in r.get_data(as_text=True)


def test_borrar_fotos_de_vales_cerrados(client, app):
    nuevo(client, codigo="LAB-1", stock=5)
    solicitar(app.test_client(), [("LAB-1", 1)])
    solicitar(app.test_client(), [("LAB-1", 1)])
    client.post("/vales/1/entregar", data={})
    client.post("/vales/1/devolucion", data={"devolver_1": "1"})   # vale 1 queda cerrado
    assert "(1)" in client.get("/configuracion").get_data(as_text=True)
    r = client.post("/configuracion", data={"accion": "borrar_credenciales"}, follow_redirects=True)
    assert "Se eliminaron 1 foto" in r.get_data(as_text=True)
    assert client.get("/vales/1/credencial").status_code == 404
    assert client.get("/vales/1/firma").status_code == 200           # la firma se conserva
    assert client.get("/vales/2/credencial").status_code == 200      # vale abierto: intacta


def test_eliminar_vale_borra_sus_imagenes(client, app):
    nuevo(client, codigo="LAB-1", stock=5)
    solicitar(app.test_client(), [("LAB-1", 1)])
    client.post("/vales/1/eliminar")
    import sqlite3
    con = sqlite3.connect(app.config["DATABASE"])
    assert con.execute("SELECT COUNT(*) FROM vale_archivos").fetchone()[0] == 0


# --------------------------------------------------------- vales de empleados


def solicitar_empleado(c, renglones, **extra):
    data = {"alumno": "Ing. Pedro Ruiz", "practica": "Mantenimiento lab 2",
            "regreso_estimado": "14:30", "firma": FIRMA}
    data.update(extra)
    data["material"] = [r[0] for r in renglones]
    data["cantidad"] = [str(r[1]) for r in renglones]
    return c.post("/solicitud/empleado", data=data, follow_redirects=True)


def test_empleado_solicita_solo_con_nombre_y_firma(client, app):
    nuevo(client, codigo="LAB-1", stock=5)
    emp = app.test_client()
    html = emp.get("/solicitud/empleado").get_data(as_text=True)
    assert "Empleados" in html and 'name="materia"' not in html
    assert 'id="firma-lienzo"' in html and 'capture="environment"' not in html
    assert "Falta la firma del empleado" in solicitar_empleado(emp, [("LAB-1", 1)], firma="").get_data(as_text=True)
    assert "Falta: nombre del empleado" in solicitar_empleado(emp, [("LAB-1", 1)], alumno="").get_data(as_text=True)
    r = solicitar_empleado(emp, [("LAB-1", 1)], practica="")
    assert "Falta: materia o trabajo a realizar" in r.get_data(as_text=True)
    html = solicitar_empleado(emp, [("LAB-1", 2)]).get_data(as_text=True)
    assert "V-00001" in html and "Ing. Pedro Ruiz" in html and "Regreso estimado" in html
    assert existencia(client, 1) == 5   # aún no se entrega


def test_vale_de_empleado_registra_salida_y_entrada(client, app):
    nuevo(client, codigo="LAB-1", stock=5)
    emp = app.test_client()
    token = solicitar_empleado(emp, [("LAB-1", 2)]).request.path.rsplit("/", 1)[1]
    html = client.get("/vales/1").get_data(as_text=True)
    assert "Hora de salida" in html and "Aún no se entrega" in html and "<dt>Materia</dt>" not in html
    client.post("/vales/1/entregar", data={})
    html = client.get("/vales/1").get_data(as_text=True)
    assert "Hora de entrada</dt><dd>Pendiente" in html
    assert "Ing. Pedro Ruiz (empleado) · Mantenimiento lab 2" in client.get("/movimientos").get_data(as_text=True)
    assert "Materia o trabajo</dt><dd>Mantenimiento lab 2" in html
    client.post("/vales/1/devolucion", data={"devolver_1": "2"})
    vale = __import__("sqlite3").connect(app.config["DATABASE"]).execute(
        "SELECT entregado_en, cerrado_en FROM vales WHERE id = 1").fetchone()
    html = client.get("/vales/1").get_data(as_text=True)
    assert f"Hora de entrada</dt><dd>{vale[1]}" in html and vale[0] in html
    assert existencia(client, 1) == 5
    estado = emp.get(f"/solicitud/{token}").get_data(as_text=True)
    assert "Entrada</dt><dd>" + vale[1][:16] in estado


def test_empleado_cerrado_sin_devolver_no_tiene_hora_de_entrada(client, app):
    nuevo(client, codigo="LAB-1", stock=5)
    vale(client, [("LAB-1", 1)], solicitante="empleado", alumno="Ana Soto", practica="Inventario")
    client.post("/vales/1/cerrar")
    assert "Material no devuelto" in client.get("/vales/1").get_data(as_text=True)


def test_vale_de_empleado_en_mostrador_y_filtros(client):
    nuevo(client, codigo="LAB-1", stock=5)
    r = client.get("/vales/nuevo?solicitante=empleado").get_data(as_text=True)
    assert "Nombre del empleado" in r and 'name="materia"' not in r
    r = vale(client, [("LAB-1", 1)], solicitante="empleado", alumno="Ana Soto",
             materia="", maestro="", practica="", regreso_estimado="16:00")
    assert "Falta: materia o trabajo a realizar" in r.get_data(as_text=True)
    r = vale(client, [("LAB-1", 1)], solicitante="empleado", alumno="Ana Soto",
             materia="", maestro="", practica="Química I", regreso_estimado="16:00")
    assert "registrado" in r.get_data(as_text=True)
    vale(client, [("LAB-1", 1)])   # vale de alumno
    html = client.get("/vales/?solicitante=empleado").get_data(as_text=True)
    assert "Ana Soto" in html and "María López" not in html and "regresa ~16:00" in html
    assert "Química I" in html
    csv = client.get("/vales/exportar.csv?estado=todos&solicitante=empleado").get_data(as_text=True)
    assert "hora_salida" in csv and ",empleado,Ana Soto," in csv and ",16:00," in csv
    r = vale(client, [("LAB-1", 1)], solicitante="empleado", alumno="X", practica="Y",
             regreso_estimado="mañana")
    assert "hora estimada de regreso no es válida" in r.get_data(as_text=True)


def test_migra_vales_sin_columna_solicitante(tmp_path):
    import sqlite3
    from inventario.db import VALES_DDL
    ruta = tmp_path / "v.db"
    con = sqlite3.connect(ruta)
    ddl = VALES_DDL.format(nombre="vales").replace(
        ",\n    solicitante    TEXT    NOT NULL DEFAULT 'alumno' CHECK (solicitante IN ('alumno', 'empleado')),\n    regreso_estimado TEXT  NOT NULL DEFAULT ''", "")
    assert "solicitante" not in ddl
    con.executescript(ddl + """
        INSERT INTO vales (alumno, materia, maestro, practica, fecha, usuario)
        VALUES ('Ana', 'F', 'M', 'P', '2026-01-01 10:00:00', 'luis');""")
    con.commit()
    con.close()
    app = create_app({"TESTING": True, "CSRF_ENABLED": False, "SECRET_KEY": "t", "DATABASE": str(ruta)})
    c = app.test_client()
    c.post("/configuracion-inicial", data={"usuario": "admin", "contrasena": "secreta1",
                                          "confirmacion": "secreta1"})
    assert "Ana" in c.get("/vales/?estado=todos&solicitante=alumno").get_data(as_text=True)



# ------------------------------------------ seguridad del inicio de sesión


def test_bloquea_tras_varios_intentos_fallidos(client, app):
    crear_usuario(client, "luis", "operador")
    c = app.test_client()
    for _ in range(5):
        r = c.post("/login", data={"usuario": "luis", "contrasena": "mala"})
        assert "incorrectos" in r.get_data(as_text=True)
    r = c.post("/login", data={"usuario": "luis", "contrasena": "clave123"})
    assert r.status_code == 429 and "Demasiados intentos" in r.get_data(as_text=True)
    assert c.get("/").status_code == 302
    # otro usuario desde la misma IP aún puede entrar
    assert entrar(app, "admin", "secreta1").get("/").status_code == 200


def test_encabezados_de_seguridad(client):
    r = client.get("/")
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_qr_usa_la_red_local_y_no_localhost(client):
    html = client.get("/vales/qr", base_url="http://localhost:5000").get_data(as_text=True)
    url = html.split('class="url">')[1].split("<")[0]
    assert url.endswith(":5000/solicitud/") and "localhost" not in url
    assert url.startswith("http://")


def test_encabezado_falso_de_ip_no_evita_el_bloqueo(client, app):
    crear_usuario(client, "luis", "operador")
    c = app.test_client()
    for i in range(5):
        c.post("/login", data={"usuario": "luis", "contrasena": "mala"},
               headers={"CF-Connecting-IP": f"10.0.0.{i}", "X-Forwarded-For": f"10.0.1.{i}"})
    r = c.post("/login", data={"usuario": "luis", "contrasena": "clave123"})
    assert r.status_code == 429



# ------------------------------------------------------------- folios libres


def test_folio_eliminado_vuelve_a_estar_disponible(client):
    nuevo(client, codigo="LAB-1", stock=20)
    for _ in range(3):
        vale(client, [("LAB-1", 1)])                     # V-00001, V-00002, V-00003
    client.post("/vales/2/eliminar")
    r = vale(client, [("LAB-1", 1)], alumno="Nuevo Alumno")
    assert "Vale V-00002 registrado" in r.get_data(as_text=True)
    assert "Vale V-00004 registrado" in vale(client, [("LAB-1", 1)]).get_data(as_text=True)
    client.post("/vales/4/eliminar")                     # el último también se libera
    assert "Vale V-00004 registrado" in vale(client, [("LAB-1", 1)]).get_data(as_text=True)


def test_folio_libre_en_solicitudes_y_sin_datos_del_vale_anterior(client, app):
    nuevo(client, codigo="LAB-1", stock=20)
    solicitar(app.test_client(), [("LAB-1", 1)])         # V-00001 con firma y credencial
    vale(client, [("LAB-1", 1)])                         # V-00002
    client.post("/vales/1/eliminar")
    html = solicitar_empleado(app.test_client(), [("LAB-1", 1)]).get_data(as_text=True)
    assert "V-00001" in html and "Ing. Pedro Ruiz" in html
    # el folio reutilizado no conserva la credencial del vale borrado
    assert client.get("/vales/1/credencial").status_code == 404
    assert client.get("/vales/1/firma").status_code == 200
