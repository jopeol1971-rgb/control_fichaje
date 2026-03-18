from app import app, db
from models import Usuario

with app.app_context():
    try:
        # 1. Intentamos crear las tablas en el servidor Postgres
        db.create_all()
        print("✅ Tablas creadas en PostgreSQL.")

        # 2. Verificamos si ya existe el usuario
        if not Usuario.query.filter_by(nombre="Empleado Test").first():
            nuevo_usuario = Usuario(nombre="Empleado Test", rol="empleado")
            db.session.add(nuevo_usuario)
            db.session.commit()
            print("👤 Usuario 'Empleado Test' creado con éxito.")
        else:
            print("ℹ️ El usuario de prueba ya existe en la base de datos.")
            
    except Exception as e:
        print(f"❌ Error crítico al inicializar la DB: {e}")
        print("Revisa que tu servidor PostgreSQL esté encendido y los datos del .env sean correctos.")