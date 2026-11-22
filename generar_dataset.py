"""
Generador de un dataset sintético para el problema de Asignación de Flota
(Fleet Assignment) de una aerolínea doméstica ficticia con hub en Aeroparque.

Genera tres archivos CSV:
  - aeropuertos.csv : nodos de la red (código IATA, ciudad, coordenadas, si es base)
  - flota.csv       : tipos de avión disponibles (cantidad, asientos, costos, tiempo de giro)
  - vuelos.csv      : itinerario diario (origen, destino, horarios, demanda esperada, tarifa)

Los datos son inventados pero con órdenes de magnitud realistas (distancias reales
entre aeropuertos argentinos, tiempos de vuelo derivados de la distancia, costos
aproximados por hora de vuelo).

Uso:
    python generar_dataset.py                    # chico: 102 vuelos, 3 tipos -> ./datos
    python generar_dataset.py --tamano grande    # ~200 vuelos, 4 tipos -> ./datos_grande
    python generar_dataset.py --tamano uruguay   # 76 vuelos, base Carrasco -> ./datos_uruguay
    python generar_dataset.py --semilla 7 --salida otra_carpeta
    python generar_dataset.py --factor-demanda 1.2   # escenario de demanda +20%
"""

import argparse
import math
import os

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# 1. Aeropuertos (coordenadas reales aproximadas)
# ---------------------------------------------------------------------------
AEROPUERTOS = [
    # codigo, ciudad, provincia, lat, lon, es_base
    ("AEP", "Buenos Aires (Aeroparque)", "CABA", -34.5592, -58.4156, True),
    ("COR", "Córdoba", "Córdoba", -31.3236, -64.2080, True),
    ("MDZ", "Mendoza", "Mendoza", -32.8317, -68.7929, False),
    ("BRC", "San Carlos de Bariloche", "Río Negro", -41.1512, -71.1578, False),
    ("IGR", "Puerto Iguazú", "Misiones", -25.7373, -54.4734, False),
    ("SLA", "Salta", "Salta", -24.8560, -65.4862, False),
    ("TUC", "San Miguel de Tucumán", "Tucumán", -26.8409, -65.1049, False),
    ("NQN", "Neuquén", "Neuquén", -38.9490, -68.1557, False),
    ("USH", "Ushuaia", "Tierra del Fuego", -54.8433, -68.2958, False),
    ("FTE", "El Calafate", "Santa Cruz", -50.2803, -72.0531, False),
    ("MDQ", "Mar del Plata", "Buenos Aires", -37.9342, -57.5733, False),
    ("CRD", "Comodoro Rivadavia", "Chubut", -45.7853, -67.4655, False),
    ("REL", "Trelew", "Chubut", -43.2105, -65.2703, False),
    ("ROS", "Rosario", "Santa Fe", -32.9036, -60.7850, False),
    # aeropuertos que se suman en el dataset grande
    ("JUJ", "San Salvador de Jujuy", "Jujuy", -24.3928, -65.0978, False),
    ("PSS", "Posadas", "Misiones", -27.3858, -55.9707, False),
    ("RES", "Resistencia", "Chaco", -27.4500, -59.0561, False),
    ("CNQ", "Corrientes", "Corrientes", -27.4455, -58.7619, False),
    ("SDE", "Santiago del Estero", "Santiago del Estero", -27.7656, -64.3100, False),
    ("UAQ", "San Juan", "San Juan", -31.5715, -68.4182, False),
    ("BHI", "Bahía Blanca", "Buenos Aires", -38.7250, -62.1693, False),
    ("RGL", "Río Gallegos", "Santa Cruz", -51.6089, -69.3126, False),
    ("CPC", "San Martín de los Andes (Chapelco)", "Neuquén", -40.0754, -71.1373, False),
    ("PMY", "Puerto Madryn", "Chubut", -42.7592, -65.1027, False),
    ("SFN", "Santa Fe", "Santa Fe", -31.7117, -60.8117, False),
    ("CTC", "San Fernando del Valle de Catamarca", "Catamarca", -28.5956, -65.7517, False),
    ("LRJ", "La Rioja", "La Rioja", -29.3816, -66.7958, False),
    ("EQS", "Esquel", "Chubut", -42.9080, -71.1395, False),
    ("RCU", "Río Cuarto", "Córdoba", -33.0851, -64.2613, False),
    ("VDM", "Viedma", "Río Negro", -40.8692, -63.0004, False),
    ("AFA", "San Rafael", "Mendoza", -34.5883, -68.4039, False),
    # dataset Uruguay: aeropuertos uruguayos y destinos regionales (todos en UTC-3)
    ("MVD", "Montevideo (Carrasco)", "Canelones (UY)", -34.8384, -56.0308, True),
    ("PDP", "Punta del Este", "Maldonado (UY)", -34.8551, -55.0943, False),
    ("STY", "Salto", "Salto (UY)", -31.4385, -57.9853, False),
    ("RVY", "Rivera", "Rivera (UY)", -30.9746, -55.4762, False),
    ("MLZ", "Melo", "Cerro Largo (UY)", -32.3379, -54.2167, False),
    ("ATI", "Artigas", "Artigas (UY)", -30.4007, -56.5079, False),
    ("PDU", "Paysandú", "Paysandú (UY)", -32.3634, -58.0619, False),
    ("TAW", "Tacuarembó", "Tacuarembó (UY)", -31.7489, -55.9258, False),
    ("EZE", "Buenos Aires (Ezeiza)", "Buenos Aires (AR)", -34.8222, -58.5358, False),
    ("GRU", "São Paulo (Guarulhos)", "São Paulo (BR)", -23.4356, -46.4731, False),
    ("GIG", "Río de Janeiro (Galeão)", "Rio de Janeiro (BR)", -22.8100, -43.2506, False),
    ("POA", "Porto Alegre", "Rio Grande do Sul (BR)", -29.9944, -51.1714, False),
    ("FLN", "Florianópolis", "Santa Catarina (BR)", -27.6703, -48.5525, False),
    ("CWB", "Curitiba", "Paraná (BR)", -25.5317, -49.1758, False),
    ("ASU", "Asunción", "Central (PY)", -25.2400, -57.5191, False),
]

# ---------------------------------------------------------------------------
# 2. Flota (valores aproximados, en USD)
#    costo_hora_bloque: combustible + tripulación + mantenimiento + tasas, por hora de vuelo
#    costo_fijo_diario: costo de propiedad/leasing de tener el avión en servicio un día
# ---------------------------------------------------------------------------
FLOTA_CHICO = [
    # tipo, cantidad, asientos, costo_hora_bloque, costo_fijo_diario, giro_min, alcance_km
    ("E190", 10, 96, 3200, 5000, 35, 4000),
    ("B737-800", 14, 170, 5000, 8500, 45, 5400),
    ("B737-MAX8", 8, 170, 4300, 11500, 45, 6500),
]

# Dataset grande: se suma el ATR 72 (turbohélice), barato pero chico y de alcance corto
FLOTA_GRANDE = [
    ("ATR72", 10, 70, 2600, 3500, 25, 1500),
    ("E190", 16, 96, 3200, 5000, 35, 4000),
    ("B737-800", 13, 170, 5000, 8500, 45, 5400),
    ("B737-MAX8", 12, 170, 4300, 11500, 45, 6500),
]

# ---------------------------------------------------------------------------
# 3. Rutas: (origen, destino, frecuencias diarias ida y vuelta, demanda media por vuelo,
#            pernocte en destino: si True, el último vuelo de ida duerme en destino y
#            vuelve a primera hora del día siguiente)
# ---------------------------------------------------------------------------
RUTAS_CHICO = [
    ("AEP", "COR", 7, 135, True),
    ("AEP", "MDZ", 6, 145, True),
    ("AEP", "BRC", 5, 160, True),
    ("AEP", "IGR", 4, 150, False),
    ("AEP", "SLA", 4, 135, True),
    ("AEP", "TUC", 3, 115, False),
    ("AEP", "NQN", 4, 120, True),
    ("AEP", "USH", 2, 165, True),
    ("AEP", "FTE", 2, 150, False),
    ("AEP", "MDQ", 2, 70, False),
    ("AEP", "CRD", 3, 105, True),
    ("AEP", "REL", 2, 85, False),
    ("AEP", "ROS", 2, 60, False),
    ("COR", "BRC", 1, 100, False),
    ("COR", "IGR", 1, 85, False),
    ("COR", "SLA", 1, 80, False),
    ("COR", "NQN", 1, 70, False),
    ("USH", "FTE", 1, 75, False),
]

# Dataset grande: más frecuencias en las troncales y rutas regionales de baja demanda
RUTAS_GRANDE = [
    # troncales desde Aeroparque
    ("AEP", "COR", 10, 130, True),
    ("AEP", "MDZ", 8, 140, True),
    ("AEP", "BRC", 7, 155, True),
    ("AEP", "IGR", 6, 145, False),
    ("AEP", "SLA", 6, 130, True),
    ("AEP", "NQN", 6, 115, True),
    ("AEP", "TUC", 4, 115, True),
    ("AEP", "CRD", 4, 100, True),
    ("AEP", "USH", 3, 160, True),
    ("AEP", "FTE", 3, 145, False),
    ("AEP", "REL", 3, 80, False),
    ("AEP", "MDQ", 3, 65, False),
    ("AEP", "ROS", 3, 60, False),
    # regionales desde Aeroparque
    ("AEP", "JUJ", 3, 110, True),
    ("AEP", "UAQ", 3, 95, False),
    ("AEP", "PSS", 3, 90, False),
    ("AEP", "BHI", 3, 75, True),
    ("AEP", "RES", 2, 80, False),
    ("AEP", "SFN", 2, 55, False),
    ("AEP", "CNQ", 1, 75, False),
    ("AEP", "SDE", 1, 70, False),
    ("AEP", "RGL", 1, 95, True),
    ("AEP", "CPC", 1, 110, False),
    ("AEP", "PMY", 1, 80, False),
    ("AEP", "CTC", 1, 60, False),
    ("AEP", "LRJ", 1, 60, False),
    ("AEP", "EQS", 1, 70, False),
    ("AEP", "RCU", 1, 50, False),
    ("AEP", "VDM", 1, 45, False),
    ("AEP", "AFA", 1, 55, False),
    # desde Córdoba
    ("COR", "BRC", 1, 100, False),
    ("COR", "IGR", 1, 85, False),
    ("COR", "SLA", 1, 80, False),
    ("COR", "NQN", 1, 70, False),
    ("COR", "MDZ", 1, 70, False),
    ("COR", "TUC", 1, 60, False),
    # Patagonia
    ("USH", "FTE", 1, 75, False),
]

TAMANOS = {
    "chico": (FLOTA_CHICO, RUTAS_CHICO),
    "grande": (FLOTA_GRANDE, RUTAS_GRANDE),
}

# Dataset Uruguay: aerolínea ficticia con base en Carrasco. Casi no hay cabotaje en Uruguay,
# así que mezcla rutas cortas al interior (ATR 42) con rutas regionales (Embraer y Airbus),
# más Punta del Este en temporada de verano.
FLOTA_URUGUAY = [
    ("ATR42-600", 8, 48, 1900, 2800, 25, 1300),
    ("E195-E2", 8, 132, 3600, 7000, 35, 4800),
    ("A320neo", 6, 174, 4500, 10500, 45, 6300),
]
RUTAS_URUGUAY = [
    # regionales desde Carrasco
    ("MVD", "AEP", 10, 145, True),
    ("MVD", "EZE", 2, 130, False),
    ("MVD", "GRU", 4, 165, True),
    ("MVD", "GIG", 1, 150, False),
    ("MVD", "POA", 3, 100, False),
    ("MVD", "FLN", 1, 110, False),
    ("MVD", "CWB", 1, 90, False),
    ("MVD", "ASU", 1, 90, False),
    ("MVD", "COR", 1, 85, False),
    ("MVD", "ROS", 1, 60, False),
    # interior de Uruguay
    ("MVD", "STY", 2, 40, True),
    ("MVD", "RVY", 2, 35, False),
    ("MVD", "PDU", 1, 30, False),
    ("MVD", "TAW", 1, 25, False),
    ("MVD", "MLZ", 1, 28, False),
    ("MVD", "ATI", 1, 22, False),
    # Punta del Este (temporada de verano)
    ("PDP", "AEP", 3, 140, False),
    ("PDP", "GRU", 1, 150, False),
    ("PDP", "POA", 1, 80, False),
]
TAMANOS["uruguay"] = (FLOTA_URUGUAY, RUTAS_URUGUAY)
PREFIJOS = {"uruguay": "UY"}  # prefijo de id_vuelo (por defecto XX)
FACTOR_TARIFA = {"uruguay": 1.8}  # rutas internacionales: tarifas más altas

PRIMERA_SALIDA = 6 * 60          # 06:00
ULTIMA_LLEGADA = 23 * 60 + 55    # 23:55
VELOCIDAD_KMH = 720              # velocidad media en crucero (aprox.)
TIEMPO_FIJO_MIN = 30             # rodaje, despegue y aproximación


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def redondear5(x):
    return int(5 * round(x / 5))


def hhmm(minutos):
    minutos = int(minutos) % (24 * 60)
    return f"{minutos // 60:02d}:{minutos % 60:02d}"


def factor_horario(minuto_salida):
    """Picos de demanda a la mañana temprano y a la tarde-noche."""
    h = minuto_salida / 60
    if 6 <= h < 9.5 or 17 <= h < 21:
        return 1.15
    if 11 <= h < 15:
        return 0.85
    return 1.0


def generar(semilla=42, factor_demanda=1.0, tamano="chico"):
    rng = np.random.default_rng(semilla)
    flota, rutas = TAMANOS[tamano]

    df_aer = pd.DataFrame(
        AEROPUERTOS,
        columns=["codigo_iata", "ciudad", "provincia", "latitud", "longitud", "es_base"],
    )
    coords = {r.codigo_iata: (r.latitud, r.longitud) for r in df_aer.itertuples()}
    # solo los aeropuertos que aparecen en alguna ruta
    usados = {o for o, *_ in rutas} | {d for _, d, *_ in rutas}
    df_aer = df_aer[df_aer.codigo_iata.isin(usados)].reset_index(drop=True)

    df_flota = pd.DataFrame(
        flota,
        columns=["tipo_avion", "cantidad_disponible", "asientos", "costo_hora_bloque_usd",
                 "costo_fijo_diario_usd", "tiempo_giro_min", "alcance_km"],
    )

    vuelos = []
    numero = 1000

    def agregar_vuelo(orig, dest, salida, dist, dur, demanda_media):
        nonlocal numero
        numero += 2
        demanda = demanda_media * factor_horario(salida) * rng.lognormal(0, 0.15) * factor_demanda
        demanda = int(np.clip(round(demanda), 10, 240))
        tarifa = (40 + 0.07 * dist) * rng.uniform(0.9, 1.1) * FACTOR_TARIFA.get(tamano, 1.0)
        vuelos.append({
            "id_vuelo": f"{PREFIJOS.get(tamano, 'XX')}{numero}",
            "origen": orig,
            "destino": dest,
            "hora_salida": hhmm(salida),
            "hora_llegada": hhmm(salida + dur),
            "duracion_min": dur,
            "distancia_km": round(dist),
            "demanda_esperada": demanda,
            "tarifa_promedio_usd": round(tarifa, 2),
        })

    for orig, dest, freq, demanda_media, pernocte in rutas:
        dist = haversine_km(*coords[orig], *coords[dest])
        dur = redondear5(dist / VELOCIDAD_KMH * 60 + TIEMPO_FIJO_MIN)

        # Horarios de ida repartidos en el día con algo de ruido
        ultima_salida_ida = ULTIMA_LLEGADA - dur - (0 if pernocte else (dur + 50))
        base = np.linspace(PRIMERA_SALIDA, ultima_salida_ida, freq + 2)[1:-1] if freq > 1 \
            else np.array([rng.uniform(PRIMERA_SALIDA + 60, ultima_salida_ida - 60)])
        salidas_ida = sorted(
            redondear5(np.clip(s + rng.uniform(-35, 35), PRIMERA_SALIDA, ultima_salida_ida))
            for s in base
        )
        if pernocte:
            # el último vuelo de ida sale tarde y el avión duerme en destino
            salidas_ida[-1] = redondear5(ultima_salida_ida - rng.uniform(0, 30))

        for i, s in enumerate(salidas_ida):
            agregar_vuelo(orig, dest, s, dist, dur, demanda_media)

            if pernocte and i == len(salidas_ida) - 1:
                # vuelta a primera hora de la mañana (el avión durmió en destino)
                salida_vuelta = redondear5(PRIMERA_SALIDA + rng.uniform(0, 75))
            else:
                espera = rng.uniform(50, 110)
                salida_vuelta = redondear5(s + dur + espera)
                salida_vuelta = min(salida_vuelta, ULTIMA_LLEGADA - dur)
            agregar_vuelo(dest, orig, salida_vuelta, dist, dur, demanda_media)

    df_vuelos = (
        pd.DataFrame(vuelos)
        .sort_values(["hora_salida", "origen"])
        .reset_index(drop=True)
    )
    return df_aer, df_flota, df_vuelos


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--semilla", type=int, default=42)
    parser.add_argument("--factor-demanda", type=float, default=1.0)
    parser.add_argument("--tamano", choices=TAMANOS, default="chico",
                        help="chico: 102 vuelos y 3 tipos; grande: 200 vuelos y 4 tipos; "
                             "uruguay: 76 vuelos con base en Carrasco")
    parser.add_argument("--salida", default=None,
                        help="carpeta de salida (por defecto datos/ o datos_grande/)")
    args = parser.parse_args()
    if args.salida is None:
        args.salida = "datos" if args.tamano == "chico" else f"datos_{args.tamano}"

    df_aer, df_flota, df_vuelos = generar(args.semilla, args.factor_demanda, args.tamano)
    os.makedirs(args.salida, exist_ok=True)
    df_aer.to_csv(os.path.join(args.salida, "aeropuertos.csv"), index=False, encoding="utf-8-sig")  # BOM: Excel muestra bien los acentos
    df_flota.to_csv(os.path.join(args.salida, "flota.csv"), index=False, encoding="utf-8-sig")  # BOM: Excel muestra bien los acentos
    df_vuelos.to_csv(os.path.join(args.salida, "vuelos.csv"), index=False, encoding="utf-8-sig")  # BOM: Excel muestra bien los acentos
    print(f"{len(df_aer)} aeropuertos, {len(df_flota)} tipos de avión, "
          f"{len(df_vuelos)} vuelos -> {args.salida}/")


if __name__ == "__main__":
    main()
