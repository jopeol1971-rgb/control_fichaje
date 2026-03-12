from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'clave_secreta_para_sesiones' # Cambia esto en producción
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///fichajes.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# --- MODELOS (Simplificados para app.py) ---
class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    rol = db.Column(db.String(20), default='empleado') # 'admin' o 'empleado'

class Fichaje(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'))
    tipo = db.Column(db.String(20)) # 'entrada', 'salida'
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    ip_origen = db.Column(db.String(45))

# --- RUTAS ---

@app.route('/')
def index():
    # Simulación de usuario logueado (esto se haría con Flask-Login)
    return render_template('index.html')

@app.route('/fichar/<tipo>', methods=['POST'])
def registrar_fichaje(tipo):
    # 1. Obtener ID del usuario (de la sesión)
    user_id = session.get('user_id', 1) # Por ahora forzamos ID 1
    
    # 2. Capturar IP (Requisito de trazabilidad)
    ip = request.remote_addr
    
    # 3. Crear registro con la hora del servidor (Garantía legal)
    nuevo_fichaje = Fichaje(
        usuario_id=user_id,
        tipo=tipo,
        timestamp=datetime.now(), 
        ip_origen=ip
    )
    
    db.session.add(nuevo_fichaje)
    db.session.commit()
    
    flash(f'Registro de {tipo} completado con éxito.')
    return redirect(url_for('index'))

@app.route('/admin/panel')
def admin_panel():
    # Solo accesible si el rol es 'admin'
    todos_los_fichajes = Fichaje.query.all()
    return render_template('admin.html', fichajes=todos_los_fichajes)

if __name__ == '__main__':
    with app.app_context():
        db.create_all() # Crea la base de datos si no existe
    app.run(debug=True)