from app import app, db, Usuario

with app.app_context():
    # 1. Creamos las tablas
    db.create_all()

    # 2. Verificamos si ya existe el usuario para no duplicarlo
    if not Usuario.query.filter_by(nombre="Empleado Test").first():
        nuevo_usuario = Usuario(nombre="Empleado Test", rol="empleado")
        db.session.add(nuevo_usuario)
        db.session.commit()
        print("¡Usuario de prueba 'Empleado Test' creado con éxito!")
    else:
        print("El usuario ya existe.")