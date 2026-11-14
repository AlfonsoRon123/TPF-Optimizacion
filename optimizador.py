"""
optimizador.py — Asignación de flota (Fleet Assignment) con rotaciones de aviones.

Funciona con cualquier conjunto de aeropuertos, vuelos y flota que respete la
estructura de columnas (ver COLUMNAS). Lo usa app.py, pero también se puede usar solo:

    from optimizador import leer_tabla, validar, resolver
    tablas = {n: leer_tabla(f"datos/{n}.csv") for n in ("vuelos", "flota", "aeropuertos")}
    datos = validar(tablas)
    res = resolver(datos)
    print(res.estado, res.costo_total)

Modelo (red tiempo-espacio, un "nodo" = aeropuerto + momento del día, por tipo de avión):

    x[f,k] ∈ {0,1}   el vuelo f lo opera un avión del tipo k
    y[a,k,i] ≥ 0     aviones tipo k esperando en tierra en a entre el evento i y el i+1
    N[k]             aviones tipo k usados = aviones contados a la "hora de conteo"

    min  Σ (costo_hora_k · horas_f + tarifa_f · max(0, demanda_f − asientos_k)) · x[f,k]
         + Σ costo_fijo_k · N[k]
    s.a. Σ_k x[f,k] = 1                         cada vuelo lo hace exactamente un avión
         entra + llegan = sale + despegan       balance en cada nodo (aeropuerto, momento, tipo)
         N[k] ≤ cantidad_disponible_k           no usar más aviones de los que hay
         x[f,k] = 0 si distancia_f > alcance_k  alcance de cada tipo
"""
from __future__ import annotations

import datetime as dt
import io
import math
import time
import unicodedata
from collections import defaultdict, deque
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import pulp

# ---------------------------------------------------------------------------
# Estructura de los datos de entrada
# ---------------------------------------------------------------------------
COLUMNAS = {
    "vuelos": {
        "obligatorias": ["id_vuelo", "origen", "destino", "hora_salida",
                         "demanda_esperada", "tarifa_promedio_usd"],
        "una_de": [("hora_llegada", "duracion_min")],
        "opcionales": ["distancia_km"],
    },
    "flota": {
        "obligatorias": ["tipo_avion", "cantidad_disponible", "asientos",
                         "costo_hora_bloque_usd", "tiempo_giro_min"],
        "una_de": [],
        "opcionales": ["costo_fijo_diario_usd", "alcance_km"],
    },
    "aeropuertos": {
        "obligatorias": ["codigo_iata", "latitud", "longitud"],
        "una_de": [],
        "opcionales": ["ciudad", "provincia", "es_base"],
    },
}
TABLAS = tuple(COLUMNAS)
DIA = 24 * 60


# ---------------------------------------------------------------------------
# Lectura
# ---------------------------------------------------------------------------
def _nombre(archivo) -> str:
    return str(getattr(archivo, "name", archivo))


def _bytes(archivo) -> bytes:
    if hasattr(archivo, "getvalue"):
        return archivo.getvalue()
    with open(archivo, "rb") as fh:
        return fh.read()


def leer_tabla(archivo) -> pd.DataFrame:
    """Lee un CSV (separador , o ; y codificación UTF-8 o Latin-1) o la primera hoja de un Excel."""
    if _nombre(archivo).lower().endswith((".xlsx", ".xlsm", ".xls")):
        return pd.read_excel(io.BytesIO(_bytes(archivo)))
    datos = _bytes(archivo)
    for codificacion in ("utf-8-sig", "latin-1"):
        try:
            texto = datos.decode(codificacion)
            break
        except UnicodeDecodeError:
            continue
    return pd.read_csv(io.StringIO(texto), sep=None, engine="python")


def leer_excel(archivo) -> dict[str, pd.DataFrame]:
    """Lee un Excel con tres hojas: vuelos, flota y aeropuertos (alcanza con que el nombre las contenga)."""
    hojas = pd.read_excel(io.BytesIO(_bytes(archivo)), sheet_name=None)
    tablas = {}
    for nombre_hoja, df in hojas.items():
        n = _normalizar(nombre_hoja)
        for tabla, clave in (("vuelos", "vuelo"), ("flota", "flota"), ("aeropuertos", "aeropuerto")):
            if clave in n and tabla not in tablas:
                tablas[tabla] = df
    return tablas


def plantilla_excel(tablas: dict[str, pd.DataFrame]) -> bytes:
    """Devuelve un Excel con una hoja por tabla (sirve de plantilla o para exportar)."""
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for nombre, df in tablas.items():
            df.to_excel(writer, sheet_name=nombre[:31], index=False)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Validación
# ---------------------------------------------------------------------------
@dataclass
class Datos:
    vuelos: pd.DataFrame | None = None
    flota: pd.DataFrame | None = None
    aeropuertos: pd.DataFrame | None = None
    errores: list[str] = field(default_factory=list)
    advertencias: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errores and self.vuelos is not None


def _normalizar(texto) -> str:
    texto = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    return texto.strip().lower().replace(" ", "_").replace("-", "_")


def _a_numero(serie: pd.Series) -> pd.Series:
    if not pd.api.types.is_numeric_dtype(serie):
        serie = serie.astype(str).str.strip().str.replace(",", ".", regex=False)
        serie = serie.replace({"": np.nan, "nan": np.nan, "None": np.nan})
    return pd.to_numeric(serie, errors="coerce")


def a_minutos(valor) -> int | None:
    """Convierte una hora (08:25, 8:25:00, hora de Excel, 0.35...) a minutos desde las 00:00."""
    if valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return None
    if isinstance(valor, (dt.time, dt.datetime, pd.Timestamp)):
        return valor.hour * 60 + valor.minute
    if isinstance(valor, (int, float, np.integer, np.floating)):
        v = float(valor)
        if 0 <= v < 1:  # fracción de día (así guarda Excel las horas)
            return int(round(v * DIA)) % DIA
        return None
    texto = str(valor).strip()
    if not texto or texto.lower() == "nan":
        return None
    try:
        partes = texto.split(":")
        h, m = int(partes[0]), int(partes[1]) if len(partes) > 1 else 0
    except ValueError:
        return None
    if 0 <= h < 24 and 0 <= m < 60:
        return h * 60 + m
    return None


def hhmm(minutos: int) -> str:
    minutos = int(minutos) % DIA
    return f"{minutos // 60:02d}:{minutos % 60:02d}"


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def _filas(indices, maximo=8) -> str:
    """Números de fila tal como se ven en Excel (encabezado = fila 1)."""
    filas = [str(i + 2) for i in list(indices)[:maximo]]
    resto = len(indices) - maximo
    return ", ".join(filas) + (f" y {resto} más" if resto > 0 else "")


def validar(tablas: dict[str, pd.DataFrame | None]) -> Datos:
    """Revisa estructura y consistencia de las tres tablas y devuelve datos limpios o la lista de errores."""
    datos = Datos()
    err, adv = datos.errores, datos.advertencias
    limpias = {}

    # --- 1. Que estén las tablas y las columnas ---
    for nombre, reglas in COLUMNAS.items():
        df = tablas.get(nombre)
        if df is None:
            err.append(f"Falta la tabla **{nombre}**.")
            continue
        df = df.copy()
        df.columns = [_normalizar(c) for c in df.columns]
        df = df.dropna(how="all")
        faltan = [c for c in reglas["obligatorias"] if c not in df.columns]
        for grupo in reglas["una_de"]:
            if not any(c in df.columns for c in grupo):
                faltan.append(" o ".join(grupo))
        if faltan:
            err.append(f"En **{nombre}** faltan columnas: {', '.join(faltan)}.")
            continue
        if df.empty:
            err.append(f"La tabla **{nombre}** está vacía.")
            continue
        limpias[nombre] = df.reset_index(drop=True)
    if err:
        return datos

    # --- 2. Aeropuertos ---
    a = limpias["aeropuertos"]
    a["codigo_iata"] = a["codigo_iata"].astype(str).str.strip().str.upper()
    for c in ("latitud", "longitud"):
        a[c] = _a_numero(a[c])
        malos = a.index[a[c].isna()]
        if len(malos):
            err.append(f"En **aeropuertos**, `{c}` vacía o no numérica (filas {_filas(malos)}).")
    dup = a.index[a.codigo_iata.duplicated()]
    if len(dup):
        err.append(f"En **aeropuertos** hay códigos repetidos: {', '.join(a.codigo_iata[dup].unique())}.")
    for c in ("ciudad", "provincia"):
        if c not in a.columns:
            a[c] = ""
    if "es_base" not in a.columns:
        a["es_base"] = False
    codigos = set(a.codigo_iata)

    # --- 3. Flota ---
    fl = limpias["flota"]
    fl["tipo_avion"] = fl["tipo_avion"].astype(str).str.strip()
    if "costo_fijo_diario_usd" not in fl.columns:
        fl["costo_fijo_diario_usd"] = 0
        adv.append("**flota** no tiene `costo_fijo_diario_usd`: se toma 0 (no se penaliza usar más aviones).")
    if "alcance_km" not in fl.columns:
        fl["alcance_km"] = np.inf
    numericas = {"cantidad_disponible": 0, "asientos": 1, "costo_hora_bloque_usd": 0,
                 "tiempo_giro_min": 0, "costo_fijo_diario_usd": 0, "alcance_km": 1}
    for c, minimo in numericas.items():
        fl[c] = _a_numero(fl[c])
        if c == "alcance_km":
            fl[c] = fl[c].fillna(np.inf)
        if c == "costo_fijo_diario_usd":
            fl[c] = fl[c].fillna(0)
        malos = fl.index[fl[c].isna() | (fl[c] < minimo)]
        if len(malos):
            err.append(f"En **flota**, `{c}` vacía, no numérica o menor que {minimo} (filas {_filas(malos)}).")
    dup = fl.index[fl.tipo_avion.duplicated()]
    if len(dup):
        err.append(f"En **flota** hay tipos repetidos: {', '.join(fl.tipo_avion[dup].unique())}.")
    if not err:
        fl["cantidad_disponible"] = fl["cantidad_disponible"].round().astype(int)
        fl["asientos"] = fl["asientos"].round().astype(int)
        fl["tiempo_giro_min"] = fl["tiempo_giro_min"].round().astype(int)

    # --- 4. Vuelos ---
    v = limpias["vuelos"]
    v["id_vuelo"] = v["id_vuelo"].astype(str).str.strip()
    for c in ("origen", "destino"):
        v[c] = v[c].astype(str).str.strip().str.upper()
    v["salida_min"] = v["hora_salida"].map(a_minutos)
    malos = v.index[v.salida_min.isna()]
    if len(malos):
        err.append(f"En **vuelos**, `hora_salida` no se entiende (usar HH:MM) en filas {_filas(malos)}.")

    if "duracion_min" in v.columns:
        v["duracion_min"] = _a_numero(v["duracion_min"])
    if "hora_llegada" in v.columns:
        llegada = v["hora_llegada"].map(a_minutos)
        dur_llegada = (llegada - v.salida_min) % DIA
        if "duracion_min" in v.columns:
            v["duracion_min"] = v["duracion_min"].fillna(dur_llegada)
        else:
            v["duracion_min"] = dur_llegada
    malos = v.index[v.duracion_min.isna() | (v.duracion_min <= 0)]
    if len(malos):
        err.append(f"En **vuelos**, duración vacía o no positiva (revisar `hora_llegada`/`duracion_min`) "
                   f"en filas {_filas(malos)}.")

    for c in ("demanda_esperada", "tarifa_promedio_usd"):
        v[c] = _a_numero(v[c])
        malos = v.index[v[c].isna() | (v[c] < 0)]
        if len(malos):
            err.append(f"En **vuelos**, `{c}` vacía, no numérica o negativa (filas {_filas(malos)}).")

    dup = v.index[v.id_vuelo.duplicated()]
    if len(dup):
        err.append(f"En **vuelos** hay `id_vuelo` repetidos: {', '.join(v.id_vuelo[dup].unique()[:8])}.")
    iguales = v.index[v.origen == v.destino]
    if len(iguales):
        err.append(f"En **vuelos** hay vuelos con origen igual a destino (filas {_filas(iguales)}).")
    desconocidos = sorted((set(v.origen) | set(v.destino)) - codigos)
    if desconocidos:
        err.append(f"Estos aeropuertos aparecen en **vuelos** pero no en **aeropuertos**: "
                   f"{', '.join(desconocidos)}.")

    if err:
        return datos

    v["salida_min"] = v["salida_min"].astype(int)
    v["duracion_min"] = v["duracion_min"].round().astype(int)
    v["hora_salida"] = v["salida_min"].map(hhmm)
    v["hora_llegada"] = (v["salida_min"] + v["duracion_min"]).map(hhmm)

    # distancia: si no viene, se calcula con las coordenadas
    coords = a.set_index("codigo_iata")[["latitud", "longitud"]]
    calculada = [haversine_km(*coords.loc[o], *coords.loc[d]) for o, d in zip(v.origen, v.destino)]
    if "distancia_km" not in v.columns:
        v["distancia_km"] = np.nan
    v["distancia_km"] = _a_numero(v["distancia_km"])
    faltantes = int(v.distancia_km.isna().sum())
    v["distancia_km"] = v["distancia_km"].fillna(pd.Series(calculada, index=v.index)).round()
    if faltantes:
        adv.append(f"Se calculó `distancia_km` con las coordenadas en {faltantes} vuelos.")

    # el día se repite: en cada aeropuerto tienen que salir tantos vuelos como llegan
    salen = v.groupby("origen").size()
    llegan = v.groupby("destino").size()
    balance = salen.sub(llegan, fill_value=0)
    desbalance = balance[balance != 0]
    if len(desbalance):
        detalle = ", ".join(
            f"{ap} ({int(salen.get(ap, 0))} salen / {int(llegan.get(ap, 0))} llegan)"
            for ap in desbalance.index[:10])
        err.append("El itinerario se repite todos los días, así que en cada aeropuerto tienen que salir "
                   f"tantos vuelos como llegan. No se cumple en: {detalle}.")

    # vuelos que ningún avión puede hacer
    max_alcance = fl.alcance_km.max()
    largos = v.index[v.distancia_km > max_alcance]
    if len(largos):
        err.append(f"Hay vuelos más largos que el alcance de todos los aviones "
                   f"({', '.join(v.id_vuelo[largos][:8])}).")
    muy_largos = v.index[v.duracion_min + fl.tiempo_giro_min.max() >= DIA]
    if len(muy_largos):
        err.append(f"Hay vuelos que con el tiempo de giro duran un día o más ({', '.join(v.id_vuelo[muy_largos][:8])}).")

    max_asientos = fl.asientos.max()
    excedidos = int((v.demanda_esperada > max_asientos).sum())
    if excedidos:
        adv.append(f"{excedidos} vuelos tienen más demanda que el avión más grande ({max_asientos} asientos): "
                   "ahí siempre van a quedar pasajeros sin lugar.")
    sin_uso = sorted(codigos - set(v.origen) - set(v.destino))
    if sin_uso:
        adv.append(f"Aeropuertos sin vuelos (se ignoran): {', '.join(sin_uso)}.")

    if not err:
        datos.vuelos = v[["id_vuelo", "origen", "destino", "hora_salida", "hora_llegada", "salida_min",
                          "duracion_min", "distancia_km", "demanda_esperada", "tarifa_promedio_usd"]]
        datos.flota = fl[["tipo_avion", "cantidad_disponible", "asientos", "costo_hora_bloque_usd",
                          "costo_fijo_diario_usd", "tiempo_giro_min", "alcance_km"]]
        datos.aeropuertos = a[["codigo_iata", "ciudad", "provincia", "latitud", "longitud", "es_base"]]
    return datos


# ---------------------------------------------------------------------------
# Modelo
# ---------------------------------------------------------------------------
@dataclass
class Resultado:
    estado: str
    tiempo_s: float = 0.0
    costo_total: float = 0.0
    costo_operativo: float = 0.0
    costo_fijo: float = 0.0
    costo_pasajeros_perdidos: float = 0.0
    hora_conteo: int = 240
    asignacion: pd.DataFrame | None = None       # una fila por vuelo
    rotaciones: pd.DataFrame | None = None       # una fila por (avión, vuelo)
    aviones: pd.DataFrame | None = None          # una fila por avión
    resumen_flota: pd.DataFrame | None = None    # una fila por tipo de avión
    faltan_aviones: dict | None = None           # si es infactible: cuántos aviones faltan por tipo
    mensaje: str = ""
    n_variables: int = 0
    n_restricciones: int = 0

    @property
    def optimo(self) -> bool:
        return self.estado == "Optimal"


def _elegir_hora_conteo(salidas, fines) -> int:
    """Momento del día con menos aviones en el aire o en giro (idealmente ninguno)."""
    cobertura = np.zeros(DIA)
    for s, f in zip(salidas, fines):
        largo = int(f - s)
        idx = (np.arange(largo) + int(s)) % DIA
        cobertura[idx] += 1
    candidatos = np.arange(0, DIA, 5)
    distancia_a_las_4 = np.minimum(np.abs(candidatos - 240), DIA - np.abs(candidatos - 240))
    orden = np.lexsort((distancia_a_las_4, cobertura[candidatos]))
    return int(candidatos[orden[0]])


def _solver(limite_s):
    if "PULP_CBC_CMD" in pulp.listSolvers(onlyAvailable=True):
        return pulp.PULP_CBC_CMD(msg=False, timeLimit=limite_s)
    return pulp.HiGHS(msg=False, timeLimit=limite_s)


def _armar_y_resolver(v, fl, relajar, sin_limite, elastico, limite_s):
    """Arma y resuelve el modelo. v y fl ya tienen demanda y giro del escenario."""
    F = list(v.index)
    K = list(fl.index)
    permitido = {(f, k) for f in F for k in K if v.at[f, "distancia_km"] <= fl.at[k, "alcance_km"]}

    t0 = _elegir_hora_conteo(v.salida_min, v.salida_min + v.duracion_min + fl.tiempo_giro_min.max())

    def rel(minuto):
        return (int(minuto) - t0) % DIA

    cat = pulp.LpContinuous if relajar else pulp.LpBinary
    prob = pulp.LpProblem("asignacion_de_flota", pulp.LpMinimize)
    x = {(f, k): pulp.LpVariable(f"x_{i}_{j}", 0, 1, cat)
         for i, f in enumerate(F) for j, k in enumerate(K) if (f, k) in permitido}

    costo_op, costo_pp, pax_pp = {}, {}, {}
    for (f, k) in x:
        costo_op[f, k] = float(fl.at[k, "costo_hora_bloque_usd"]) * float(v.at[f, "duracion_min"]) / 60
        pp = max(0.0, float(v.at[f, "demanda"]) - float(fl.at[k, "asientos"]))
        pax_pp[f, k] = pp
        costo_pp[f, k] = pp * float(v.at[f, "tarifa_promedio_usd"])

    # red tiempo-espacio
    eventos = defaultdict(list)       # (aeropuerto, k) -> [(t_rel, "sale"/"llega", f)]
    cruza = {}                        # (f, k) -> el arco de vuelo + giro cruza la hora de conteo
    for (f, k) in x:
        sal = rel(v.at[f, "salida_min"])
        listo = rel(v.at[f, "salida_min"] + v.at[f, "duracion_min"] + fl.at[k, "tiempo_giro_min"])
        cruza[f, k] = listo <= sal
        eventos[v.at[f, "origen"], k].append((sal, "sale", f))
        eventos[v.at[f, "destino"], k].append((listo, "llega", f))

    y, nodos = {}, {}
    N = {k: [] for k in K}
    for j, k in enumerate(K):
        N[k] = [x[f, k] for f in F if (f, k) in x and cruza[f, k]]
    for (ap, k), evs in eventos.items():
        tiempos = sorted({t for t, _, _ in evs})
        nodos[ap, k] = tiempos
        n = len(tiempos)
        for i in range(n):
            y[ap, k, i] = pulp.LpVariable(f"y_{ap}_{K.index(k)}_{i}", 0)
        N[k].append(y[ap, k, n - 1])  # arco de pernocte: del último evento al primero, cruza la hora de conteo
        llegan_en = defaultdict(list)
        salen_en = defaultdict(list)
        for t, tipo, f in evs:
            (llegan_en if tipo == "llega" else salen_en)[t].append(x[f, k])
        for i, t in enumerate(tiempos):
            entra = y[ap, k, i - 1] if i > 0 else y[ap, k, n - 1]
            prob += (entra + pulp.lpSum(llegan_en[t]) == y[ap, k, i] + pulp.lpSum(salen_en[t]),
                     f"balance_{ap}_{K.index(k)}_{i}")

    for i, f in enumerate(F):
        prob += pulp.lpSum(x[f, k] for k in K if (f, k) in x) == 1, f"cubrir_{i}"

    N_expr = {k: pulp.lpSum(N[k]) for k in K}
    restr_flota, extra = {}, {}
    for j, k in enumerate(K):
        if sin_limite:
            continue
        limite = int(fl.at[k, "cantidad_disponible"])
        if elastico:
            extra[k] = pulp.LpVariable(f"extra_{j}", 0, None, pulp.LpInteger)
            restr_flota[k] = N_expr[k] <= limite + extra[k]
        else:
            restr_flota[k] = N_expr[k] <= limite
        prob += restr_flota[k], f"flota_{j}"

    objetivo = (pulp.lpSum((costo_op[f, k] + costo_pp[f, k]) * x[f, k] for (f, k) in x)
                + pulp.lpSum(float(fl.at[k, "costo_fijo_diario_usd"]) * N_expr[k] for k in K))
    if elastico:
        objetivo += 1e7 * pulp.lpSum(extra.values())
    prob += objetivo

    t_inicio = time.perf_counter()
    prob.solve(_solver(limite_s))
    tiempo = time.perf_counter() - t_inicio

    return dict(prob=prob, x=x, y=y, N=N_expr, nodos=nodos, eventos=eventos, cruza=cruza,
                restr_flota=restr_flota, extra=extra, costo_op=costo_op, costo_pp=costo_pp,
                pax_pp=pax_pp, t0=t0, rel=rel, tiempo=tiempo, K=K, F=F)


def _escenario(datos: Datos, factor_demanda, giro_extra, cantidades):
    v = datos.vuelos.copy().set_index("id_vuelo", drop=False)
    v["demanda"] = (v["demanda_esperada"] * factor_demanda).round()
    fl = datos.flota.copy().set_index("tipo_avion", drop=False)
    fl["tiempo_giro_min"] = fl["tiempo_giro_min"] + int(giro_extra)
    if cantidades:
        for k, n in cantidades.items():
            if k in fl.index:
                fl.at[k, "cantidad_disponible"] = int(n)
    return v, fl


def resolver(datos: Datos, factor_demanda: float = 1.0, giro_extra: int = 0,
             cantidades: dict | None = None, sin_limite: bool = False,
             limite_s: int = 120, con_rotaciones: bool = True) -> Resultado:
    """Resuelve la asignación de flota para un escenario y arma la solución para mostrar."""
    v, fl = _escenario(datos, factor_demanda, giro_extra, cantidades)
    m = _armar_y_resolver(v, fl, relajar=False, sin_limite=sin_limite, elastico=False, limite_s=limite_s)
    prob = m["prob"]
    estado = pulp.LpStatus[prob.status]
    res = Resultado(estado=estado, tiempo_s=m["tiempo"], hora_conteo=m["t0"],
                    n_variables=len(prob.variables()), n_restricciones=len(prob.constraints))

    if estado != "Optimal" or prob.sol_status not in (1, 2):
        res.estado = "Infeasible" if estado in ("Infeasible", "Not Solved") else estado
        # diagnóstico: cuántos aviones de cada tipo harían falta
        d = _armar_y_resolver(v, fl, relajar=False, sin_limite=False, elastico=True, limite_s=limite_s)
        if pulp.LpStatus[d["prob"].status] == "Optimal":
            faltan = {k: int(round(d["extra"][k].value() or 0)) for k in d["K"]}
            res.faltan_aviones = {k: n for k, n in faltan.items() if n > 0}
            detalle = ", ".join(f"{n} {k}" for k, n in res.faltan_aviones.items())
            res.mensaje = (f"Con la flota cargada no alcanza para cubrir todos los vuelos. "
                           f"Haría falta sumar al menos: {detalle}.")
        else:
            res.mensaje = "El modelo no tiene solución y no se pudo diagnosticar el motivo."
        return res

    x, K = m["x"], m["K"]
    tipo_de = {f: k for (f, k), var in x.items() if (var.value() or 0) > 0.5}

    asig = v.drop(columns=["salida_min"]).copy()
    asig["tipo_avion"] = asig.id_vuelo.map(tipo_de)
    asig["asientos"] = asig.tipo_avion.map(fl.asientos).astype(int)
    asig["pasajeros"] = np.minimum(asig.demanda, asig.asientos).astype(int)
    asig["pasajeros_sin_lugar"] = (asig.demanda - asig.pasajeros).clip(lower=0).astype(int)
    asig["ocupacion"] = asig.pasajeros / asig.asientos
    asig["costo_operativo_usd"] = [m["costo_op"][f, tipo_de[f]] for f in asig.id_vuelo]
    asig["costo_pasajeros_perdidos_usd"] = [m["costo_pp"][f, tipo_de[f]] for f in asig.id_vuelo]
    asig = asig.round({"costo_operativo_usd": 2, "costo_pasajeros_perdidos_usd": 2, "ocupacion": 4})

    usados = {k: int(round(pulp.value(m["N"][k]) or 0)) for k in K}
    res.costo_operativo = float(asig.costo_operativo_usd.sum())
    res.costo_pasajeros_perdidos = float(asig.costo_pasajeros_perdidos_usd.sum())
    res.costo_fijo = float(sum(usados[k] * fl.at[k, "costo_fijo_diario_usd"] for k in K))
    res.costo_total = res.costo_operativo + res.costo_fijo + res.costo_pasajeros_perdidos

    if con_rotaciones:
        avion_de, rot, aviones = _armar_rotaciones(m, v, fl, tipo_de)
        asig["avion"] = asig.id_vuelo.map(avion_de)
        res.rotaciones, res.aviones = rot, aviones

    asig = asig.rename(columns={"demanda": "demanda_escenario"})
    res.asignacion = asig.sort_values(["hora_salida", "origen"]).reset_index(drop=True)

    resumen = []
    for k in K:
        vk = asig[asig.tipo_avion == k]
        horas = vk.duracion_min.sum() / 60
        resumen.append({
            "tipo_avion": k,
            "disponibles": None if sin_limite else int(fl.at[k, "cantidad_disponible"]),
            "usados": usados[k],
            "vuelos": len(vk),
            "horas_de_vuelo": round(horas, 1),
            "horas_por_avion": round(horas / usados[k], 1) if usados[k] else 0.0,
            "ocupacion_media": vk.pasajeros.sum() / vk.asientos.sum() if len(vk) else 0.0,
        })
    res.resumen_flota = pd.DataFrame(resumen)
    return res


def _armar_rotaciones(m, v, fl, tipo_de):
    """Convierte los flujos de la red en el recorrido de cada avión (primero en llegar, primero en salir)."""
    rel, t0, K = m["rel"], m["t0"], m["K"]
    avion_de, filas, resumen = {}, [], []
    for k in K:
        vuelos_k = [f for f, kk in tipo_de.items() if kk == k]
        if not vuelos_k:
            continue
        pools = defaultdict(deque)
        lineas = {}
        contador = 0

        def nuevo(inicio):
            nonlocal contador
            contador += 1
            ident = f"{k} #{contador:02d}"
            lineas[ident] = {"inicio": inicio, "vuelos": []}
            return ident

        # aviones en tierra a la hora de conteo (arco de pernocte de cada aeropuerto)
        for (ap, kk), tiempos in m["nodos"].items():
            if kk != k:
                continue
            en_tierra = int(round(m["y"][ap, k, len(tiempos) - 1].value() or 0))
            for _ in range(en_tierra):
                pools[ap].append(nuevo(ap))

        listo = {f: rel(v.at[f, "salida_min"] + v.at[f, "duracion_min"] + fl.at[k, "tiempo_giro_min"])
                 for f in vuelos_k}
        eventos = []
        for f in vuelos_k:
            eventos.append((rel(v.at[f, "salida_min"]), 1, f))           # despegue
            if m["cruza"][f, k]:
                # el avión de este vuelo está en el aire a la hora de conteo: aparece cuando llega
                eventos.append((listo[f], 0, ("inicial", f)))
            else:
                eventos.append((listo[f], 0, f))                           # disponible otra vez
        eventos.sort(key=lambda e: (e[0], e[1]))

        for _, tipo, f in eventos:
            if tipo == 0:
                if isinstance(f, tuple):
                    ident = nuevo(v.at[f[1], "destino"])
                    pools[v.at[f[1], "destino"]].append(ident)
                else:
                    pools[v.at[f, "destino"]].append(avion_de[f])
            else:
                ap = v.at[f, "origen"]
                ident = pools[ap].popleft() if pools[ap] else nuevo(ap)
                avion_de[f] = ident
                lineas[ident]["vuelos"].append(f)

        for ident, linea in lineas.items():
            if not linea["vuelos"]:
                continue
            recorrido = [linea["inicio"]]
            minutos = 0
            for orden, f in enumerate(linea["vuelos"], start=1):
                recorrido.append(v.at[f, "destino"])
                minutos += v.at[f, "duracion_min"]
                inicio = t0 + rel(v.at[f, "salida_min"])
                filas.append({
                    "avion": ident, "tipo_avion": k, "orden": orden, "id_vuelo": f,
                    "origen": v.at[f, "origen"], "destino": v.at[f, "destino"],
                    "hora_salida": v.at[f, "hora_salida"], "hora_llegada": v.at[f, "hora_llegada"],
                    "inicio_min": inicio, "fin_min": inicio + v.at[f, "duracion_min"],
                })
            resumen.append({
                "avion": ident, "tipo_avion": k, "vuelos": len(linea["vuelos"]),
                "horas_de_vuelo": round(minutos / 60, 1),
                "arranca_en": linea["inicio"], "duerme_en": recorrido[-1],
                "recorrido": " → ".join(recorrido),
            })
    return avion_de, pd.DataFrame(filas), pd.DataFrame(resumen)


# ---------------------------------------------------------------------------
# Sensibilidad
# ---------------------------------------------------------------------------
def precios_sombra(datos: Datos, factor_demanda=1.0, giro_extra=0, cantidades=None,
                   limite_s: int = 120) -> dict:
    """Resuelve la relajación lineal y devuelve el precio sombra de la restricción de flota de cada tipo.

    Un precio sombra de −5.000 significa: un avión más de ese tipo bajaría el costo diario en USD 5.000
    (mientras no cambie la base). Si el óptimo de la relajación coincide con el entero, es exacto.
    """
    v, fl = _escenario(datos, factor_demanda, giro_extra, cantidades)
    m = _armar_y_resolver(v, fl, relajar=True, sin_limite=False, elastico=False, limite_s=limite_s)
    if pulp.LpStatus[m["prob"].status] != "Optimal":
        return {"estado": pulp.LpStatus[m["prob"].status], "precios": {}, "costo_lp": None}
    precios = {k: float(c.pi) if c.pi is not None else 0.0 for k, c in m["restr_flota"].items()}
    return {"estado": "Optimal", "precios": precios, "costo_lp": float(pulp.value(m["prob"].objective))}


def ahorro_un_avion_mas(datos: Datos, base: Resultado, factor_demanda=1.0, giro_extra=0,
                        cantidades=None) -> pd.DataFrame:
    """Re-resuelve sumando un avión de cada tipo (de a uno) y mide el ahorro real."""
    actuales = dict(zip(datos.flota.tipo_avion, datos.flota.cantidad_disponible))
    if cantidades:
        actuales.update(cantidades)
    filas = []
    for k in actuales:
        prueba = dict(actuales)
        prueba[k] += 1
        r = resolver(datos, factor_demanda, giro_extra, prueba, con_rotaciones=False)
        filas.append({
            "tipo_avion": k,
            "ahorro_usd_dia": base.costo_total - r.costo_total if r.optimo else None,
            "ahorro_usd_anio": (base.costo_total - r.costo_total) * 365 if r.optimo else None,
        })
    return pd.DataFrame(filas)


def correr_escenarios(datos: Datos, escenarios: list[dict]) -> pd.DataFrame:
    """Resuelve una lista de escenarios: cada uno es un dict con factor_demanda, giro_extra, cantidades."""
    filas = []
    for esc in escenarios:
        r = resolver(datos, esc.get("factor_demanda", 1.0), esc.get("giro_extra", 0),
                     esc.get("cantidades"), con_rotaciones=False)
        fila = {
            "escenario": esc.get("nombre", ""),
            "demanda": esc.get("factor_demanda", 1.0),
            "giro_extra_min": esc.get("giro_extra", 0),
            "estado": "Óptimo" if r.optimo else "Sin solución",
        }
        if r.optimo:
            a = r.asignacion
            fila.update({
                "costo_total_usd": r.costo_total,
                "aviones_usados": int(r.resumen_flota.usados.sum()),
                "pasajeros_sin_lugar": int(a.pasajeros_sin_lugar.sum()),
                "pct_sin_lugar": a.pasajeros_sin_lugar.sum() / a.demanda_escenario.sum(),
                "ocupacion_media": a.pasajeros.sum() / a.asientos.sum(),
            })
        else:
            fila["faltan_aviones"] = ", ".join(f"{n} {k}" for k, n in (r.faltan_aviones or {}).items())
        filas.append(fila)
    return pd.DataFrame(filas)
