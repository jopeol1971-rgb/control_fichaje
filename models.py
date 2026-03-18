from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

db = SQLAlchemy()

class Usuario(db.Model):
    __tablename__ = 'usuario'
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), unique=True, nullable=False)
    # Aumentamos a 255 para guardar el hash seguro
    password = db.Column(db.String(255), nullable=False)
    rol = db.Column(db.String(20), default='empleado')
    # Horas semanales o mensuales según prefieras (ej. 40.0)
    horas_contratadas = db.Column(db.Float, nullable=True, default=40.0)

    def set_password(self, password_plana):
        """Crea un hash irreversible de la contraseña."""
        self.password = generate_password_hash(password_plana)

    def check_password(self, password_plana):
        """Compara la contraseña ingresada con el hash guardado."""
        return check_password_hash(self.password, password_plana)

class Fichaje(db.Model):
    __tablename__ = 'fichaje'
    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    tipo = db.Column(db.String(20), nullable=False) # 'entrada', 'salida', 'descanso'
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    ip_origen = db.Column(db.String(45))
    
    # --- CAMPOS PARA AUDITORÍA ---
    editado_por_admin = db.Column(db.Boolean, default=False)
    motivo_edicion = db.Column(db.String(255), nullable=True)
    
    # Relación para acceder a user.fichajes o fichaje.usuario
    usuario = db.relationship('Usuario', backref=db.backref('fichajes', lazy=True))