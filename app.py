import os
import csv
from io import StringIO
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, session, Response
from flask_sqlalchemy import SQLAlchemy
from dotenv import load_dotenv
from models import db, Usuario, Fichaje
from werkzeug.security import check_password_hash

# 1. CARGA DE CONFIGURACIÓN
load_dotenv()

app = Flask(__name__)
# Prioriza la clave del .env, si no existe usa una por defecto
app.secret_key = os.getenv('SECRET_KEY', 'clave_secreta_para_sesiones')

# 2. CONFIGURACIÓN DE POSTGRESQL
# Corrección necesaria: SQLAlchemy requiere 'postgresql://' en lugar de 'postgres://'
uri = os.getenv("DATABASE_URL")
if uri and uri.startswith("postgres://"):
    uri = uri.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = uri
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# 3. INICIALIZACIÓN
db.init_app(app)

# --- LÓGICA DE CÁLCULO ---

def calcular_horas_diarias(fichajes):
    resumen = {}
    ahora = datetime.now()
    
    for f in fichajes:
        fecha_str = f.timestamp.strftime('%Y-%m-%d')
        if fecha_str not in resumen:
            resumen[fecha_str] = {'eventos': [], 'total_pausa': timedelta(0)}
        resumen[fecha_str]['eventos'].append(f)

    for fecha, datos in resumen.items():
        eventos = sorted(datos['eventos'], key=lambda x: x.timestamp)
        
        entrada_principal = next((e.timestamp for e in eventos if e.tipo == 'entrada'), None)
        salida_principal = next((e.timestamp for e in reversed(eventos) if e.tipo == 'salida'), None)
        
        inicio_descanso = None
        for e in eventos:
            if e.tipo == 'descanso':
                inicio_descanso = e.timestamp
            elif e.tipo == 'entrada' and inicio_descanso:
                datos['total_pausa'] += (e.timestamp - inicio_descanso)
                inicio_descanso = None
        
        if inicio_descanso and fecha == ahora.strftime('%Y-%m-%d'):
            datos['total_pausa'] += (ahora - inicio_descanso)

        datos['total_pausa_str'] = str(datos['total_pausa']).split('.')[0]
        datos['pausa'] = datos['total_pausa_str']
        limite = timedelta(minutes=30)

        if entrada_principal:
            fin_calculo = salida_principal if salida_principal else ahora
            duracion_jornada = fin_calculo - entrada_principal
            horas_netas = duracion_jornada - datos['total_pausa']
            datos['total_horas'] = str(horas_netas).split('.')[0]
            datos['entrada'] = entrada_principal
            datos['salida'] = salida_principal
            
            if datos['total_pausa'] > limite:
                datos['observaciones'] = f"⚠️ Exceso pausa: {datos['total_pausa_str']} (Máx 30min)"
                datos['alerta'] = True
            else:
                datos['observaciones'] = f"Pausa: {datos['total_pausa_str']}"
                datos['alerta'] = False
        else:
            datos['total_horas'] = "Pendiente"
            datos['observaciones'] = "Sin entrada principal"
            datos['alerta'] = False
            datos['entrada'] = None
            datos['salida'] = None
            
    return resumen

# --- RUTAS DE LA APLICACIÓN ---

from werkzeug.security import check_password_hash

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        nombre = request.form.get('nombre')
        password_ingresada = request.form.get('password')

        # 1. Buscamos al usuario SOLO por su nombre
        user = Usuario.query.filter_by(nombre=nombre).first()

        # 2. Verificamos si el usuario existe Y si la contraseña coincide con el hash
        if user and check_password_hash(user.password, password_ingresada):
            session['user_id'] = user.id
            session['rol'] = user.rol  # Guardamos el rol para las rutas de admin
            flash(f'Bienvenido a Superpekes, {user.nombre}')
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
    if not user:
        session.pop('user_id', None)
        return redirect(url_for('login'))

    fichajes_usuario = Fichaje.query.filter_by(usuario_id=user.id).all()
    ultimo_fichaje = Fichaje.query.filter_by(usuario_id=user.id).order_by(Fichaje.timestamp.desc()).first()
    
    estado = ultimo_fichaje.tipo if ultimo_fichaje else 'fuera'
    alerta_olvido = False
    ahora = datetime.now()
    hoy = ahora.date()
    
    fichajes_hoy = [f for f in fichajes_usuario if f.timestamp.date() == hoy]
    fichajes_hoy.sort(key=lambda x: x.timestamp)

    # --- LÓGICA DE PAUSAS Y TIEMPO TRABAJADO ---
    segundos_pausa_cerrados = 0
    temp_inicio_pausa = None
    for f in fichajes_hoy:
        if f.tipo == 'descanso':
            temp_inicio_pausa = f.timestamp
        elif f.tipo == 'entrada' and temp_inicio_pausa:
            segundos_pausa_cerrados += int((f.timestamp - temp_inicio_pausa).total_seconds())
            temp_inicio_pausa = None

    primera_entrada_hoy = next((f for f in fichajes_hoy if f.tipo == 'entrada'), None)
    
    # --- CÁLCULO DEL PROGRESO ---
    progreso = 0
    if primera_entrada_hoy:
        # Tiempo total desde la primera entrada hasta ahora (en segundos)
        segundos_desde_inicio = int((ahora - primera_entrada_hoy.timestamp).total_seconds())
        
        # Si está actualmente en pausa, sumamos el tiempo de la pausa abierta
        pausa_actual = 0
        if estado == 'descanso' and ultimo_fichaje:
            pausa_actual = int((ahora - ultimo_fichaje.timestamp).total_seconds())
        
        # Tiempo real trabajado = Total - (Pausas cerradas + Pausa actual)
        segundos_trabajados = segundos_desde_inicio - (segundos_pausa_cerrados + pausa_actual)
        
        # Objetivo diario (Horas contratadas / 5 días a la semana) convertido a segundos
        segundos_objetivo_diario = (user.horas_contratadas / 5) * 3600
        
        if segundos_objetivo_diario > 0:
            progreso = min((segundos_trabajados / segundos_objetivo_diario) * 100, 100)

    # Variables para el template
    hora_inicio_jornada = primera_entrada_hoy.timestamp.isoformat() if primera_entrada_hoy else ""
    hora_inicio_pausa = ultimo_fichaje.timestamp.isoformat() if (ultimo_fichaje and estado == 'descanso') else ""

    if ultimo_fichaje and estado == 'entrada' and ultimo_fichaje.timestamp.date() < hoy:
        alerta_olvido = True

    return render_template('index.html', 
                           user=user, 
                           estado=estado, 
                           historial=fichajes_usuario, 
                           hora_inicio=hora_inicio_jornada,
                           hora_pausa=hora_inicio_pausa,
                           total_segundos_pausa_cerrados=segundos_pausa_cerrados,
                           alerta_olvido=alerta_olvido,
                           progreso=round(progreso, 1)) # Enviamos el progreso redondeado

@app.route('/fichar/<tipo>', methods=['POST'])
def registrar_fichaje(tipo):
    user_id = session.get('user_id')
    ultimo = Fichaje.query.filter_by(usuario_id=user_id).order_by(Fichaje.timestamp.desc()).first()
    ultimo_tipo = ultimo.tipo if ultimo else 'salida'

    if tipo == 'descanso' and ultimo_tipo == 'descanso':
        tipo = 'entrada'
        mensaje = "Fin de descanso. ¡A seguir!"
    elif tipo == 'descanso':
        mensaje = "Descanso iniciado."
    else:
        mensaje = f"Registro de {tipo} completado."

    if tipo == 'entrada' and ultimo_tipo == 'entrada':
        flash("Ya tienes una entrada activa.")
        return redirect(url_for('index'))

    nuevo_fichaje = Fichaje(
        usuario_id=user_id,
        tipo=tipo,
        timestamp=datetime.now(),
        ip_origen=request.remote_addr
    )
    db.session.add(nuevo_fichaje)
    db.session.commit()
    
    flash(f'{mensaje} a las {datetime.now().strftime("%H:%M:%S")}.')
    return redirect(url_for('index'))

@app.route('/admin/panel')
def admin_panel():
    user_id = session.get('user_id')
    user = db.session.get(Usuario, user_id)
    if not user or user.rol != 'admin':
        return redirect(url_for('index'))
    todos_los_fichajes = Fichaje.query.order_by(Fichaje.timestamp.desc()).all()
    return render_template('admin.html', entries=todos_los_fichajes)

@app.route('/admin/exportar')
def exportar_csv():
    user_id = session.get('user_id')
    user = db.session.get(Usuario, user_id)
    if not user or user.rol != 'admin':
        return redirect(url_for('login'))

    fichajes = Fichaje.query.all()
    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(['ID', 'Usuario', 'Tipo', 'Fecha y Hora', 'IP Origen', 'Editado por Admin', 'Motivo de Corrección'])
    
    for f in fichajes:
        cw.writerow([
            f.id, f.usuario.nombre, f.tipo, 
            f.timestamp.strftime('%Y-%m-%d %H:%M:%S'), f.ip_origen,
            "SÍ" if f.editado_por_admin else "NO",
            f.motivo_edicion if f.motivo_edicion else ""
        ])
    
    return Response(si.getvalue(), mimetype="text/csv",
                    headers={"Content-disposition": "attachment; filename=informe_fichajes.csv"})

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
    user_id = session.get('user_id')
    user = db.session.get(Usuario, user_id)
    
    if not user or user.rol != 'admin':
        return redirect(url_for('index'))

    if request.method == 'POST':
        nombre = request.form.get('nombre')
        password_plana = request.form.get('password')
        rol = request.form.get('rol')
        # Obtenemos las horas del formulario (por defecto 40 si viene vacío)
        horas = request.form.get('horas_contratadas', 40, type=float)

        if Usuario.query.filter_by(nombre=nombre).first():
            flash("El nombre de usuario ya existe.")
        else:
            # Creamos el objeto usuario
            nuevo_usuario = Usuario(
                nombre=nombre, 
                rol=rol, 
                horas_contratadas=horas
            )
            # USAMOS EL MÉTODO DEL MODELO: Cifra la contraseña antes de guardar
            nuevo_usuario.set_password(password_plana)
            
            db.session.add(nuevo_usuario)
            db.session.commit()
            
            flash(f"Empleado {nombre} creado con {horas}h contratadas.")
            return redirect(url_for('admin_panel'))
            
    return render_template('nuevo_empleado.html')

@app.route('/admin/corregir_fichaje', methods=['POST'])
def corregir_fichaje():
    user_id = session.get('user_id')
    user = db.session.get(Usuario, user_id)
    if not user or user.rol != 'admin':
        flash("Acceso denegado.")
        return redirect(url_for('index'))

    f_id = request.form.get('fichaje_id')
    fichaje = db.session.get(Fichaje, f_id)
    if fichaje:
        fichaje.timestamp = datetime.strptime(request.form.get('nueva_fecha_hora'), '%Y-%m-%dT%H:%M')
        fichaje.editado_por_admin = True
        fichaje.motivo_edicion = request.form.get('motivo')
        db.session.commit()
        flash("Registro corregido con éxito.")
    return redirect(url_for('admin_panel'))

# --- ARRANQUE DE LA APLICACIÓN ---

if __name__ == '__main__':
    with app.app_context():
        # Crea tablas en Postgres si no existen
        db.create_all()
        # Semilla inicial para el administrador
        if not Usuario.query.filter_by(nombre='admin').first():
            db.session.add(Usuario(nombre='admin', password='1234', rol='admin'))
            db.session.commit()
            print("Base de datos PostgreSQL inicializada con usuario admin.")
            
    app.run(debug=True)