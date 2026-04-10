from app import app, db
from models import Usuario, Fichaje # Importa tus modelos

with app.app_context():
    try:
        # 1. ELIMINAMOS LAS TABLAS ACTUALES (Solo en tu DB local)
        db.drop_all() 
        print("🗑️ Tablas antiguas eliminadas de la DB local.")

        # 2. LAS CREAMOS DE NUEVO (Con las columnas nuevas como created_at)
        db.create_all()
        print("✅ Tablas creadas con la estructura actualizada.")

        # 3. CREAMOS AL ADMIN (Asegúrate de que los campos coincidan con tu modelo)
        if not Usuario.query.filter_by(dni="00000000T").first():
            admin = Usuario(
                nombre="Admin", 
                dni="00000000T", 
                rol="admin",
                nass="000000000000",
                horas_contratadas=40.0
            )
            admin.set_password("1234")
            db.session.add(admin)
            db.session.commit()
            print("👤 Admin creado con éxito.")
            
    except Exception as e:
        print(f"❌ Error: {e}")