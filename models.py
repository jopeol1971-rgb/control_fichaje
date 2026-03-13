from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class Usuario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    password = db.Column(db.String(80), nullable=False, default="1234") # Nueva columna
    rol = db.Column(db.String(20), default='empleado')

class Fichaje(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'))
    tipo = db.Column(db.String(20))
    timestamp = db.Column(db.DateTime)
    ip_origen = db.Column(db.String(45))
    
    # Relación para facilitar la lectura en el panel admin

    usuario = db.relationship('Usuario', backref=db.backref('fichajes', lazy=True))