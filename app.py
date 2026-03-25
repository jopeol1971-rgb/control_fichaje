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
        
        # Identificamos el primer y último punto del día
        entrada_principal = next((e.timestamp for e in eventos if e.tipo == 'entrada'), None)
        salida_principal = next((e.timestamp for e in reversed(eventos) if e.tipo == 'salida'), None)
        
        # Mantenemos el cálculo de la pausa solo para información/auditoría
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
            # Si hoy no ha salido todavía, contamos hasta la hora actual
            fin_calculo = salida_principal if salida_principal else ahora
            
            # --- MODIFICACIÓN AQUÍ: La jornada es el tiempo total sin restar pausas ---
            duracion_jornada = fin_calculo - entrada_principal
            datos['total_horas'] = str(duracion_jornada).split('.')[0] 
            # -----------------------------------------------------------------------
            
            datos['entrada'] = entrada_principal
            datos['salida'] = salida_principal
            
            # Mantenemos la alerta visual por si te interesa saber si descansaron de más
            if datos['total_pausa'] > limite:
                datos['observaciones'] = f"⚠️ Pausa larga: {datos['total_pausa_str']}"
                datos['alerta'] = True
            else:
                datos['observaciones'] = f"Pausa: {datos['total_pausa_str']}"
                datos['alerta'] = False
        else:
            datos['total_horas'] = "Pendiente"
            datos['observaciones'] = "Sin entrada"
            datos['alerta'] = False
            datos['entrada'] = None
            datos['salida'] = None
            
    return resumen

# --- RUTAS DE LA APLICACIÓN ---

from werkzeug.security import check_password_hash

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        dni_ingresado = request.form.get('dni')
        password_ingresada = request.form.get('password')

        # 1. Buscamos al usuario por su DNI
        user = Usuario.query.filter_by(dni=dni_ingresado).first()

        # 2. Verificamos usuario y contraseña
        if user and check_password_hash(user.password, password_ingresada):
            session['user_id'] = user.id
            session['rol'] = user.rol
            
            flash(f'Bienvenido a Superpekes, {user.nombre}')
            
            # Independientemente del rol, enviamos a la pantalla de fichaje
            return redirect(url_for('index'))
        else:
            flash('DNI o contraseña incorrectos')
            
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
    
    # --- 1. LÓGICA DE BOLSA SEMANAL ---
    inicio_semana = ahora - timedelta(days=ahora.weekday())
    inicio_semana = inicio_semana.replace(hour=0, minute=0, second=0, microsecond=0)
    
    fichajes_semana = [f for f in fichajes_usuario if f.timestamp >= inicio_semana]
    fichajes_semana.sort(key=lambda x: x.timestamp)
    
    total_segundos_semana = 0
    temp_entrada_semana = None
    
    for f in fichajes_semana:
        if f.tipo == 'entrada':
            temp_entrada_semana = f.timestamp
        elif f.tipo == 'salida' and temp_entrada_semana:
            total_segundos_semana += (f.timestamp - temp_entrada_semana).total_seconds()
            temp_entrada_semana = None
        elif f.tipo == 'descanso' and temp_entrada_semana:
            total_segundos_semana += (f.timestamp - temp_entrada_semana).total_seconds()
            temp_entrada_semana = None
            
    if estado == 'entrada' and ultimo_fichaje:
        total_segundos_semana += (ahora - ultimo_fichaje.timestamp).total_seconds()

    horas_totales_semana = round(total_segundos_semana / 3600, 1)
    objetivo_semanal = 20 
    porc_semanal = min(int((horas_totales_semana / objetivo_semanal) * 100), 100)

    # --- 2. LÓGICA DIARIA EXISTENTE ---
    fichajes_hoy = [f for f in fichajes_usuario if f.timestamp.date() == hoy]
    fichajes_hoy.sort(key=lambda x: x.timestamp)

    segundos_pausa_cerrados = 0
    temp_inicio_pausa = None
    for f in fichajes_hoy:
        if f.tipo == 'descanso':
            temp_inicio_pausa = f.timestamp
        elif f.tipo == 'entrada' and temp_inicio_pausa:
            segundos_pausa_cerrados += int((f.timestamp - temp_inicio_pausa).total_seconds())
            temp_inicio_pausa = None

    primera_entrada_hoy = next((f for f in fichajes_hoy if f.tipo == 'entrada'), None)
    
    progreso = 0
    if primera_entrada_hoy:
        segundos_desde_inicio = int((ahora - primera_entrada_hoy.timestamp).total_seconds())
        pausa_actual = 0
        if estado == 'descanso' and ultimo_fichaje:
            pausa_actual = int((ahora - ultimo_fichaje.timestamp).total_seconds())
        
        # --- CAMBIO AQUÍ: Para que el progreso NO reste las pausas ---
        # Si quieres que la barra de progreso ignore los descansos, usa 'segundos_desde_inicio'
        # Si quieres mantener el descuento pero que el usuario vea su tiempo de pausa, deja 'segundos_trabajados'
        segundos_trabajados = segundos_desde_inicio - (segundos_pausa_cerrados + pausa_actual)
        segundos_objetivo_diario = (user.horas_contratadas / 5) * 3600
        
        if segundos_objetivo_diario > 0:
            progreso = min((segundos_trabajados / segundos_objetivo_diario) * 100, 100)

    hora_inicio_jornada = primera_entrada_hoy.timestamp.isoformat() if primera_entrada_hoy else ""
    hora_inicio_pausa = ultimo_fichaje.timestamp.isoformat() if (ultimo_fichaje and estado == 'descanso') else ""

    # --- 3. ALERTA DE OLVIDO (Informativa) ---
    if ultimo_fichaje and estado in ['entrada', 'descanso'] and ultimo_fichaje.timestamp.date() < hoy:
        alerta_olvido = True

    # --- 4. RETORNO SIN BLOQUEO ---
    return render_template('index.html', 
                           user=user, 
                           estado=estado, 
                           historial=fichajes_usuario, 
                           hora_inicio=hora_inicio_jornada,
                           hora_pausa=hora_inicio_pausa,
                           total_segundos_pausa_cerrados=segundos_pausa_cerrados,
                           alerta_olvido=alerta_olvido,
                           progreso=round(progreso, 1),
                           horas_totales_semana=horas_totales_semana,
                           porc_semanal=porc_semanal,
                           bloqueado=False)

@app.route('/fichar/<tipo>', methods=['POST'])
def registrar_fichaje(tipo):
    user_id = session.get('user_id')
    rol_actual = session.get('rol')  # Obtenemos el rol de la sesión
    ahora = datetime.now()
    ultimo = Fichaje.query.filter_by(usuario_id=user_id).order_by(Fichaje.timestamp.desc()).first()
    ultimo_tipo = ultimo.tipo if ultimo else 'salida'

    # Lógica de mensajes (se mantiene igual)
    if tipo == 'descanso' and ultimo_tipo == 'descanso':
        tipo = 'entrada'
        mensaje = "Fin de descanso. ¡A seguir!"
    elif tipo == 'descanso':
        mensaje = "Descanso iniciado."
    else:
        mensaje = f"Registro de {tipo} completado."

    # Si es una entrada y ya hay una activa del mismo día, avisamos
    if tipo == 'entrada' and ultimo_tipo == 'entrada' and ultimo.timestamp.date() == ahora.date():
        flash("Ya tienes una entrada activa para hoy.")
        return redirect(url_for('index'))

    # --- NUEVA LÓGICA DE ESTADO ---
    if rol_actual == 'admin':
        estado_final = 'aprobado'
    else:
        # Solo para empleados: si es un fichaje de un día olvidado, queda pendiente
        estado_final = 'pendiente' if (ultimo and ultimo.timestamp.date() < ahora.date()) else 'aprobado'

    # Creamos el fichaje con el estado calculado
    nuevo_fichaje = Fichaje(
        usuario_id=user_id,
        tipo=tipo,
        timestamp=ahora,
        ip_origen=request.remote_addr,
        estado=estado_final  # <--- Aplicamos la variable
    )
    
    db.session.add(nuevo_fichaje)
    db.session.commit()
    
    flash(f'{mensaje} a las {ahora.strftime("%H:%M:%S")}.')
    return redirect(url_for('index'))

@app.route('/fichaje_manual', methods=['GET', 'POST'])
def fichaje_manual():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        fecha_str = request.form.get('fecha')
        horas = request.form.get('horas', type=float)
        comentario = request.form.get('comentario')

        if not fecha_str or not horas:
            flash("⚠️ Indica fecha y horas.")
            return redirect(url_for('fichaje_manual'))

        entrada_dt = datetime.strptime(f"{fecha_str} 09:00:00", "%Y-%m-%d %H:%M:%S")
        salida_dt = entrada_dt + timedelta(hours=horas)

        try:
            # Determinamos el estado: 'aprobado' para admin, 'pendiente' para el resto
            estado_final = 'aprobado' if session.get('rol') == 'admin' else 'pendiente'

            # Creamos los fichajes con el estado calculado
            f_entrada = Fichaje(
                usuario_id=session['user_id'],
                tipo='entrada',
                timestamp=entrada_dt,
                motivo_edicion=f"Manual: {comentario}",
                estado=estado_final
            )
            f_salida = Fichaje(
                usuario_id=session['user_id'],
                tipo='salida',
                timestamp=salida_dt,
                motivo_edicion=f"Manual: {comentario}",
                estado=estado_final
            )

            db.session.add(f_entrada)
            db.session.add(f_salida)
            db.session.commit()
            
            # Mensaje personalizado según si requiere revisión o no
            if estado_final == 'aprobado':
                flash(f"✅ Horas registradas y aprobadas: {horas}h el día {fecha_str}")
            else:
                flash(f"✅ Horas enviadas a revisión: {horas}h el día {fecha_str}")
            
            return redirect(url_for('index'))
            
        except Exception as e:
            db.session.rollback()
            flash(f"❌ Error: {str(e)}")
            return redirect(url_for('fichaje_manual'))

    return render_template('fichaje_manual.html', hoy=datetime.now().strftime('%Y-%m-%d'))

from flask import request # Asegúrate de tener importado request

@app.route('/admin/panel')
@app.route('/admin/empleado/<int:user_id>')
def admin_panel(user_id=None):
    if session.get('rol') != 'admin':
        return redirect(url_for('index'))
    
    lista_empleados = Usuario.query.all()
    query = Fichaje.query
    
    # --- LÓGICA DE FILTROS ---
    # Filtro por Empleado
    empleado_seleccionado = None
    if user_id:
        query = query.filter_by(usuario_id=user_id)
        empleado_seleccionado = Usuario.query.get(user_id)

    # Filtro por Fechas
    fecha_inicio = request.args.get('desde')
    fecha_fin = request.args.get('hasta')

    if fecha_inicio:
        query = query.filter(Fichaje.timestamp >= fecha_inicio)
    if fecha_fin:
        # Añadimos ' 23:59:59' para incluir todo el día final
        query = query.filter(Fichaje.timestamp <= f"{fecha_fin} 23:59:59")
    # -------------------------

    todos_los_fichajes = query.order_by(
        Fichaje.estado.desc(), 
        Fichaje.timestamp.desc()
    ).all()
    
    return render_template('admin.html', 
                           entries=todos_los_fichajes, 
                           empleados=lista_empleados, 
                           filtro_user=empleado_seleccionado,
                           fecha_inicio=fecha_inicio,
                           fecha_fin=fecha_fin)

@app.route('/admin/exportar')
def exportar_csv():
    if session.get('rol') != 'admin':
        return redirect(url_for('login'))

    fichajes = Fichaje.query.order_by(Fichaje.timestamp.desc()).all()
    si = StringIO()
    cw = csv.writer(si)
    
    cw.writerow([
        'ID', 'Empleado', 'DNI/NIE', 'NASS', 
        'Tipo', 'Fecha', 'Hora', 'IP', 
        'Editado', 'Notas'
    ])
    
    for f in fichajes:
        cw.writerow([
            f.id, 
            f.usuario.nombre, 
            f.usuario.dni or "N/A", 
            f.usuario.nass or "N/A",
            f.tipo.capitalize(), 
            f.timestamp.strftime('%Y-%m-%d'),
            f.timestamp.strftime('%H:%M:%S'), 
            f.ip_origen,
            "SÍ" if f.editado_por_admin else "NO",
            f.motivo_edicion or ""
        ])
    
    fecha_archivo = datetime.now().strftime("%Y_%m_%d")
    return Response(
        si.getvalue(), 
        mimetype="text/csv",
        headers={"Content-disposition": f"attachment; filename=fichajes_{fecha_archivo}.csv"}
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
    # Solo el admin puede entrar aquí
    if session.get('rol') != 'admin':
        return redirect(url_for('index'))

    if request.method == 'POST':
        # 1. Recoger y limpiar datos
        nombre = request.form.get('nombre', '').strip()
        apellidos = request.form.get('apellidos', '').strip()
        dni = request.form.get('dni', '').strip().upper()  # Forzamos mayúsculas
        nass = request.form.get('nass', '').strip()
        email = request.form.get('email', '').strip().lower() # Forzamos minúsculas
        telefono = request.form.get('telefono', '').strip()
        direccion = request.form.get('direccion', '').strip()
        password_plana = request.form.get('password')
        rol = request.form.get('rol', 'empleado')
        horas = request.form.get('horas_contratadas', 40.0, type=float)

        # 2. Validación de campos obligatorios
        if not nombre or not dni or not nass or not password_plana:
            flash("⚠️ Error: Nombre, DNI, NASS y Contraseña son obligatorios.")
            return redirect(url_for('nuevo_empleado'))

        # 3. Comprobar si ya existen en la base de datos
        if Usuario.query.filter_by(dni=dni).first():
            flash(f"⚠️ El DNI {dni} ya está registrado.")
            return redirect(url_for('nuevo_empleado'))
            
        if Usuario.query.filter_by(nass=nass).first():
            flash(f"⚠️ El NASS {nass} ya pertenece a otro empleado.")
            return redirect(url_for('nuevo_empleado'))

        # 4. Intentar guardar en la base de datos
        try:
            nuevo_usuario = Usuario(
                nombre=nombre,
                apellidos=apellidos,
                dni=dni,
                nass=nass,
                email=email if email else None,
                telefono=telefono if telefono else None,
                direccion=direccion if direccion else None,
                horas_contratadas=horas,
                rol=rol
            )
            nuevo_usuario.set_password(password_plana)
            
            db.session.add(nuevo_usuario)
            db.session.commit()
            flash(f"✅ Empleado {nombre} registrado correctamente.")
            return redirect(url_for('admin_panel'))

        except Exception as e:
            db.session.rollback()
            flash(f"❌ Error al guardar en la base de datos: {str(e)}")
            return redirect(url_for('nuevo_empleado'))
            
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
        nueva_fecha_hora = request.form.get('nueva_fecha_hora')
        nuevo_motivo_admin = request.form.get('motivo')
        
        # 1. Conservamos la nota original si existe
        nota_original = fichaje.motivo_edicion if fichaje.motivo_edicion else "Sin nota inicial"
        
        # 2. Actualizamos los datos
        fichaje.timestamp = datetime.strptime(nueva_fecha_hora, '%Y-%m-%dT%H:%M')
        fichaje.editado_por_admin = True
        fichaje.estado = 'aprobado' # Forzamos la aprobación al corregir
        
        # 3. Concatenamos ambos textos para que no se pierda nada
        fichaje.motivo_edicion = f"Original: {nota_original} | Corrección Admin: {nuevo_motivo_admin}"
        
        db.session.commit()
        flash("Registro corregido y aprobado conservando historial.")
        
    return redirect(url_for('admin_panel'))

@app.route('/admin/validar_fichaje/<int:f_id>/<accion>', methods=['POST'])
def validar_fichaje(f_id, accion):
    if session.get('rol') != 'admin':
        flash("Acceso restringido.")
        return redirect(url_for('index'))
    
    fichaje = db.session.get(Fichaje, f_id)
    if not fichaje:
        flash("Fichaje no encontrado.")
        return redirect(url_for('admin_panel'))

    if accion == 'aprobar':
        fichaje.estado = 'aprobado'
        flash(f"✅ Fichaje de {fichaje.usuario.nombre} aprobado.")
        
    elif accion == 'modificar':
        # Leemos la nueva fecha/hora del formulario
        nueva_fecha_hora = request.form.get('nueva_fecha_hora')
        nuevo_motivo = request.form.get('motivo')
        
        if nueva_fecha_hora:
            # Actualizamos el timestamp y lo marcamos como aprobado y editado
            fichaje.timestamp = datetime.strptime(nueva_fecha_hora, '%Y-%m-%dT%H:%M')
            fichaje.estado = 'aprobado'
            fichaje.editado_por_admin = True
            fichaje.motivo_edicion = f"Corregido por admin: {nuevo_motivo}"
            flash(f"📝 Fichaje de {fichaje.usuario.nombre} corregido y aprobado.")
        else:
            flash("⚠️ Debes indicar una fecha y hora para modificar.")

    db.session.commit()
    return redirect(url_for('admin_panel'))

# --- ARRANQUE DE LA APLICACIÓN ---

if __name__ == '__main__':
    with app.app_context():
        # 1. Crea las tablas en Postgres con la nueva estructura (DNI, NASS, etc.)
        db.create_all()
        
        # 2. Definimos el DNI ficticio para el acceso del administrador
        admin_dni = '00000000T'
        
        # 3. Verificamos si ya existe el administrador por su DNI
        if not Usuario.query.filter_by(dni=admin_dni).first():
            # Creamos el objeto con los nuevos campos requeridos
            admin_inicial = Usuario(
                nombre='Admin',
                apellidos='Sistema',
                dni=admin_dni,          # Este será tu "usuario" en el login
                nass='000000000000',
                rol='admin',
                horas_contratadas=40.0,
                email='admin@superpekes.com'
            )
            
            # IMPORTANTE: Usamos set_password para que la clave '1234' se guarde 
            # como un hash seguro. Si pones password='1234', el login fallará.
            admin_inicial.set_password('1234')
            
            db.session.add(admin_inicial)
            db.session.commit()
            print(f"Base de datos inicializada. Admin creado (DNI: {admin_dni}, Pass: 1234)")
        else:
            print("El usuario administrador ya existe en la base de datos.")
            
    app.run(debug=True)