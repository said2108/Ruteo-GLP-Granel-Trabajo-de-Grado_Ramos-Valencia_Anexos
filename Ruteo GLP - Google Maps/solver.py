# © 2026 Valentina Ramos Muñoz y Sofía Valentina Valencia López. Todos los derechos reservados.
# Universidad del Valle, Ingeniería Industrial, Palmira. Se prohíbe su reproducción, distribución o uso comercial sin autorización de las autoras.

import math
import requests
from pulp import LpProblem, LpMinimize, LpVariable, LpStatus, lpSum, value, PULP_CBC_CMD

from data import CLIENTES, PEAJES

OSRM_BASE = "https://router.project-osrm.org"


def _osrm_table(coords_list, timeout=30):
    """
    coords_list: list of (lat, lon) tuples
    Returns dict {(i, j): distance_km} for all i != j, or None on failure.
    """
    locs = ";".join(f"{lon},{lat}" for lat, lon in coords_list)
    url = f"{OSRM_BASE}/table/v1/driving/{locs}"
    try:
        resp = requests.get(url, params={"annotations": "distance", "sources": "all", "destinations": "all"}, timeout=timeout)
        data = resp.json()
        if "distances" not in data:
            return None
        d = data["distances"]
        n = len(coords_list)
        return {(i, j): d[i][j] / 1000 for i in range(n) for j in range(n) if i != j}
    except Exception:
        return None


def _osrm_route(coords_list, timeout=15):
    """
    coords_list: list of (lat, lon) tuples en orden de visita.
    Returns (geojson_geometry_dict, distance_km) o (None, None) si falla.
    """
    locs = ";".join(f"{lon},{lat}" for lat, lon in coords_list)
    url = f"{OSRM_BASE}/route/v1/driving/{locs}"
    try:
        resp = requests.get(url, params={"overview": "full", "geometries": "geojson"}, timeout=timeout)
        data = resp.json()
        route = data["routes"][0]
        return route["geometry"], route["distance"] / 1000
    except Exception:
        return None, None


def _haversine(lat1, lon1, lat2, lon2):
    R = 6371
    lat1, lon1 = math.radians(lat1), math.radians(lon1)
    lat2, lon2 = math.radians(lat2), math.radians(lon2)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _peaje(ciudad_i, ciudad_j):
    return PEAJES.get(f'{ciudad_i}|{ciudad_j}', 0)


def resolver_vrp(pedidos, parametros):
    """
    Resuelve el VRP y retorna un dict con rutas y costos.

    pedidos: lista de dicts con claves id_cliente, nombre, ciudad, demanda, lat, lon
    parametros: dict con claves planta, vehiculos, costo_galon
    """
    planta = parametros['planta']
    vehiculos_cfg = parametros['vehiculos']
    costo_galon = parametros['costo_galon']

    vehiculos   = [v['id'] for v in vehiculos_cfg]
    capacidad   = {v['id']: v['capacidad']   for v in vehiculos_cfg}
    consumo     = {v['id']: v['consumo']     for v in vehiculos_cfg}
    costo_peaje = {v['id']: v['costo_peaje'] for v in vehiculos_cfg}

    # ── Nodos ────────────────────────────────────────────────
    nodos    = ['Planta'] + [p['id_cliente'] for p in pedidos]
    clientes = [p['id_cliente'] for p in pedidos]

    # ── Demandas ─────────────────────────────────────────────
    demanda = {'Planta': 0}
    for p in pedidos:
        demanda[p['id_cliente']] = p['demanda']

    # ── Coordenadas ──────────────────────────────────────────
    coords = {'Planta': (planta['lat'], planta['lon'])}
    for p in pedidos:
        coords[p['id_cliente']] = (p['lat'], p['lon'])

    # ── Ciudad de cada nodo (para lookup de peajes) ──────────
    ciudad_nodo = {'Planta': planta['ciudad']}
    for p in pedidos:
        ciudad_nodo[p['id_cliente']] = p['ciudad']

    # ── Coordenadas en orden de nodos ───────────────────────────
    coords_list = [coords[n] for n in nodos]

    # ── Matriz de distancias (OSRM real, fallback Haversine × 1.3) ──
    osrm_dists = _osrm_table(coords_list)
    dist = {}
    for i, ni in enumerate(nodos):
        for j, nj in enumerate(nodos):
            if i == j:
                dist[(ni, nj)] = 0.0
            elif osrm_dists is not None and (i, j) in osrm_dists:
                dist[(ni, nj)] = osrm_dists[(i, j)]
            else:
                lat1, lon1 = coords_list[i]
                lat2, lon2 = coords_list[j]
                dist[(ni, nj)] = _haversine(lat1, lon1, lat2, lon2) * 1.3

    # ── Modelo PuLP ──────────────────────────────────────────
    prob = LpProblem('VRP_DINAMICO', LpMinimize)

    x = LpVariable.dicts('x', (nodos, nodos, vehiculos), cat='Binary')
    u = LpVariable.dicts('u', (clientes, vehiculos), lowBound=0, cat='Continuous')

    # Función objetivo: combustible + peajes
    prob += lpSum(
        (dist[i, j] * consumo[v] * costo_galon
         + _peaje(ciudad_nodo[i], ciudad_nodo[j]) * costo_peaje[v])
        * x[i][j][v]
        for i in nodos for j in nodos for v in vehiculos if i != j
    )

    # R1: cada cliente se visita exactamente una vez
    for j in clientes:
        prob += lpSum(x[i][j][v] for i in nodos for v in vehiculos if i != j) == 1

    # R2: conservación de flujo
    for v in vehiculos:
        for j in clientes:
            prob += (
                lpSum(x[i][j][v] for i in nodos if i != j)
                == lpSum(x[j][k][v] for k in nodos if k != j)
            )

    # R3: capacidad de vehículos
    for v in vehiculos:
        prob += lpSum(
            demanda[j] * x[i][j][v]
            for j in clientes for i in nodos if i != j
        ) <= capacidad[v]

    # R4: cada vehículo sale de la planta a lo sumo una vez
    for v in vehiculos:
        prob += lpSum(x['Planta'][j][v] for j in clientes) <= 1

    # R5: regreso a planta
    for v in vehiculos:
        prob += (
            lpSum(x[i]['Planta'][v] for i in clientes)
            == lpSum(x['Planta'][j][v] for j in clientes)
        )

    # R6: eliminación de subtours (MTZ)
    n = len(clientes)
    for v in vehiculos:
        for i in clientes:
            for j in clientes:
                if i != j:
                    prob += u[i][v] - u[j][v] + n * x[i][j][v] <= n - 1

    # ── Resolver ─────────────────────────────────────────────
    prob.solve(PULP_CBC_CMD(msg=0))

    estado = LpStatus[prob.status]

    if prob.status != 1:
        return {'estado': estado, 'costo_combustible': 0, 'costo_peajes': 0, 'costo_total': 0, 'rutas': {}}

    # ── Extraer rutas ────────────────────────────────────────
    pedido_idx = {p['id_cliente']: p for p in pedidos}
    rutas = {}

    for v in vehiculos:
        salida = [
            j for j in clientes
            if x['Planta'][j][v].varValue is not None and x['Planta'][j][v].varValue > 0.9
        ]
        if not salida:
            continue

        # Reconstruir secuencia ordenada
        secuencia = ['Planta']
        nodo_actual = salida[0]
        visitados = set()

        while nodo_actual != 'Planta':
            if nodo_actual in visitados:
                break
            visitados.add(nodo_actual)
            secuencia.append(nodo_actual)
            siguiente = [
                j for j in nodos
                if x[nodo_actual][j][v].varValue is not None and x[nodo_actual][j][v].varValue > 0.9
            ]
            nodo_actual = siguiente[0] if siguiente else 'Planta'

        secuencia.append('Planta')

        # Construir lista de paradas con info completa
        paradas = []
        for nodo in secuencia:
            if nodo == 'Planta':
                paradas.append({
                    'nodo': 'Planta',
                    'nombre': 'PLANTA',
                    'ciudad': planta['ciudad'],
                    'demanda': 0,
                    'lat': planta['lat'],
                    'lon': planta['lon'],
                })
            else:
                p = pedido_idx[nodo]
                paradas.append({
                    'nodo': nodo,
                    'nombre': p['nombre'],
                    'ciudad': p['ciudad'],
                    'demanda': p['demanda'],
                    'lat': p['lat'],
                    'lon': p['lon'],
                })

        distancia_total = sum(dist[(secuencia[k], secuencia[k + 1])] for k in range(len(secuencia) - 1))
        carga_total     = sum(demanda[n] for n in secuencia if n != 'Planta')

        costo_comb_v = sum(
            dist[i, j] * consumo[v] * costo_galon * value(x[i][j][v])
            for i in nodos for j in nodos if i != j
        )
        costo_peaj_v = sum(
            _peaje(ciudad_nodo[i], ciudad_nodo[j]) * costo_peaje[v] * value(x[i][j][v])
            for i in nodos for j in nodos if i != j
        )

        # Obtener geometría real de la ruta desde OSRM
        seq_coords = [(coords[n][0], coords[n][1]) for n in secuencia]
        geometry, real_dist = _osrm_route(seq_coords)

        rutas[v] = {
            'paradas':          paradas,
            'carga_total':      carga_total,
            'distancia_km':     round(real_dist if real_dist else distancia_total, 2),
            'costo_combustible': round(costo_comb_v, 2),
            'costo_peajes':     round(costo_peaj_v, 2),
            'costo_total':      round(costo_comb_v + costo_peaj_v, 2),
            'geometria':        geometry,
        }

    costo_comb_total  = sum(r['costo_combustible'] for r in rutas.values())
    costo_peaj_total  = sum(r['costo_peajes']      for r in rutas.values())
    distancia_total   = sum(r['distancia_km']       for r in rutas.values())

    return {
        'estado':            estado,
        'costo_combustible': round(costo_comb_total, 2),
        'costo_peajes':      round(costo_peaj_total, 2),
        'costo_total':       round(value(prob.objective), 2),
        'distancia_km':      round(distancia_total, 2),
        'rutas':             rutas,
    }
