import io

import pytest

from inventario import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "test.db")})
    return app.test_client()


def nuevo(client, codigo="A-1", stock=10, minimo=2):
    return client.post("/productos/nuevo", data={
        "codigo": codigo, "nombre": f"Producto {codigo}", "categoria": "General",
        "stock_inicial": stock, "stock_minimo": minimo, "responsable": "Ana",
    }, follow_redirects=True)


def mover(client, pid, tipo, cantidad):
    return client.post(f"/productos/{pid}/movimiento", data={
        "tipo": tipo, "cantidad": cantidad, "motivo": "prueba", "responsable": "Ana",
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
