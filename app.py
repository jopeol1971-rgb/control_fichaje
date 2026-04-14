# 1. Librerías del sistema (Standard Library)
import io
import os
import csv
from datetime import datetime, timedelta

# 2. Librerías de terceros (Flask y extensiones)
from dotenv import load_dotenv
from flask import (
    Flask, render_template, request, redirect, 
    url_for, flash, session, Response, send_file
)
from flask_sqlalchemy import SQLAlchemy
# Añadida generate_password_hash para el registro de usuarios
from werkzeug.security import check_password_hash, generate_password_hash

# 3. Librerías de generación de PDF (ReportLab)
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
# Añadido ParagraphStyle para evitar el error 'name not defined'
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle 
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

# 4. Módulos locales del proyecto
from models import db, Usuario, Fichaje, InformeMensual

def formatear_segundos_a_hhmm(segundos):
    """Convierte segundos a formato string HH:MM"""
    if not segundos:
        return "00:00"
    horas = int(segundos // 3600)
    minutos = int((segundos % 3600) // 60)
    return f"{horas:02d}:{minutos:02d}"

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
            resumen[fecha_str] = {
                'eventos': [], 
                'total_pausa': timedelta(0),
                'segundos_netos': 0  # Inicializamos para evitar errores en el informe
            }
        resumen[fecha_str]['eventos'].append(f)

    for fecha, datos in resumen.items():
        eventos = sorted(datos['eventos'], key=lambda x: x.timestamp)
        
        entrada_principal = next((e.timestamp for e in eventos if e.tipo == 'entrada'), None)
        salida_principal = next((e.timestamp for e in reversed(eventos) if e.tipo == 'salida'), None)
        
        # Cálculo de pausas
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
        limite_pausa = timedelta(minutes=30)

        if entrada_principal:
            fin_calculo = salida_principal if salida_principal else ahora
            duracion_jornada = fin_calculo - entrada_principal
            
            # GUARDAR SEGUNDOS (Vital para sumatorios de informes)
            datos['segundos_netos'] = int(duracion_jornada.total_seconds())
            datos['total_horas'] = str(duracion_jornada).split('.')[0] 
            
            datos['entrada'] = entrada_principal
            datos['salida'] = salida_principal
            
            # Alertas y observaciones
            if datos['total_pausa'] > limite_pausa:
                datos['observaciones'] = f"⚠️ Pausa larga: {datos['total_pausa_str']}"
                datos['alerta'] = True
            else:
                datos['observaciones'] = f"Pausa: {datos['total_pausa_str']}"
                datos['alerta'] = False
        else:
            datos['total_horas'] = "00:00:00"
            datos['segundos_netos'] = 0
            datos['observaciones'] = "Sin entrada"
            datos['alerta'] = False
            datos['entrada'] = None
            datos['salida'] = None
            
    return resumen

def generar_pdf_logic(usuario, jornadas, total_periodo, rango):
    buffer = io.BytesIO()
    # 1. Configuración del documento con márgenes optimizados
    doc = SimpleDocTemplate(
        buffer, 
        pagesize=A4, 
        rightMargin=25, 
        leftMargin=25, 
        topMargin=30, 
        bottomMargin=30
    )
    elements = []
    styles = getSampleStyleSheet()
    
    # 2. Definición de estilos mejorados
    estilo_titulo = ParagraphStyle(
        'CustomTitle', 
        parent=styles['Heading1'], 
        alignment=1, 
        fontSize=18, 
        spaceAfter=25,
        textColor=colors.black
    )
    estilo_empresa = ParagraphStyle(
        'EmpresaStyle', 
        parent=styles['Normal'], 
        fontSize=10, 
        leading=14 # Más espacio entre líneas de cabecera
    )
    estilo_firma = ParagraphStyle(
        'FirmaStyle', 
        parent=styles['Normal'], 
        fontSize=9, 
        alignment=1, 
        leading=12
    )

    # --- 1. CABECERA: DATOS DE LA EMPRESA ---
    datos_empresa = [
        [Paragraph("<b>EMPRESA:</b> SUPERPEKES EVENTOS S.L.", estilo_empresa), 
         Paragraph("<b>CIF:</b> B-12345678", estilo_empresa)],
        [Paragraph("<b>CENTRO DE TRABAJO:</b> Calle del Daniels 123, Bezana", estilo_empresa), ""]
    ]
    # Tabla invisible para alinear datos de empresa y CIF a los extremos
    tabla_empresa = Table(datos_empresa, colWidths=[380, 160])
    tabla_empresa.setStyle(TableStyle([('VALIGN', (0,0), (-1,-1), 'TOP')]))
    elements.append(tabla_empresa)
    elements.append(Spacer(1, 20))

    # --- 2. TÍTULO Y DATOS DEL TRABAJADOR ---
    elements.append(Paragraph(f"REGISTRO MENSUAL DE JORNADA", estilo_titulo))
    
    datos_empleado = [
        f"<b>TRABAJADOR:</b> {usuario.nombre} {usuario.apellidos}",
        f"<b>DNI:</b> {usuario.dni}  |  <b>NASS:</b> {usuario.nass}",
        f"<b>PERIODO:</b> {rango['desde']} a {rango['hasta']}"
    ]
    
    for linea in datos_empleado:
        elements.append(Paragraph(linea, styles['Normal']))
    elements.append(Spacer(1, 20))

    # --- 3. TABLA DE REGISTROS (Encuadre Profesional) ---
    data = [['Fecha', 'Entrada', 'Salida', 'Total Horas', 'Firma / Obs.']]
    
    fechas_ordenadas = sorted(jornadas.keys(), key=lambda x: datetime.strptime(x, '%d/%m/%Y'))
    
    for fecha in fechas_ordenadas:
        d = jornadas[fecha]
        entrada = d['entrada'].strftime('%H:%M:%S') if d['entrada'] else '-'
        salida = d['salida'].strftime('%H:%M:%S') if d['salida'] else '-'
        total = f"{d['total']} h"
        data.append([fecha, entrada, salida, total, ""])

    # Ajuste de columnas: 540 puntos totales aproximadamente
    col_widths = [75, 75, 75, 75, 240] 
    
    tabla_fichajes = Table(data, colWidths=col_widths)
    tabla_fichajes.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#5e5ce6')), # Tu color corporativo
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'), # Centrado vertical perfecto
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.whitesmoke, colors.white]) # Filas alternas
    ]))
    elements.append(tabla_fichajes)

    # --- 4. RESUMEN Y FIRMAS (Centradas) ---
    elements.append(Spacer(1, 15))
    elements.append(Paragraph(f"<b>CÓMPUTO TOTAL DEL PERIODO:</b> {total_periodo} horas", styles['Normal']))
    elements.append(Spacer(1, 50)) # Espacio generoso antes de las firmas

    data_firmas = [
        [Paragraph("<b>FIRMA DE LA EMPRESA (Sello)</b>", estilo_firma), 
         Paragraph("<b>FIRMA DEL TRABAJADOR</b>", estilo_firma)],
        ["", ""], 
        ["___________________________", "___________________________"],
        [Paragraph("Fdo: Responsable de Superpekes", estilo_firma), 
         Paragraph(f"Fdo: {usuario.nombre} {usuario.apellidos}", estilo_firma)]
    ]
    
    tabla_firmas = Table(data_firmas, colWidths=[270, 270])
    tabla_firmas.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    elements.append(tabla_firmas)

    # Construir PDF
    doc.build(elements)
    buffer.seek(0)
    return buffer

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
    objetivo_semanal = user.horas_contratadas
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
    rol_actual = session.get('rol')
    ahora = datetime.now()
    
    # Obtenemos el último registro del usuario
    ultimo = Fichaje.query.filter_by(usuario_id=user_id).order_by(Fichaje.timestamp.desc()).first()
    ultimo_tipo = ultimo.tipo if ultimo else 'salida'

    # 1. Lógica de mensajes y alternancia de descanso
    if tipo == 'descanso' and ultimo_tipo == 'descanso':
        tipo = 'entrada'
        mensaje = "Fin de descanso. ¡A seguir!"
    elif tipo == 'descanso':
        mensaje = "Descanso iniciado."
    else:
        mensaje = f"Registro de {tipo} completado."

    # 2. Evitar duplicados de entrada en el mismo día
    if tipo == 'entrada' and ultimo_tipo == 'entrada' and ultimo.timestamp.date() == ahora.date():
        flash("Ya tienes una entrada activa para hoy.")
        return redirect(url_for('index'))

    # 3. LÓGICA DE ESTADO CORREGIDA
    if rol_actual == 'admin':
        estado_final = 'aprobado'
    else:
        # Solo queda 'pendiente' si el último fichaje es de un día anterior 
        # Y además se quedó en estado abierto ('entrada' o 'descanso')
        olvido_dia_anterior = ultimo and ultimo.timestamp.date() < ahora.date()
        sesion_abierta = ultimo_tipo in ['entrada', 'descanso']
        
        if olvido_dia_anterior and sesion_abierta:
            estado_final = 'pendiente'
        else:
            estado_final = 'aprobado'

    # 4. Creación del registro
    try:
        nuevo_fichaje = Fichaje(
            usuario_id=user_id,
            tipo=tipo,
            timestamp=ahora,
            ip_origen=request.remote_addr,
            estado=estado_final
        )
        
        db.session.add(nuevo_fichaje)
        db.session.commit()
        flash(f'{mensaje} a las {ahora.strftime("%H:%M:%S")}.')
        
    except Exception as e:
        db.session.rollback()
        flash(f"Error al registrar: {str(e)}")
        
    return redirect(url_for('index'))
@app.route('/admin/crear_fichaje', methods=['POST'])
def admin_crear_fichaje():
    if session.get('rol') != 'admin':
        return redirect(url_for('index'))
    
    user_id = request.form.get('user_id')
    tipo = request.form.get('tipo') # 'salida' o 'descanso'
    fecha_hora = request.form.get('fecha_hora') # Formato 'YYYY-MM-DDTHH:MM'
    
    try:
        nuevo_fichaje = Fichaje(
            usuario_id=user_id,
            tipo=tipo,
            timestamp=datetime.strptime(fecha_hora, '%Y-%m-%dT%H:%M'),
            estado='aprobado',
            motivo_edicion=f"Añadido por Admin: Registro faltante ({tipo})"
        )
        db.session.add(nuevo_fichaje)
        db.session.commit()
        flash(f"✅ {tipo.capitalize()} añadido correctamente.")
    except Exception as e:
        flash(f"❌ Error: {str(e)}")
        
    return redirect(url_for('admin_panel'))

@app.route('/fichaje_manual', methods=['GET', 'POST'])
def fichaje_manual():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        fecha_str = request.form.get('fecha')
        h_entrada = request.form.get('hora_entrada') # Nuevo
        h_salida = request.form.get('hora_salida')   # Nuevo
        comentario = request.form.get('comentario')

        if not fecha_str or not h_entrada or not h_salida:
            flash("⚠️ Indica fecha, hora de entrada y salida.")
            return redirect(url_for('fichaje_manual'))

        try:
            # Combinamos la fecha con las horas elegidas
            entrada_dt = datetime.strptime(f"{fecha_str} {h_entrada}", "%Y-%m-%d %H:%M")
            salida_dt = datetime.strptime(f"{fecha_str} {h_salida}", "%Y-%m-%d %H:%M")

            # Validación: que la salida no sea anterior a la entrada
            if salida_dt <= entrada_dt:
                flash("❌ La hora de salida debe ser posterior a la de entrada.")
                return redirect(url_for('fichaje_manual'))

            estado_final = 'aprobado' if session.get('rol') == 'admin' else 'pendiente'

            # Creamos los registros
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
            
            flash(f"✅ Horario registrado: {h_entrada} a {h_salida}")
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
    if session.get('rol') != 'admin': return redirect(url_for('login'))

    u_id = request.args.get('usuario_id')
    desde = request.args.get('desde')
    hasta = request.args.get('hasta')

    # Validación preventiva
    if not u_id or not desde or not hasta:
        flash("⚠️ Faltan filtros (empleado, fecha inicio o fin) para exportar.")
        return redirect(url_for('admin_panel'))

    query = Fichaje.query
    query = query.filter_by(usuario_id=u_id)
    query = query.filter(Fichaje.timestamp >= desde)
    query = query.filter(Fichaje.timestamp <= f"{hasta} 23:59:59")

    fichajes = query.order_by(Fichaje.timestamp.asc()).all()
    
    if not fichajes:
        flash("No hay registros para este empleado en las fechas seleccionadas.")
        return redirect(url_for('admin_panel'))
    
    output = io.StringIO() 
    writer = csv.writer(output)
    writer.writerow(['ID_EMPLEADO', 'NOMBRE', 'DNI_NIE', 'NASS', 'TIPO', 'FECHA', 'HORA', 'ESTADO'])
    
    for f in fichajes:
        writer.writerow([
            f.usuario_id, 
            f"{f.usuario.nombre} {f.usuario.apellidos or ''}", 
            f.usuario.dni, 
            f.usuario.nass or "N/A",
            f.tipo.upper(), 
            f.timestamp.strftime('%d/%m/%Y'),
            f.timestamp.strftime('%H:%M:%S'), 
            f.estado.upper()
        ])
    
    return Response(
        output.getvalue(), 
        mimetype="text/csv",
        headers={"Content-disposition": "attachment; filename=registro_jornada.csv"}
    )

@app.route('/exportar_pdf')
def exportar_pdf():
    if session.get('rol') != 'admin': return redirect(url_for('login'))
    
    u_id = request.args.get('usuario_id')
    desde = request.args.get('desde')
    hasta = request.args.get('hasta')

    if not u_id or not desde or not hasta:
        flash("⚠️ Selecciona empleado y rango de fechas.")
        return redirect(url_for('admin_panel'))
    
    # 1. Obtenemos el usuario
    usuario = db.session.get(Usuario, u_id)
    if not usuario:
        flash("Empleado no encontrado.")
        return redirect(url_for('admin_panel'))

    # 2. Obtenemos los fichajes
    fichajes = Fichaje.query.filter_by(usuario_id=u_id).filter(
        Fichaje.timestamp >= desde, 
        Fichaje.timestamp <= f"{hasta} 23:59:59"
    ).order_by(Fichaje.timestamp.asc()).all()

    if not fichajes:
        flash("Sin datos para generar el PDF en estas fechas.")
        return redirect(url_for('admin_panel'))

    # 3. Lógica de agrupación por día
    jornadas = {}
    for f in fichajes:
        fecha_str = f.timestamp.strftime('%d/%m/%Y')
        if fecha_str not in jornadas:
            jornadas[fecha_str] = {'entrada': None, 'salida': None, 'total': 0}
        
        # Guardamos la primera entrada y la última salida del día
        if f.tipo == 'entrada' and not jornadas[fecha_str]['entrada']:
            jornadas[fecha_str]['entrada'] = f.timestamp
        elif f.tipo == 'salida':
            jornadas[fecha_str]['salida'] = f.timestamp

    # 4. Cálculo de horas
    total_periodo = 0
    for fecha in jornadas:
        datos = jornadas[fecha]
        if datos['entrada'] and datos['salida']:
            diff = datos['salida'] - datos['entrada']
            horas = diff.total_seconds() / 3600
            jornadas[fecha]['total'] = round(horas, 2)
            total_periodo += horas

    # 5. Generación del archivo con la lógica actualizada de ReportLab
    try:
        pdf_buffer = generar_pdf_logic(
            usuario=usuario, 
            jornadas=jornadas, 
            total_periodo=round(total_periodo, 2),
            rango={'desde': desde, 'hasta': hasta}
        )
        
        nombre_archivo = f"Registro_{usuario.dni}_{desde}.pdf"
        return send_file(
            pdf_buffer, 
            mimetype='application/pdf', 
            as_attachment=True, 
            download_name=nombre_archivo
        )
    except Exception as e:
        flash(f"Error generando PDF: {str(e)}")
        return redirect(url_for('admin_panel'))

@app.route('/admin/informe')
def admin_informe():
    if session.get('rol') != 'admin':
        return redirect(url_for('index'))

    # 1. CAPTURAR FILTROS (ID, Nombre y Fechas)
    u_id = request.args.get('usuario_id')
    nombre_buscado = request.args.get('nombre_empleado', '').strip()
    fecha_inicio = request.args.get('desde')
    fecha_fin = request.args.get('hasta')

    # 2. FILTRAR USUARIOS
    query_usuarios = Usuario.query
    
    if u_id:
        # Si tenemos ID, vamos directos al usuario
        query_usuarios = query_usuarios.filter(Usuario.id == u_id)
    elif nombre_buscado:
        # Si no hay ID pero sí texto, buscamos por nombre
        query_usuarios = query_usuarios.filter(
            (Usuario.nombre.ilike(f"%{nombre_buscado}%")) | 
            (Usuario.apellidos.ilike(f"%{nombre_buscado}%"))
        )
    
    usuarios = query_usuarios.all()
    informe_final = []
    ahora = datetime.now()

    # 3. PROCESAR DATOS POR USUARIO
    for u in usuarios:
        query_fichajes = Fichaje.query.filter_by(usuario_id=u.id)
        
        if fecha_inicio:
            query_fichajes = query_fichajes.filter(Fichaje.timestamp >= fecha_inicio)
        if fecha_fin:
            query_fichajes = query_fichajes.filter(Fichaje.timestamp <= f"{fecha_fin} 23:59:59")
            
        fichajes_u = query_fichajes.order_by(Fichaje.timestamp.asc()).all()
        
        if not fichajes_u:
            continue

        horas_dia = calcular_horas_diarias(fichajes_u)
        
        seg_semana = 0
        seg_mes = 0

        for fecha_str, datos in horas_dia.items():
            try:
                f_dt = datetime.strptime(fecha_str, '%Y-%m-%d')
                s_netos = datos.get('segundos_netos', 0)
                
                # Sumatorios (Se mantienen los del mes/semana actual según lógica previa)
                if f_dt.month == ahora.month:
                    seg_mes += s_netos
                if f_dt.isocalendar()[1] == ahora.isocalendar()[1]:
                    seg_semana += s_netos
            except:
                continue

        informe_final.append({
            'id': u.id,
            'nombre': f"{u.nombre} {u.apellidos}", 
            'dias': horas_dia,
            'total_semanal': formatear_segundos_a_hhmm(seg_semana),
            'total_mensual': formatear_segundos_a_hhmm(seg_mes)
        })

    return render_template('informe.html', 
                           informe=informe_final, 
                           empleados=Usuario.query.all(),
                           nombre_buscado=nombre_buscado,
                           usuario_id=u_id,
                           fecha_inicio=fecha_inicio,
                           fecha_fin=fecha_fin,
                           now=datetime.now())

@app.route('/admin/cerrar_mes', methods=['POST'])
def cerrar_mes():
    if session.get('rol') != 'admin':
        return redirect(url_for('index'))

    u_id = request.form.get('usuario_id')
    mes = int(request.form.get('mes'))
    anio = int(request.form.get('anio'))
    
    # En lugar de confiar ciegamente en el form, 
    # podrías recalcular o validar aquí los segundos_netos
    horas_recibidas = float(request.form.get('horas_totales'))

    existente = InformeMensual.query.filter_by(usuario_id=u_id, mes=mes, anio=anio).first()
    if existente:
        flash("⚠️ Ya existe un informe cerrado para este mes.")
    else:
        nuevo_informe = InformeMensual(
            usuario_id=u_id,
            mes=mes,
            anio=anio,
            horas_totales=horas_recibidas,
            aceptado_por_empleado=False
        )
        db.session.add(nuevo_informe)
        db.session.commit()
        flash("✅ Mes cerrado. El empleado ya puede firmarlo en 'Mis Informes'.")

    return redirect(url_for('admin_informe', usuario_id=u_id))

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

# --- RUTAS DE INFORMES Y FIRMAS ---

from flask import send_file, make_response # Asegúrate de tener estas importaciones

@app.route('/empleado/ver_pdf/<int:informe_id>')
def ver_pdf_personal(informe_id):
    # 1. Usar 'user_id' para ser consistentes con el resto de la app
    u_id = session.get('user_id')
    
    if not u_id:
        print("DEBUG: No hay user_id en sesión, redirigiendo...")
        return redirect(url_for('login'))
    
    # 2. Obtener el informe y verificar que pertenece al usuario logueado
    informe = db.session.get(InformeMensual, informe_id)
    if not informe or informe.usuario_id != u_id:
        flash("No tienes permiso para ver este informe o no existe.")
        return redirect(url_for('ver_mis_informes')) # Ajustado al nombre de tu ruta

    # 3. Obtener el objeto usuario completo
    usuario = db.session.get(Usuario, u_id)
    
   # 4. Definir el rango de fechas basado en el informe mensual
    desde = f"{informe.anio}-{informe.mes:02d}-01"
    
    # Lógica para calcular el primer día del mes siguiente
    if informe.mes == 12:
        mes_sig = 1
        anio_sig = informe.anio + 1
    else:
        mes_sig = informe.mes + 1
        anio_sig = informe.anio
        
    hasta = f"{anio_sig}-{mes_sig:02d}-01 00:00:00"

    # 5. Obtener los fichajes del mes
    # Usamos '>' y '<' para evitar problemas con los días finales
    fichajes = Fichaje.query.filter_by(usuario_id=usuario.id).filter(
        Fichaje.timestamp >= desde,
        Fichaje.timestamp < hasta  # Cambiado a menor estricto
    ).order_by(Fichaje.timestamp.asc()).all()

    # 6. Agrupar por días (Lógica idéntica a la del admin)
    jornadas = {}
    total_segundos = 0
    for f in fichajes:
        fecha_str = f.timestamp.strftime('%d/%m/%Y')
        if fecha_str not in jornadas:
            jornadas[fecha_str] = {'entrada': None, 'salida': None, 'total': 0}
        
        if f.tipo == 'entrada' and not jornadas[fecha_str]['entrada']:
            jornadas[fecha_str]['entrada'] = f.timestamp
        elif f.tipo == 'salida':
            jornadas[fecha_str]['salida'] = f.timestamp

    # 7. Calcular totales por día
    for fecha in jornadas:
        d = jornadas[fecha]
        if d['entrada'] and d['salida']:
            diff = d['salida'] - d['entrada']
            segundos = diff.total_seconds()
            jornadas[fecha]['total'] = round(segundos / 3600, 2)
            total_segundos += segundos

    # 8. Generar PDF usando la función lógica común
    try:
        pdf_buffer = generar_pdf_logic(
            usuario=usuario,
            jornadas=jornadas,
            total_periodo=round(total_segundos / 3600, 2),
            rango={'desde': desde, 'hasta': hasta[:10]}
        )
        
        nombre_descarga = f"Mi_Registro_{informe.mes}_{informe.anio}.pdf"
        return send_file(
            pdf_buffer, 
            mimetype='application/pdf',
            as_attachment=False, # Para que se abra en el navegador
            download_name=nombre_descarga
        )
    except Exception as e:
        flash(f"Error al generar el PDF: {str(e)}")
        return redirect(url_for('ver_mis_informes'))


@app.route('/empleado/firmar_informe/<int:informe_id>')
def firmar_informe_empleado(informe_id): # Nombre cambiado
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    informe = db.session.get(InformeMensual, informe_id)
    if informe and informe.usuario_id == session['user_id']:
        informe.aceptado_por_empleado = True
        informe.fecha_firma = datetime.now()
        informe.ip_firma = request.headers.get('X-Forwarded-For', request.remote_addr) 
        db.session.commit()
        flash("✅ Informe mensual firmado correctamente.")
    else:
        flash("❌ No tienes permiso para firmar este informe.")
    return redirect(url_for('ver_mis_informes'))


@app.route('/mis_informes')
def ver_mis_informes():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    user = db.session.get(Usuario, session['user_id'])
    informes = InformeMensual.query.filter_by(usuario_id=user.id).order_by(InformeMensual.anio.desc(), InformeMensual.mes.desc()).all()
    
    return render_template('mis_informes.html', informes=informes, user=user)

# --- ARRANQUE DE LA APLICACIÓN ---

def inicializar_base_de_datos():
    """Crea las tablas e inserta el admin inicial si no existe."""
    with app.app_context():
        # 1. Crea las tablas en Postgres (solo si no existen)
        db.create_all()
        
        # 2. Definimos el DNI para el administrador
        admin_dni = '00000000T'
        
        # 3. Verificamos si ya existe el administrador
        if not Usuario.query.filter_by(dni=admin_dni).first():
            admin_inicial = Usuario(
                nombre='Admin',
                apellidos='Sistema',
                dni=admin_dni,
                nass='000000000000',
                rol='admin',
                horas_contratadas=40.0,
                email='admin@superpekes.com'
            )
            
            # Hash seguro para la contraseña
            admin_inicial.set_password('1234')
            
            db.session.add(admin_inicial)
            db.session.commit()
            print(f"Base de datos inicializada. Admin creado (DNI: {admin_dni}, Pass: 1234)")
        else:
            print("El usuario administrador ya existe en la base de datos.")

# Llamada obligatoria para que funcione en el despliegue de Render
inicializar_base_de_datos()

if __name__ == '__main__':
    # Esto solo se ejecuta en local (python app.py)
    app.run(debug=True)