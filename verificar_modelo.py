"""
Modelo base de Asignación de Flota (Fleet Assignment) sobre red tiempo-espacio,
usado para verificar que el dataset sea factible e interesante.

  x[f,k] ∈ {0,1}  : el vuelo f lo opera un avión tipo k
  y[arco]  ≥ 0    : aviones tipo k esperando en tierra en un aeropuerto entre dos eventos
  N[k]            : aviones tipo k usados = aviones en tierra a la hora de conteo (04:00)

  min Σ_f Σ_k (costo_hora_k · horas_f + tarifa_f · max(0, demanda_f − asientos_k)) · x[f,k]
      + Σ_k costo_fijo_k · N[k]
  s.a.  Σ_k x[f,k] = 1                         ∀ f        (cada vuelo, un avión)
        balance de flujo en cada nodo (aeropuerto, hora, tipo)
        N[k] ≤ cantidad_disponible_k           ∀ k        (flota)
        x[f,k] = 0 si distancia_f > alcance_k               (alcance)
"""
import sys
import time
from collections import defaultdict

import pandas as pd
import pulp

carpeta = sys.argv[1] if len(sys.argv) > 1 else "datos"
relajar = "--lp" in sys.argv

vuelos = pd.read_csv(f"{carpeta}/vuelos.csv")
flota = pd.read_csv(f"{carpeta}/flota.csv")

HORA_CONTEO = 4 * 60  # 04:00, todos los aviones están en tierra


def a_min(hhmm):
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


def t_rel(minuto):
    """Tiempo relativo a la hora de conteo, en [0, 1440)."""
    return (minuto - HORA_CONTEO) % 1440


F = list(vuelos.id_vuelo)
K = list(flota.tipo_avion)
v = vuelos.set_index("id_vuelo")
fl = flota.set_index("tipo_avion")

cat = "Continuous" if relajar else "Binary"
prob = pulp.LpProblem("asignacion_flota", pulp.LpMinimize)
x = {(f, k): pulp.LpVariable(f"x_{f}_{k}", lowBound=0, upBound=1, cat=cat) for f in F for k in K}

# Alcance: un tipo de avión no puede hacer vuelos más largos que su alcance
for f in F:
    for k in K:
        if v.loc[f, "distancia_km"] > fl.loc[k, "alcance_km"]:
            x[f, k].upBound = 0

costo_op, costo_spill, pax_perdidos = {}, {}, {}
for f in F:
    for k in K:
        costo_op[f, k] = float(fl.loc[k, "costo_hora_bloque_usd"] * v.loc[f, "duracion_min"] / 60)
        perdidos = int(max(0, v.loc[f, "demanda_esperada"] - fl.loc[k, "asientos"]))
        pax_perdidos[f, k] = perdidos
        costo_spill[f, k] = float(perdidos * v.loc[f, "tarifa_promedio_usd"])

# --- Red tiempo-espacio por tipo de avión ---
eventos = defaultdict(list)  # (aeropuerto, k) -> lista de (tiempo, tipo, vuelo)
for f in F:
    sal = t_rel(a_min(v.loc[f, "hora_salida"]))
    lle = a_min(v.loc[f, "hora_salida"]) + v.loc[f, "duracion_min"]
    for k in K:
        listo = t_rel(lle + fl.loc[k, "tiempo_giro_min"])
        assert listo > sal, f"El avión de {f} estaría listo después de la hora de conteo"
        eventos[v.loc[f, "origen"], k].append((sal, "sale", f))
        eventos[v.loc[f, "destino"], k].append((listo, "llega", f))

y, N = {}, {}
for k in K:
    N[k] = 0
for (a, k), evs in eventos.items():
    tiempos = sorted({t for t, _, _ in evs})
    # arcos de tierra entre nodos consecutivos + arco de pernocte (último -> primero)
    for i, t in enumerate(tiempos):
        y[a, k, i] = pulp.LpVariable(f"y_{a}_{k}_{i}", lowBound=0)
    n = len(tiempos)
    N[k] = N[k] + y[a, k, n - 1]  # el arco de pernocte cruza la hora de conteo
    for i, t in enumerate(tiempos):
        entra = y[a, k, i - 1] if i > 0 else y[a, k, n - 1]
        llegan = pulp.lpSum(x[f, k] for tt, tipo, f in evs if tt == t and tipo == "llega")
        salen = pulp.lpSum(x[f, k] for tt, tipo, f in evs if tt == t and tipo == "sale")
        prob += entra + llegan == y[a, k, i] + salen, f"bal_{a}_{k}_{i}"

for f in F:
    prob += pulp.lpSum(x[f, k] for k in K) == 1, f"cubrir_{f}"
restr_flota = {}
sin_limite = "--sin-limite" in sys.argv
for k in K:
    limite = 999 if sin_limite else int(fl.loc[k, "cantidad_disponible"])
    restr_flota[k] = N[k] <= limite
    prob += restr_flota[k], f"flota_{k}"

prob += (
    pulp.lpSum((costo_op[f, k] + costo_spill[f, k]) * x[f, k] for f in F for k in K)
    + pulp.lpSum(float(fl.loc[k, "costo_fijo_diario_usd"]) * N[k] for k in K)
)

t0 = time.time()
prob.solve(pulp.PULP_CBC_CMD(msg=False))
dt = time.time() - t0

print(f"Estado: {pulp.LpStatus[prob.status]}  |  tiempo: {dt:.2f}s  |  "
      f"variables: {len(prob.variables())}  restricciones: {len(prob.constraints)}")
print(f"Costo total: USD {pulp.value(prob.objective):,.0f}")
for k in K:
    usados = pulp.value(N[k])
    vuelos_k = sum(pulp.value(x[f, k]) for f in F)
    print(f"  {k:10s} aviones usados {usados:5.1f} / {fl.loc[k, 'cantidad_disponible']}"
          f"   vuelos {vuelos_k:5.1f}")
perdidos = sum(pax_perdidos[f, k] * pulp.value(x[f, k]) for f in F for k in K)
print(f"Pasajeros sin lugar: {perdidos:,.0f} de {vuelos.demanda_esperada.sum():,} "
      f"({perdidos / vuelos.demanda_esperada.sum():.1%})")
if relajar:
    for k in K:
        c = restr_flota[k]
        print(f"  precio sombra flota {k}: {c.pi:,.0f} USD/avión/día")
