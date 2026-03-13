from flask import Flask, render_template, request, redirect, url_for, flash, session, Response # Añadido Response
from datetime import datetime, timedelta
from models import db, Usuario, Fichaje
import csv # Movido arriba
from io import StringIO # Movido arriba

app = Flask(__name__)
app.secret_key = 'clave_secreta_para_sesiones'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///fichajes.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

def calcular_horas_diarias(fichajes):
    resumen = {}
    
    # Agrupamos fichajes por fecha (día/mes/año)
    for f in fichajes:
        fecha_str = f.timestamp.strftime('%Y-%m-%d')
        if fecha_str not in resumen:
            resumen[fecha_str] = {'entrada': None, 'salida': None, 'pausa': None, 'total': timedelta(0)}
        
        # Guardamos el primer evento de cada tipo para simplificar
        if f.tipo == 'entrada' and not resumen[fecha_str]['entrada']:
            resumen[fecha_str]['entrada'] = f.timestamp
        elif f.tipo == 'descanso' and not resumen[fecha_str]['pausa']:
            resumen[fecha_str]['pausa'] = f.timestamp
        elif f.tipo == 'salida' and not resumen[fecha_str]['salida']:
            resumen[fecha_str]['salida'] = f.timestamp

    # Calculamos la duración
    for fecha, datos in resumen.items():
        if datos['entrada'] and datos['salida']:
            duracion = datos['salida'] - datos['entrada']
            # Si hubo pausa, podríamos restar tiempo (aquí lo simplificamos a total entrada-salida)
            resumen[fecha]['total_horas'] = str(duracion).split('.')[0] # Formato HH:MM:SS
        else:
            resumen[fecha]['total_horas'] = "Pendiente"
            
    return resumen

# --- RUTAS ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        nombre = request.form.get('nombre')
        password = request.form.get('password')
        
        user = Usuario.query.filter_by(nombre=nombre, password=password).first()
        
        if user:
            session['user_id'] = user.id
            flash(f'Bienvenido, {user.nombre}')
            return redirect(url_for('index'))
        else:
            flash('Usuario o contraseña incorrectos')
            
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('login'))

@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    user = db.session.get(Usuario, session['user_id']) 
    
    # 1. Obtenemos todos los fichajes del usuario para el calendario
    fichajes_usuario = Fichaje.query.filter_by(usuario_id=user.id).all()
    
    # 2. Buscamos el último fichaje para el estado de los botones
    ultimo_fichaje = Fichaje.query.filter_by(usuario_id=user.id).order_by(Fichaje.timestamp.desc()).first()
    estado = ultimo_fichaje.tipo if ultimo_fichaje else 'fuera'
    
    return render_template('index.html', user=user, estado=estado, historial=fichajes_usuario)

@app.route('/fichar/<tipo>', methods=['POST'])
def registrar_fichaje(tipo):
    user_id = session.get('user_id', 1)
    ultimo = Fichaje.query.filter_by(usuario_id=user_id).order_by(Fichaje.timestamp.desc()).first()
    ultimo_tipo = ultimo.tipo if ultimo else 'salida'

    if tipo == 'entrada' and ultimo_tipo == 'entrada':
        flash("Ya has registrado una entrada.")
        return redirect(url_for('index'))
    
    if tipo in ['salida', 'descanso'] and (not ultimo or ultimo_tipo == 'salida'):
        flash(f"No puedes registrar {tipo} sin haber iniciado jornada.")
        return redirect(url_for('index'))

    nuevo_fichaje = Fichaje(
        usuario_id=user_id,
        tipo=tipo,
        timestamp=datetime.now(),
        ip_origen=request.remote_addr
    )
    db.session.add(nuevo_fichaje)
    db.session.commit()
    
    hora_fichaje = datetime.now().strftime("%H:%M:%S")
    flash(f'Registro de {tipo} completado con éxito a las {hora_fichaje}.')
    return redirect(url_for('index'))

@app.route('/admin/panel')
def admin_panel():
    # Solo permitimos el acceso si el usuario es admin (opcional, pero recomendado)
    user_id = session.get('user_id')
    user = db.session.get(Usuario, user_id)
    if not user or user.rol != 'admin':
        flash("Acceso denegado: se requieren permisos de administrador.")
        return redirect(url_for('index'))

    # Recuperamos todos los fichajes de todos los usuarios
    todos_los_fichajes = Fichaje.query.order_by(Fichaje.timestamp.desc()).all()
    return render_template('admin.html', entries=todos_los_fichajes)

# --- LA NUEVA RUTA DEBE IR AQUÍ (ANTES DEL APP.RUN) ---
@app.route('/admin/exportar')
def exportar_csv():
    fichajes = Fichaje.query.all()
    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(['ID', 'Usuario', 'Tipo', 'Fecha y Hora', 'IP'])
    for f in fichajes:
        cw.writerow([f.id, f.usuario.nombre, f.tipo, f.timestamp.strftime('%Y-%m-%d %H:%M:%S'), f.ip_origen])
    
    output = si.getvalue()
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-disposition": "attachment; filename=fichajes_jornada.csv"}
    )
@app.route('/admin/informe')
def admin_informe():
    user_id = session.get('user_id')
    user = db.session.get(Usuario, user_id)
    if not user or user.rol != 'admin':
        return redirect(url_for('index'))

    usuarios = Usuario.query.all()
    informe_final = []

    for u in usuarios:
        fichajes_u = Fichaje.query.filter_by(usuario_id=u.id).order_by(Fichaje.timestamp.asc()).all()
        horas_dia = calcular_horas_diarias(fichajes_u)
        informe_final.append({'nombre': u.nombre, 'dias': horas_dia})

    return render_template('informe.html', informe=informe_final)

@app.route('/admin/nuevo_empleado', methods=['GET', 'POST'])
def nuevo_empleado():
    # Seguridad: solo admin entra aquí
    user_id = session.get('user_id')
    user = Usuario.query.get(user_id)
    if not user or user.rol != 'admin':
        flash("Acceso denegado.")
        return redirect(url_for('index'))

    if request.method == 'POST':
        nombre = request.form.get('nombre')
        password = request.form.get('password')
        rol = request.form.get('rol')

        # Verificar si el nombre ya existe
        existente = Usuario.query.filter_by(nombre=nombre).first()
        if existente:
            flash("El nombre de usuario ya existe.")
        else:
            nuevo_user = Usuario(nombre=nombre, password=password, rol=rol)
            db.session.add(nuevo_user)
            db.session.commit()
            flash(f"Empleado {nombre} creado con éxito.")
            return redirect(url_for('admin_panel'))

    return render_template('nuevo_empleado.html')

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        
        # Esto buscará al usuario o lo creará si no existe
        admin = Usuario.query.filter_by(nombre='admin').first()
        if not admin:
            admin = Usuario(nombre='admin', password='1234', rol='admin')
            db.session.add(admin)
        else:
            # Si ya existe, nos aseguramos de que tenga la contraseña puesta
            admin.password = '1234'
            
        db.session.commit()
        print(">>> Usuario listo: nombre: admin | password: 1234")

    app.run(debug=True)