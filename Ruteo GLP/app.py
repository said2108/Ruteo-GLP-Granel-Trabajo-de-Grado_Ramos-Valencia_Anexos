# © 2026 Valentina Ramos Muñoz y Sofía Valentina Valencia López. Todos los derechos reservados.
# Universidad del Valle, Ingeniería Industrial, Palmira. Se prohíbe su reproducción, distribución o uso comercial sin autorización de las autoras.

import copy
import json
import sys
from pathlib import Path

VENDOR_DIR = Path(__file__).with_name('.vendor')
if VENDOR_DIR.exists():
    sys.path.insert(0, str(VENDOR_DIR))

from flask import Flask, render_template, request, redirect, url_for, flash

from data import PEDIDOS_BASE, PARAMETROS_BASE, CATALOGO_CLIENTES
from historico import comparar_fecha, fechas_disponibles, pedidos_por_fecha
from solver import resolver_vrp

app = Flask(__name__)
app.secret_key = 'vrp-glp-tesis'
CAPACIDAD_MAXIMA_NUEVA_RUTA = 15300

# ── Estado global (app de un solo usuario) ───────────────────
_estado = {
    'pedidos':    None,
    'parametros': None,
    'resultados': None,
}


def _pedidos():
    if _estado['pedidos'] is None:
        _estado['pedidos'] = copy.deepcopy(PEDIDOS_BASE)
    return _estado['pedidos']


def _parametros():
    if _estado['parametros'] is None:
        _estado['parametros'] = copy.deepcopy(PARAMETROS_BASE)
    return _estado['parametros']


# ── Dashboard ─────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('seleccion_modo.html')


@app.route('/ruteo-nuevo')
def ruteo_nuevo():
    pedidos = _pedidos()
    parametros = _parametros()
    demanda_total = sum(p['demanda'] for p in pedidos)
    cap_v1 = parametros['vehiculos'][0]['capacidad']
    cap_v2 = parametros['vehiculos'][1]['capacidad']
    uso_capacidad = _uso_capacidad_por_vehiculo(_estado['resultados'], parametros)
    return render_template(
        'index.html',
        pedidos=pedidos,
        demanda_total=demanda_total,
        cap_v1=cap_v1,
        cap_v2=cap_v2,
        uso_capacidad=uso_capacidad,
        resultados=_estado['resultados'],
    )


@app.route('/comparacion')
def comparacion():
    fecha = request.args.get('fecha')
    ver_comparacion = request.args.get('comparar') == '1'
    fechas = fechas_disponibles()
    pedidos_historicos = pedidos_por_fecha(fecha) if fecha else []
    comparativo = comparar_fecha(fecha, _parametros()) if fecha and ver_comparacion else None

    return render_template(
        'comparacion.html',
        fechas=fechas,
        fechas_json=json.dumps(fechas, ensure_ascii=False),
        fecha_seleccionada=fecha,
        pedidos_historicos=pedidos_historicos,
        comparativo=comparativo,
        capacidades=_capacidades_vehiculos(_parametros()),
    )


# ── Pedidos ───────────────────────────────────────────────────
@app.route('/pedidos')
def pedidos():
    pedidos_actuales = _pedidos()
    capacidad_total = _capacidad_total_flota(_parametros())
    demanda_total = _demanda_total(pedidos_actuales)
    porcentaje_capacidad = round((demanda_total / capacidad_total) * 100) if capacidad_total else 0
    if porcentaje_capacidad >= 95:
        estado_capacidad = 'danger'
    elif porcentaje_capacidad >= 80:
        estado_capacidad = 'warning'
    else:
        estado_capacidad = 'success'

    return render_template(
        'pedidos.html',
        pedidos=pedidos_actuales,
        catalogo_json=_catalogo_json(),
        capacidad_total=capacidad_total,
        demanda_total=demanda_total,
        capacidad_disponible=max(capacidad_total - demanda_total, 0),
        porcentaje_capacidad=porcentaje_capacidad,
        estado_capacidad=estado_capacidad,
    )


@app.route('/pedidos/agregar', methods=['POST'])
def pedidos_agregar():
    try:
        pedido = _pedido_desde_form(request.form)
        pedidos = _pedidos()
        if _cliente_ya_en_pedidos(pedidos, pedido['id_cliente']):
            flash('Este cliente ya fue agregado a los pedidos del día y no puede agregarse de nuevo.', 'danger')
            return redirect(url_for('pedidos'))

        capacidad_max_vehiculo = max(v['capacidad'] for v in _parametros()['vehiculos'])
        if pedido['demanda'] > capacidad_max_vehiculo:
            flash(f'Este pedido no puede agregarse. La demanda de {pedido["demanda"]:,} kg supera la capacidad máxima de cualquier vehículo disponible en la flota (10.500 kg máximo).', 'danger')
            return redirect(url_for('pedidos'))

        capacidad_total = _capacidad_total_flota(_parametros())
        if _demanda_total(pedidos) + pedido['demanda'] > capacidad_total:
            flash(_mensaje_capacidad_superada(), 'danger')
            return redirect(url_for('pedidos'))

        pedidos.append(pedido)
        _estado['resultados'] = None
        flash('Pedido agregado.', 'success')
    except (ValueError, KeyError):
        flash('Datos inválidos. Revisa los campos.', 'danger')
    return redirect(url_for('pedidos'))


@app.route('/pedidos/editar/<int:idx>', methods=['POST'])
def pedidos_editar(idx):
    pedidos = _pedidos()
    if 0 <= idx < len(pedidos):
        try:
            pedido = _pedido_desde_form(request.form)
            capacidad_max_vehiculo = max(v['capacidad'] for v in _parametros()['vehiculos'])
            if pedido['demanda'] > capacidad_max_vehiculo:
                flash(f'No es posible actualizar este pedido. La demanda de {pedido["demanda"]:,} kg supera la capacidad máxima de cualquier vehículo disponible en la flota (10.500 kg máximo).', 'danger')
                return redirect(url_for('pedidos'))

            capacidad_total = _capacidad_total_flota(_parametros())
            demanda_sin_actual = _demanda_total(pedidos) - pedidos[idx]['demanda']
            if demanda_sin_actual + pedido['demanda'] > capacidad_total:
                flash(_mensaje_capacidad_superada('actualizar'), 'danger')
                return redirect(url_for('pedidos'))

            pedidos[idx] = pedido
            _estado['resultados'] = None
            flash('Pedido actualizado.', 'success')
        except (ValueError, KeyError):
            flash('Datos inválidos. Revisa los campos.', 'danger')
    return redirect(url_for('pedidos'))


@app.route('/pedidos/eliminar/<int:idx>', methods=['POST'])
def pedidos_eliminar(idx):
    pedidos = _pedidos()
    if 0 <= idx < len(pedidos):
        nombre = pedidos[idx]['nombre']
        pedidos.pop(idx)
        _estado['resultados'] = None
        flash(f'Pedido "{nombre}" eliminado.', 'warning')
    return redirect(url_for('pedidos'))


@app.route('/pedidos/resetear', methods=['POST'])
def pedidos_resetear():
    _estado['pedidos'] = copy.deepcopy(PEDIDOS_BASE)
    _estado['resultados'] = None
    flash('Pedidos restaurados al estado original.', 'info')
    return redirect(url_for('pedidos'))


# ── Parámetros ────────────────────────────────────────────────
@app.route('/parametros', methods=['GET', 'POST'])
def parametros():
    params = _parametros()
    if request.method == 'POST':
        try:
            params['planta']['lat']  = float(request.form['planta_lat'])
            params['planta']['lon']  = float(request.form['planta_lon'])
            params['costo_galon']    = int(request.form['costo_galon'])
            _estado['resultados'] = None
            flash('Parámetros actualizados.', 'success')
        except ValueError:
            flash('Valores inválidos.', 'danger')
        return redirect(url_for('parametros'))
    return render_template('parametros.html', params=params)


# ── Optimizar ─────────────────────────────────────────────────
@app.route('/optimizar', methods=['POST'])
def optimizar():
    pedidos = _pedidos()
    if not pedidos:
        flash('No hay pedidos para optimizar.', 'danger')
        return redirect(url_for('ruteo_nuevo'))

    resultado = resolver_vrp(pedidos, _parametros())
    _estado['resultados'] = resultado

    if resultado['estado'] != 'Optimal':
        flash(f'El solver no encontró solución óptima: {resultado["estado"]}', 'warning')
        return redirect(url_for('ruteo_nuevo'))

    return redirect(url_for('resultados'))


# ── Resultados ────────────────────────────────────────────────
@app.route('/resultados')
def resultados():
    resultado = _estado.get('resultados')
    if not resultado:
        flash('Primero ejecuta la optimización.', 'info')
        return redirect(url_for('ruteo_nuevo'))
    return render_template(
        'resultados.html',
        resultado=resultado,
        uso_capacidad=_uso_capacidad_por_vehiculo(resultado, _parametros()),
    )


# ── Helpers ───────────────────────────────────────────────────
def _pedido_desde_form(form):
    return {
        'nombre':     form['nombre'].strip(),
        'id_cliente': form['id_cliente'].strip(),
        'ciudad':     form['ciudad'].strip(),
        'demanda':    int(form['demanda']),
        'lat':        float(form['lat']),
        'lon':        float(form['lon']),
    }


def _catalogo_json():
    return json.dumps(CATALOGO_CLIENTES, ensure_ascii=False)


def _capacidades_vehiculos(parametros):
    return {v['id']: v['capacidad'] for v in parametros['vehiculos']}


def _capacidad_total_flota(parametros):
    return CAPACIDAD_MAXIMA_NUEVA_RUTA


def _demanda_total(pedidos):
    return sum(p.get('demanda', 0) for p in pedidos)


def _cliente_ya_en_pedidos(pedidos, id_cliente):
    return any(p.get('id_cliente') == id_cliente for p in pedidos)


def _mensaje_capacidad_superada(accion='agregar'):
    return (
        f'No es posible {accion} este pedido. '
        'La capacidad máxima de la flota es de 15.300 kg y se superaría con esta adición.'
    )


def _uso_capacidad_por_vehiculo(resultados, parametros):
    if not resultados or resultados.get('estado') != 'Optimal':
        return []

    capacidades = {v['id']: v['capacidad'] for v in parametros['vehiculos']}
    uso = []

    rutas = resultados.get('rutas', {})
    for vehiculo in parametros['vehiculos']:
        vid_int = vehiculo['id']
        capacidad = capacidades.get(vid_int, 0)
        ruta = rutas.get(vid_int) or rutas.get(str(vid_int)) or {}
        carga = ruta.get('carga_total', 0)
        porcentaje = round((carga / capacidad) * 100) if capacidad else 0
        uso.append({
            'id': vid_int,
            'nombre': 'Grande' if vid_int == 1 else 'Pequeño',
            'carga': carga,
            'capacidad': capacidad,
            'porcentaje': porcentaje,
        })

    return uso


if __name__ == '__main__':
    app.run(debug=True, port=5000)
