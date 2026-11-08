# © 2026 Valentina Ramos Muñoz y Sofía Valentina Valencia López. Todos los derechos reservados.
# Universidad del Valle, Ingeniería Industrial, Palmira. Se prohíbe su reproducción, distribución o uso comercial sin autorización de las autoras.

from datetime import datetime
from functools import lru_cache
import math
from pathlib import Path

from openpyxl import load_workbook

from data import CATALOGO_CLIENTES, CLIENTES, PEAJES
from solver import resolver_vrp


EXCEL_PATH = Path(__file__).with_name('Datos.xlsm')
REPORTE_SHEET = 'Reporte diario'
DISTANCE_FACTOR = 1.3

MESES = {
    1: 'enero',
    2: 'febrero',
    3: 'marzo',
    4: 'abril',
    5: 'mayo',
    6: 'junio',
    7: 'julio',
    8: 'agosto',
    9: 'septiembre',
    10: 'octubre',
    11: 'noviembre',
    12: 'diciembre',
}


def fechas_disponibles():
    totales_por_fecha = {}
    for row in _leer_reporte():
        kg = row['kg_entre']
        if kg <= 0:
            continue
        totales_por_fecha[row['fecha']] = totales_por_fecha.get(row['fecha'], 0) + kg

    fechas = sorted(
        fecha
        for fecha, total in totales_por_fecha.items()
        if 0 < total <= 15300
    )
    return [
        {
            'valor': fecha.strftime('%Y-%m-%d'),
            'texto': _fecha_larga(fecha),
        }
        for fecha in fechas
    ]


def pedidos_por_fecha(fecha_texto):
    fecha = datetime.strptime(fecha_texto, '%Y-%m-%d').date()
    catalogo = _catalogo_por_id()
    acumulados = {}
    orden = []

    for row in _leer_reporte():
        if row['fecha'] != fecha:
            continue

        codigo = row['codigo']
        kg = row['kg_entre']
        if not codigo or kg <= 0:
            continue

        cliente_catalogo = catalogo.get(codigo, {})
        if codigo not in acumulados:
            orden.append(codigo)
            acumulados[codigo] = {
                'id_cliente': codigo,
                'nombre': row['cliente'] or cliente_catalogo.get('nombre', ''),
                'ciudad': _normalizar_ciudad(row['municipio'] or cliente_catalogo.get('ciudad', '')),
                'demanda': 0,
                'lat': cliente_catalogo.get('lat'),
                'lon': cliente_catalogo.get('lon'),
                'estado': row['estado'],
            }

        acumulados[codigo]['demanda'] += kg

    pedidos = [acumulados[codigo] for codigo in orden]
    return [p for p in pedidos if p['lat'] is not None and p['lon'] is not None]


def comparar_fecha(fecha_texto, parametros):
    pedidos = pedidos_por_fecha(fecha_texto)
    empirico = reconstruir_escenario_empirico(pedidos, parametros)
    optimizado = resolver_vrp(pedidos, parametros) if pedidos else _resultado_vacio()
    resumen = _resumen_comparativo(empirico, optimizado, parametros)

    return {
        'fecha': fecha_texto,
        'fecha_texto': _fecha_larga(datetime.strptime(fecha_texto, '%Y-%m-%d').date()),
        'pedidos': pedidos,
        'empirico': empirico,
        'optimizado': optimizado,
        'resumen': resumen,
        'ahorro': _calcular_ahorro(empirico, optimizado),
    }


def reconstruir_escenario_empirico(pedidos, parametros):
    vehiculos = parametros['vehiculos']
    rutas = {v['id']: _ruta_vacia() for v in vehiculos}
    vehiculo_idx = 0

    for pedido in pedidos:
        while vehiculo_idx < len(vehiculos):
            vehiculo = vehiculos[vehiculo_idx]
            ruta = rutas[vehiculo['id']]
            if ruta['carga_total'] + pedido['demanda'] <= vehiculo['capacidad'] or not ruta['paradas']:
                ruta['paradas'].append(pedido)
                ruta['carga_total'] += pedido['demanda']
                break
            vehiculo_idx += 1

        if vehiculo_idx >= len(vehiculos):
            vehiculo = vehiculos[-1]
            rutas[vehiculo['id']]['paradas'].append(pedido)
            rutas[vehiculo['id']]['carga_total'] += pedido['demanda']

    rutas = {
        vid: _calcular_metricas_ruta(vid, ruta['paradas'], ruta['carga_total'], parametros)
        for vid, ruta in rutas.items()
        if ruta['paradas']
    }

    return {
        'estado': 'Reconstruido',
        'rutas': rutas,
        'costo_combustible': round(sum(r['costo_combustible'] for r in rutas.values()), 2),
        'costo_peajes': round(sum(r['costo_peajes'] for r in rutas.values()), 2),
        'costo_total': round(sum(r['costo_total'] for r in rutas.values()), 2),
        'distancia_km': round(sum(r['distancia_km'] for r in rutas.values()), 2),
    }


def _calcular_metricas_ruta(vehiculo_id, pedidos, carga_total, parametros):
    vehiculo = next(v for v in parametros['vehiculos'] if v['id'] == vehiculo_id)
    planta = parametros['planta']
    paradas = [_parada_planta(planta)] + [_parada_pedido(p) for p in pedidos] + [_parada_planta(planta)]
    distancia = 0
    costo_peajes = 0

    for origen, destino in zip(paradas, paradas[1:]):
        distancia += _distancia(origen, destino)
        costo_peajes += _peaje(origen['ciudad'], destino['ciudad']) * vehiculo['costo_peaje']

    costo_combustible = distancia * vehiculo['consumo'] * parametros['costo_galon']

    return {
        'paradas': paradas,
        'carga_total': carga_total,
        'distancia_km': round(distancia, 2),
        'costo_combustible': round(costo_combustible, 2),
        'costo_peajes': round(costo_peajes, 2),
        'costo_total': round(costo_combustible + costo_peajes, 2),
    }


def _resumen_comparativo(empirico, optimizado, parametros):
    return [
        _fila_resumen('Distancia total (km)', empirico.get('distancia_km', 0), _distancia_total(optimizado), 'km'),
        _fila_resumen('Costo combustible', empirico['costo_combustible'], optimizado['costo_combustible'], '$'),
        _fila_resumen('Costo peajes', empirico['costo_peajes'], optimizado['costo_peajes'], '$'),
        _fila_resumen('Costo total', empirico['costo_total'], optimizado['costo_total'], '$'),
        _fila_resumen('% capacidad V1', _pct_capacidad(empirico, 1, parametros), _pct_capacidad(optimizado, 1, parametros), '%'),
        _fila_resumen('% capacidad V2', _pct_capacidad(empirico, 2, parametros), _pct_capacidad(optimizado, 2, parametros), '%'),
    ]


def _fila_resumen(nombre, empirico, optimizado, tipo):
    diferencia = empirico - optimizado
    return {
        'nombre': nombre,
        'empirico': empirico,
        'optimizado': optimizado,
        'diferencia': diferencia,
        'tipo': tipo,
    }


def _calcular_ahorro(empirico, optimizado):
    ahorro = empirico['costo_total'] - optimizado['costo_total']
    porcentaje = (ahorro / empirico['costo_total'] * 100) if empirico['costo_total'] else 0
    return {
        'valor': round(ahorro, 2),
        'porcentaje': round(porcentaje, 1),
    }


@lru_cache(maxsize=1)
def _leer_reporte():
    wb = load_workbook(EXCEL_PATH, read_only=True, data_only=True)
    ws = wb[REPORTE_SHEET]
    rows = []

    for row in ws.iter_rows(min_row=2, values_only=True):
        fecha = row[0]
        if not isinstance(fecha, datetime):
            continue

        rows.append({
            'fecha': fecha.date(),
            'placa': _texto(row[1]),
            'codigo': _texto(row[2]),
            'cliente': _texto(row[3]),
            'municipio': _normalizar_ciudad(row[4]),
            'kg_prog': _numero(row[6]),
            'kg_entre': _numero(row[7]),
            'estado': _texto(row[11]) if len(row) > 11 else '',
        })

    wb.close()
    return rows


def _catalogo_por_id():
    return {str(c['id']): c for c in CATALOGO_CLIENTES}


def _parada_planta(planta):
    return {
        'nodo': 'Planta',
        'nombre': 'PLANTA',
        'ciudad': planta['ciudad'],
        'demanda': 0,
        'lat': planta['lat'],
        'lon': planta['lon'],
    }


def _parada_pedido(pedido):
    return {
        'nodo': pedido['id_cliente'],
        'nombre': pedido['nombre'],
        'ciudad': pedido['ciudad'],
        'demanda': pedido['demanda'],
        'lat': pedido['lat'],
        'lon': pedido['lon'],
    }


def _ruta_vacia():
    return {'paradas': [], 'carga_total': 0}


def _resultado_vacio():
    return {'estado': 'Sin pedidos', 'rutas': {}, 'costo_combustible': 0, 'costo_peajes': 0, 'costo_total': 0}


def _distancia(origen, destino):
    return _haversine(origen['lat'], origen['lon'], destino['lat'], destino['lon']) * DISTANCE_FACTOR


def _distancia_total(resultado):
    return round(sum(r.get('distancia_km', 0) for r in resultado.get('rutas', {}).values()), 2)


def _pct_capacidad(resultado, vehiculo_id, parametros):
    capacidad = next((v['capacidad'] for v in parametros['vehiculos'] if v['id'] == vehiculo_id), 0)
    carga = resultado.get('rutas', {}).get(vehiculo_id, {}).get('carga_total', 0)
    return round((carga / capacidad) * 100) if capacidad else 0


def _haversine(lat1, lon1, lat2, lon2):
    r = 6371
    lat1, lon1 = math.radians(lat1), math.radians(lon1)
    lat2, lon2 = math.radians(lat2), math.radians(lon2)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return r * 2 * math.asin(math.sqrt(a))


def _peaje(ciudad_i, ciudad_j):
    return PEAJES.get(f'{_normalizar_ciudad(ciudad_i)}|{_normalizar_ciudad(ciudad_j)}', 0)


def _normalizar_ciudad(ciudad):
    return _texto(ciudad).upper().strip()


def _texto(valor):
    return '' if valor is None else str(valor).strip()


def _numero(valor):
    if valor is None or valor == '-':
        return 0
    return int(round(float(valor)))


def _fecha_larga(fecha):
    return f'{fecha.day} de {MESES[fecha.month]} de {fecha.year}'
