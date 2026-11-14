"""
Optimizador de flota — prototipo del TAF (Streamlit).

Correr con:   streamlit run app.py
"""
import hashlib
import io
import json
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import optimizador as opt

AQUI = Path(__file__).parent
EJEMPLOS = {
    "Ejemplo chico (102 vuelos)": AQUI / "datos",
    "Ejemplo grande (200 vuelos)": AQUI / "datos_grande",
}
SUBIR = "Subir mis archivos"

# Paleta categórica en orden fijo (un color por tipo de avión, según el orden de flota.csv)
PALETA = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
GRIS = "#8a8984"
TINTA = "#0b0b0b"

st.set_page_config(page_title="Optimizador de flota", page_icon="✈️", layout="wide")


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def usd(x: float) -> str:
    return "USD " + f"{x:,.0f}".replace(",", ".")


def num(x: float, dec: int = 0) -> str:
    s = f"{x:,.{dec}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def colores(tipos) -> dict:
    return {t: (PALETA[i] if i < len(PALETA) else GRIS) for i, t in enumerate(tipos)}


@st.cache_data
def cargar_ejemplo(carpeta: str) -> dict:
    return {n: opt.leer_tabla(Path(carpeta) / f"{n}.csv") for n in opt.TABLAS}


def firma(tablas: dict, params: dict) -> str:
    h = hashlib.md5()
    for n in opt.TABLAS:
        df = tablas.get(n)
        h.update((df.to_csv(index=False) if df is not None else "-").encode())
    h.update(json.dumps(params, sort_keys=True, default=str).encode())
    return h.hexdigest()


def excel_resultados(res: opt.Resultado) -> bytes:
    hojas = {
        "asignacion": res.asignacion,
        "rotaciones": res.rotaciones.drop(columns=["inicio_min", "fin_min"]),
        "aviones": res.aviones,
        "resumen_flota": res.resumen_flota,
    }
    return opt.plantilla_excel(hojas)


# ---------------------------------------------------------------------------
# Barra lateral: datos y escenario
# ---------------------------------------------------------------------------
with st.sidebar:
    st.title("✈️ Optimizador de flota")
    st.caption("Asigna un tipo de avión a cada vuelo y arma la rotación de cada avión al menor costo.")

    st.subheader("1. Datos")
    fuente = st.radio("Origen de los datos", [*EJEMPLOS, SUBIR], label_visibility="collapsed")

    tablas_crudas: dict = {}
    clave_fuente = fuente
    if fuente == SUBIR:
        formato = st.radio("Formato", ["3 archivos CSV", "1 Excel con 3 hojas"], horizontal=True)
        try:
            if formato == "3 archivos CSV":
                subidos = {}
                for n in opt.TABLAS:
                    subidos[n] = st.file_uploader(f"{n}.csv", type=["csv", "xlsx"], key=f"up_{n}")
                for n, archivo in subidos.items():
                    if archivo is not None:
                        tablas_crudas[n] = opt.leer_tabla(archivo)
                clave_fuente += "|" + "|".join(f"{a.name}:{a.size}" for a in subidos.values() if a)
            else:
                archivo = st.file_uploader("Excel con hojas vuelos, flota y aeropuertos", type=["xlsx"])
                if archivo is not None:
                    tablas_crudas = opt.leer_excel(archivo)
                    clave_fuente += f"|{archivo.name}:{archivo.size}"
        except Exception as e:  # archivo ilegible
            st.error(f"No se pudo leer el archivo: {e}")
        plantilla = opt.plantilla_excel(cargar_ejemplo(str(EJEMPLOS["Ejemplo chico (102 vuelos)"])))
        st.download_button("Descargar plantilla de ejemplo (Excel)", plantilla,
                           file_name="plantilla_flota.xlsx", icon="⬇️", width="stretch")
    else:
        tablas_crudas = cargar_ejemplo(str(EJEMPLOS[fuente]))

    st.subheader("2. Escenario")
    factor = st.slider("Demanda respecto del pronóstico", 50, 150, 100, 5, format="%d%%",
                       help="100% = la demanda_esperada del archivo. 120% simula una temporada más fuerte.") / 100
    giro_extra = st.number_input("Minutos extra de tiempo de giro", 0, 120, 0, 5,
                                 help="Se suman al tiempo de giro de todos los tipos (prueba de robustez).")
    sin_limite = st.toggle("Sin límite de aviones",
                           help="Ignora cantidad_disponible: responde cuántos aviones de cada tipo convendría tener.")

    st.subheader("3. Optimizar")
    lugar_boton = st.empty()
    lugar_estado = st.empty()


# ---------------------------------------------------------------------------
# Pestañas
# ---------------------------------------------------------------------------
t_datos, t_resumen, t_asig, t_rot, t_mapa, t_sens = st.tabs(
    ["Datos", "Resumen", "Asignación", "Rotaciones", "Mapa", "Sensibilidad"])

# --- Datos: tablas editables + validación ---
with t_datos:
    faltan = [n for n in opt.TABLAS if n not in tablas_crudas]
    if fuente == SUBIR and faltan:
        st.info("Subí los tres archivos en la barra lateral (o un Excel con las tres hojas). "
                "Abajo está la estructura que tienen que tener.")

    with st.expander("Estructura de los archivos", expanded=(fuente == SUBIR)):
        filas = []
        for n, reglas in opt.COLUMNAS.items():
            filas += [{"tabla": n, "columna": c, "obligatoria": "sí"} for c in reglas["obligatorias"]]
            filas += [{"tabla": n, "columna": " o ".join(g), "obligatoria": "una de las dos"} for g in reglas["una_de"]]
            filas += [{"tabla": n, "columna": c, "obligatoria": "no"} for c in reglas["opcionales"]]
        st.dataframe(pd.DataFrame(filas), hide_index=True, width="stretch")
        st.caption("Horas en formato HH:MM. Se aceptan CSV separados por coma o punto y coma, y decimales con coma. "
                   "Cada vuelo necesita su vuelta: el itinerario se repite todos los días.")

    tablas = {}
    if not faltan:
        st.caption("Podés editar las tablas acá mismo (agregar o borrar filas, cambiar valores) antes de optimizar.")
        c1, c2 = st.columns([3, 2])
        with c2:
            st.markdown("**Flota**")
            tablas["flota"] = st.data_editor(tablas_crudas["flota"], num_rows="dynamic", hide_index=True,
                                             key=f"ed_flota_{clave_fuente}", width="stretch")
            st.markdown("**Aeropuertos**")
            tablas["aeropuertos"] = st.data_editor(tablas_crudas["aeropuertos"], num_rows="dynamic",
                                                   hide_index=True, key=f"ed_aer_{clave_fuente}",
                                                   width="stretch", height=300)
        with c1:
            st.markdown(f"**Vuelos** ({len(tablas_crudas['vuelos'])} filas)")
            tablas["vuelos"] = st.data_editor(tablas_crudas["vuelos"], num_rows="dynamic", hide_index=True,
                                              key=f"ed_vuelos_{clave_fuente}", width="stretch", height=560)

    datos = opt.validar(tablas) if tablas else opt.Datos(errores=["Faltan datos."])
    if tablas:
        if datos.errores:
            st.error("**Hay que corregir esto antes de optimizar:**\n\n" +
                     "\n".join(f"- {e}" for e in datos.errores))
        else:
            v, fl = datos.vuelos, datos.flota
            st.success(f"Datos válidos: {len(v)} vuelos, {v.origen.nunique()} aeropuertos con vuelos, "
                       f"{len(fl)} tipos de avión, {int(fl.cantidad_disponible.sum())} aviones disponibles.")
        for a in datos.advertencias:
            st.warning(a)

# --- Botón y resolución ---
params = {"factor": factor, "giro_extra": int(giro_extra), "sin_limite": bool(sin_limite)}
firma_actual = firma(tablas, params) if tablas else None
apretado = lugar_boton.button("Optimizar", type="primary", icon="▶️", width="stretch", disabled=not datos.ok)

if apretado and datos.ok:
    with st.spinner("Resolviendo el modelo…"):
        res = opt.resolver(datos, factor, int(giro_extra), sin_limite=sin_limite)
        sombra = None
        if res.optimo and not sin_limite:
            sombra = opt.precios_sombra(datos, factor, int(giro_extra))
    st.session_state["resultado"] = {"firma": firma_actual, "res": res, "sombra": sombra,
                                     "params": params, "datos": datos}
    st.session_state.pop("ahorro", None)
    st.session_state.pop("escenarios", None)

guardado = st.session_state.get("resultado")
vigente = guardado is not None and guardado["firma"] == firma_actual
if guardado is None:
    lugar_estado.caption("Todavía no se optimizó.")
elif not vigente:
    lugar_estado.warning("Cambiaron los datos o el escenario: volvé a optimizar.")
else:
    r = guardado["res"]
    lugar_estado.caption(f"Resuelto en {num(r.tiempo_s, 2)} s · " + ("solución óptima" if r.optimo else "sin solución"))


def sin_resultado(contenedor):
    with contenedor:
        if guardado is None:
            st.info("Cargá los datos y apretá **Optimizar** en la barra lateral.")
        elif not vigente:
            st.warning("Los resultados de abajo no corresponden a los datos o al escenario actual. Volvé a optimizar.")


# ---------------------------------------------------------------------------
# Resultados
# ---------------------------------------------------------------------------
if guardado is None:
    for t in (t_resumen, t_asig, t_rot, t_mapa, t_sens):
        sin_resultado(t)
    st.stop()

res: opt.Resultado = guardado["res"]
datos_res: opt.Datos = guardado["datos"]
p = guardado["params"]
for t in (t_resumen, t_asig, t_rot, t_mapa, t_sens):
    if not vigente:
        sin_resultado(t)

if not res.optimo:
    for t in (t_resumen, t_asig, t_rot, t_mapa, t_sens):
        with t:
            st.error(f"**No hay solución.** {res.mensaje}")
            st.caption("Opciones: sumar esos aviones en la tabla de flota, sacar vuelos del itinerario, "
                       "o activar **Sin límite de aviones** para ver la flota que haría falta.")
    st.stop()

tipos = list(datos_res.flota.tipo_avion)
color_de = colores(tipos)
asig = res.asignacion
rf = res.resumen_flota

# --- Resumen ---
with t_resumen:
    escenario = []
    if p["factor"] != 1:
        escenario.append(f"demanda al {int(p['factor'] * 100)}%")
    if p["giro_extra"]:
        escenario.append(f"giro +{p['giro_extra']} min")
    if p["sin_limite"]:
        escenario.append("sin límite de aviones")
    st.subheader("Resultado del día" + (f" · {', '.join(escenario)}" if escenario else ""))

    demanda_total = asig.demanda_escenario.sum()
    perdidos = asig.pasajeros_sin_lugar.sum()
    usados = int(rf.usados.sum())
    disponibles = int(datos_res.flota.cantidad_disponible.sum())
    c = st.columns(4)
    c[0].metric("Costo total por día (USD)", num(res.costo_total), border=True)
    c[1].metric("Aviones usados", f"{usados}" + ("" if p["sin_limite"] else f" de {disponibles}"), border=True)
    c[2].metric("Pasajeros sin lugar", f"{num(perdidos)} ({num(100 * perdidos / demanda_total, 1)}%)", border=True,
                help="Entre paréntesis, el porcentaje de la demanda que no entra en los aviones asignados.")
    c[3].metric("Ocupación media", f"{num(100 * asig.pasajeros.sum() / asig.asientos.sum(), 1)}%", border=True)

    st.markdown("##### De dónde sale el costo")
    desglose = pd.DataFrame({
        "concepto": ["Operación de los vuelos (costo por hora × duración)",
                     "Aviones en servicio (costo fijo diario)",
                     "Pasajeros sin lugar (tarifa perdida)"],
        "usd_por_dia": [round(res.costo_operativo), round(res.costo_fijo), round(res.costo_pasajeros_perdidos)],
    })
    desglose["porcentaje"] = desglose.usd_por_dia / res.costo_total
    st.dataframe(desglose, hide_index=True, width="stretch", column_config={
        "concepto": "Concepto",
        "usd_por_dia": st.column_config.NumberColumn("USD por día", format="localized"),
        "porcentaje": st.column_config.ProgressColumn("Peso", min_value=0, max_value=1, format="percent"),
    })

    st.markdown("##### Por tipo de avión")
    st.dataframe(rf, hide_index=True, width="stretch", column_config={
        "tipo_avion": "Tipo",
        "disponibles": st.column_config.NumberColumn("Disponibles"),
        "usados": st.column_config.NumberColumn("Usados"),
        "vuelos": st.column_config.NumberColumn("Vuelos"),
        "horas_de_vuelo": st.column_config.NumberColumn("Horas de vuelo", format="localized"),
        "horas_por_avion": st.column_config.NumberColumn("Horas por avión", format="localized"),
        "ocupacion_media": st.column_config.ProgressColumn("Ocupación", min_value=0, max_value=1, format="percent"),
    })
    st.caption(f"Modelo: {num(res.n_variables)} variables y {num(res.n_restricciones)} restricciones · "
               f"resuelto en {num(res.tiempo_s, 2)} s · los aviones se cuentan a las {opt.hhmm(res.hora_conteo)}, "
               "cuando hay menos aviones en el aire.")

# --- Asignación ---
with t_asig:
    st.subheader("Qué avión hace cada vuelo")
    f1, f2 = st.columns(2)
    sel_tipos = f1.multiselect("Tipo de avión", tipos, default=tipos, key="asig_tipos")
    aeropuertos = sorted(set(asig.origen) | set(asig.destino))
    sel_aer = f2.multiselect("Aeropuerto (origen o destino)", aeropuertos, key="asig_aer",
                             placeholder="Todos")
    vista = asig[asig.tipo_avion.isin(sel_tipos)]
    if sel_aer:
        vista = vista[vista.origen.isin(sel_aer) | vista.destino.isin(sel_aer)]
    columnas = ["id_vuelo", "origen", "destino", "hora_salida", "hora_llegada", "tipo_avion", "avion",
                "demanda_escenario", "asientos", "pasajeros", "pasajeros_sin_lugar", "ocupacion",
                "costo_operativo_usd", "costo_pasajeros_perdidos_usd"]
    vista = vista[columnas].round({"costo_operativo_usd": 0, "costo_pasajeros_perdidos_usd": 0})
    st.dataframe(vista, hide_index=True, width="stretch", height=520, column_config={
        "id_vuelo": "Vuelo", "origen": "Origen", "destino": "Destino",
        "hora_salida": "Sale", "hora_llegada": "Llega", "tipo_avion": "Tipo", "avion": "Avión",
        "demanda_escenario": st.column_config.NumberColumn("Demanda", format="localized"),
        "asientos": st.column_config.NumberColumn("Asientos", format="localized"),
        "pasajeros": st.column_config.NumberColumn("Pasajeros", format="localized"),
        "pasajeros_sin_lugar": st.column_config.NumberColumn("Sin lugar", format="localized"),
        "ocupacion": st.column_config.ProgressColumn("Ocupación", min_value=0, max_value=1, format="percent"),
        "costo_operativo_usd": st.column_config.NumberColumn("Costo operativo (USD)", format="localized"),
        "costo_pasajeros_perdidos_usd": st.column_config.NumberColumn("Pasajeros perdidos (USD)", format="localized"),
    })
    st.caption(f"{len(vista)} de {len(asig)} vuelos.")
    d1, d2 = st.columns(2)
    d1.download_button("Descargar asignación (CSV)", asig[columnas].to_csv(index=False).encode("utf-8-sig"),
                       file_name="asignacion.csv", mime="text/csv", icon="⬇️", width="stretch")
    d2.download_button("Descargar todo (Excel)", excel_resultados(res), file_name="resultado_flota.xlsx",
                       icon="⬇️", width="stretch")

# --- Rotaciones ---
with t_rot:
    st.subheader("Recorrido de cada avión en el día")
    sel_rot = st.multiselect("Tipo de avión", tipos, default=tipos, key="rot_tipos")
    rot = res.rotaciones[res.rotaciones.tipo_avion.isin(sel_rot)].copy()
    if rot.empty:
        st.info("Elegí al menos un tipo de avión.")
    else:
        base = pd.Timestamp("2026-01-01")
        rot["inicio"] = base + pd.to_timedelta(rot.inicio_min, unit="min")
        rot["fin"] = base + pd.to_timedelta(rot.fin_min, unit="min")
        rot["tramo"] = rot.origen + "→" + rot.destino
        orden_aviones = (res.aviones[res.aviones.tipo_avion.isin(sel_rot)]
                         .assign(o=lambda d: d.tipo_avion.map({t: i for i, t in enumerate(tipos)}))
                         .sort_values(["o", "avion"]).avion.tolist())
        fig = px.timeline(rot, x_start="inicio", x_end="fin", y="avion", color="tipo_avion", text="tramo",
                          color_discrete_map=color_de, category_orders={"avion": orden_aviones, "tipo_avion": tipos},
                          custom_data=["id_vuelo", "hora_salida", "hora_llegada", "tipo_avion"])
        fig.update_traces(
            textposition="inside", insidetextanchor="middle", textfont=dict(size=10, color=TINTA),
            marker_line=dict(width=1, color="white"),
            hovertemplate="<b>%{customdata[0]}</b> · %{y}<br>%{text}<br>%{customdata[1]} → %{customdata[2]}<extra></extra>")
        fig.update_yaxes(title=None, showgrid=False)
        fig.update_xaxes(title=None, tickformat="%H:%M", dtick=2 * 3600 * 1000, showgrid=True,
                         gridcolor="rgba(128,128,128,0.15)")
        fig.update_layout(height=max(380, 24 * len(orden_aviones) + 120), legend_title_text="Tipo", separators=",.",
                          legend=dict(orientation="h", y=1.02, x=0, yanchor="bottom"),
                          margin=dict(l=10, r=10, t=40, b=10), bargap=0.25)
        st.plotly_chart(fig, width="stretch", key="gantt")
        st.caption(f"Cada fila es un avión; cada barra, un vuelo. El día arranca a las {opt.hhmm(res.hora_conteo)}. "
                   "Como el itinerario se repite, el avión que duerme en un aeropuerto arranca al día siguiente "
                   "un recorrido que empieza en ese aeropuerto.")

    av = res.aviones[res.aviones.tipo_avion.isin(sel_rot)]
    st.dataframe(av, hide_index=True, width="stretch", column_config={
        "avion": "Avión", "tipo_avion": "Tipo", "vuelos": "Vuelos",
        "horas_de_vuelo": st.column_config.NumberColumn("Horas de vuelo", format="localized"),
        "arranca_en": "Arranca en", "duerme_en": "Duerme en", "recorrido": "Recorrido",
    })
    st.download_button("Descargar rotaciones (CSV)",
                       res.rotaciones.drop(columns=["inicio_min", "fin_min"]).to_csv(index=False).encode("utf-8-sig"),
                       file_name="rotaciones.csv", mime="text/csv", icon="⬇️")

# --- Mapa ---
with t_mapa:
    st.subheader("Red de rutas")
    fondo = st.radio("Fondo", ["Mapa (necesita internet)", "Liso (sin internet)"], horizontal=True,
                     key="fondo_mapa", label_visibility="collapsed")
    aer = datos_res.aeropuertos.set_index("codigo_iata")
    rutas = asig.assign(par=[" – ".join(sorted(x)) for x in zip(asig.origen, asig.destino)])
    fig = go.Figure()
    for t in tipos:
        sub = rutas[rutas.tipo_avion == t]
        if sub.empty:
            continue
        lat, lon = [], []
        for par in sub.par.unique():
            a, b = par.split(" – ")
            lat += [aer.at[a, "latitud"], aer.at[b, "latitud"], None]
            lon += [aer.at[a, "longitud"], aer.at[b, "longitud"], None]
        fig.add_trace(go.Scattermap(lat=lat, lon=lon, mode="lines", name=t, hoverinfo="skip",
                                    line=dict(width=2, color=color_de[t]), opacity=0.8))
    movimientos = pd.concat([asig.origen, asig.destino]).value_counts()
    usados_ap = aer.loc[movimientos.index]
    por_ap = (pd.concat([asig[["origen", "tipo_avion"]].rename(columns={"origen": "ap"}),
                         asig[["destino", "tipo_avion"]].rename(columns={"destino": "ap"})])
              .groupby(["ap", "tipo_avion"]).size().unstack(fill_value=0))
    hover = []
    for ap in movimientos.index:
        detalle = "<br>".join(f"{t}: {int(por_ap.at[ap, t])}" for t in tipos if t in por_ap.columns and por_ap.at[ap, t])
        ciudad = aer.at[ap, "ciudad"] if "ciudad" in aer.columns else ""
        hover.append(f"<b>{ap}</b> {ciudad}<br>{int(movimientos[ap])} despegues y aterrizajes<br>{detalle}")
    tam = np.clip(6 + np.sqrt(movimientos.values) * 2.2, 8, 30)
    leyenda = dict(x=0.01, y=0.99, bgcolor="rgba(255,255,255,0.85)")
    if fondo.startswith("Mapa"):
        fig.add_trace(go.Scattermap(
            lat=usados_ap.latitud, lon=usados_ap.longitud, mode="markers+text", name="Aeropuertos",
            text=list(movimientos.index), textposition="top right", textfont=dict(size=11, color=TINTA),
            marker=dict(size=tam, color="#3a3a38"), hovertext=hover, hoverinfo="text", showlegend=False))
        lat_r = usados_ap.latitud.max() - usados_ap.latitud.min()
        lon_r = usados_ap.longitud.max() - usados_ap.longitud.min()
        zoom = float(np.clip(np.log2(360 / max(lat_r * 1.8, lon_r * 1.2, 0.5)) - 0.6, 1, 10))
        fig.update_layout(
            map=dict(style="carto-positron", zoom=zoom,
                     center=dict(lat=float(usados_ap.latitud.mean()), lon=float(usados_ap.longitud.mean()))),
            height=650, margin=dict(l=0, r=0, t=0, b=0), legend_title_text="Tipo", legend=leyenda)
    else:
        # mismo dibujo en ejes comunes (longitud, latitud): no descarga nada de internet
        plano = go.Figure()
        for tr in fig.data:
            plano.add_trace(go.Scatter(x=tr.lon, y=tr.lat, mode="lines", name=tr.name, hoverinfo="skip",
                                       line=dict(width=2, color=tr.line.color), opacity=0.8))
        plano.add_trace(go.Scatter(
            x=usados_ap.longitud, y=usados_ap.latitud, mode="markers+text", text=list(movimientos.index),
            textposition="top right", textfont=dict(size=11, color=TINTA),
            marker=dict(size=tam, color="#3a3a38", line=dict(width=2, color="white")),
            hovertext=hover, hoverinfo="text", showlegend=False))
        plano.update_xaxes(visible=False)
        plano.update_yaxes(visible=False, scaleanchor="x",
                           scaleratio=1 / np.cos(np.radians(float(usados_ap.latitud.mean()))))
        plano.update_layout(height=650, margin=dict(l=0, r=0, t=0, b=0), legend_title_text="Tipo",
                            legend=leyenda, plot_bgcolor="rgba(0,0,0,0)")
        fig = plano
    st.plotly_chart(fig, width="stretch", key="mapa")
    st.caption("El tamaño de cada punto indica cuántos despegues y aterrizajes tiene el aeropuerto. "
               "Pasá el mouse por un aeropuerto para ver cuántos vuelos hace cada tipo de avión.")

    tabla_rutas = (rutas.groupby("par").agg(vuelos_por_dia=("id_vuelo", "size"),
                                             pasajeros=("pasajeros", "sum"),
                                             sin_lugar=("pasajeros_sin_lugar", "sum"),
                                             tipos=("tipo_avion", lambda s: ", ".join(f"{t} ×{n}" for t, n in s.value_counts().items())))
                   .reset_index().sort_values("vuelos_por_dia", ascending=False))
    st.dataframe(tabla_rutas, hide_index=True, width="stretch", column_config={
        "par": "Ruta (ida y vuelta)", "vuelos_por_dia": "Vuelos por día", "pasajeros": "Pasajeros",
        "sin_lugar": "Sin lugar", "tipos": "Tipos que la vuelan"})

# --- Sensibilidad ---
with t_sens:
    st.subheader("¿Cuánto vale un avión más?")
    if p["sin_limite"]:
        st.info("Con **Sin límite de aviones** no hay restricción de flota, así que no hay precio sombra. "
                "Desactivalo para ver esta sección.")
    else:
        sombra = guardado["sombra"]
        tabla = rf[["tipo_avion", "disponibles", "usados"]].copy()
        tabla["precio_sombra"] = tabla.tipo_avion.map(lambda k: round(-sombra["precios"].get(k, 0.0)) + 0)
        if "ahorro" in st.session_state:
            tabla = tabla.merge(st.session_state["ahorro"][["tipo_avion", "ahorro_usd_dia", "ahorro_usd_anio"]]
                                .round(0), on="tipo_avion", how="left")
        st.dataframe(tabla, hide_index=True, width="stretch", column_config={
            "tipo_avion": "Tipo", "disponibles": "Disponibles", "usados": "Usados",
            "precio_sombra": st.column_config.NumberColumn("Precio sombra (USD/día)", format="localized",
                                                           help="De la relajación lineal: ahorro por cada avión "
                                                                "adicional mientras no cambie la base."),
            "ahorro_usd_dia": st.column_config.NumberColumn("Ahorro real +1 avión (USD/día)", format="localized"),
            "ahorro_usd_anio": st.column_config.NumberColumn("Ahorro real +1 avión (USD/año)", format="localized"),
        })
        exacto = sombra["costo_lp"] is not None and abs(sombra["costo_lp"] - res.costo_total) < 1
        st.caption(("La relajación lineal da el mismo óptimo que el modelo entero, así que los precios sombra "
                    "son válidos. " if exacto else
                    f"Ojo: la relajación lineal da {usd(sombra['costo_lp'])}, distinto del óptimo entero; "
                    "los precios sombra son aproximados. ") +
                   "Un precio sombra de 0 indica que sobran aviones de ese tipo. El precio sombra vale para "
                   "cambios chicos; para un avión entero, el ahorro real se calcula re-optimizando.")
        if st.button("Calcular el ahorro real de sumar 1 avión de cada tipo", icon="🔁"):
            with st.spinner("Re-optimizando con un avión más de cada tipo…"):
                st.session_state["ahorro"] = opt.ahorro_un_avion_mas(datos_res, res, p["factor"], p["giro_extra"])
            st.rerun()

    st.divider()
    st.subheader("Escenarios")
    st.caption("Re-optimiza el mismo itinerario cambiando la demanda o el tiempo de giro, "
               "para ver qué tan robusta es la solución.")
    e1, e2 = st.columns(2)
    demandas = e1.multiselect("Demanda (% del pronóstico)", list(range(60, 151, 10)), default=[80, 90, 100, 110, 120],
                              format_func=lambda x: f"{x}%")
    giros = e2.multiselect("Minutos extra de giro", [0, 5, 10, 15, 20, 30, 45, 60], default=[0, 10, 20, 30])
    if st.button("Correr escenarios", type="primary", icon="▶️"):
        escenarios = [dict(nombre=f"Demanda {d}%", factor_demanda=d / 100, giro_extra=p["giro_extra"])
                      for d in sorted(demandas)]
        escenarios += [dict(nombre=f"Giro +{g} min", factor_demanda=p["factor"], giro_extra=g)
                       for g in sorted(giros)]
        with st.spinner(f"Resolviendo {len(escenarios)} escenarios…"):
            st.session_state["escenarios"] = opt.correr_escenarios(datos_res, escenarios)

    if "escenarios" in st.session_state:
        esc = st.session_state["escenarios"]

        def celda(x, fmt):
            return "—" if pd.isna(x) else fmt(x)

        vista_esc = pd.DataFrame({
            "Escenario": esc.escenario,
            "Estado": esc.estado,
            "Costo total (USD/día)": esc.get("costo_total_usd", pd.Series(np.nan, index=esc.index)).map(lambda x: celda(x, num)),
            "Aviones usados": esc.get("aviones_usados", pd.Series(np.nan, index=esc.index)).map(lambda x: celda(x, num)),
            "Pasajeros sin lugar": esc.get("pasajeros_sin_lugar", pd.Series(np.nan, index=esc.index)).map(lambda x: celda(x, num)),
            "% sin lugar": esc.get("pct_sin_lugar", pd.Series(np.nan, index=esc.index)).map(lambda x: celda(x, lambda y: f"{num(100 * y, 1)}%")),
            "Ocupación": esc.get("ocupacion_media", pd.Series(np.nan, index=esc.index)).map(lambda x: celda(x, lambda y: f"{num(100 * y, 1)}%")),
            "Faltan aviones": esc.get("faltan_aviones", pd.Series("", index=esc.index)).fillna("").replace("", "—"),
        })
        st.dataframe(vista_esc, hide_index=True, width="stretch")
        dem = esc[esc.escenario.str.startswith("Demanda") & (esc.estado == "Óptimo")]
        if len(dem) >= 2:
            g1, g2 = st.columns(2)
            fig1 = px.line(dem, x="demanda", y="costo_total_usd", markers=True,
                           labels={"demanda": "Demanda (% del pronóstico)", "costo_total_usd": "USD por día"},
                           title="Costo total según la demanda")
            fig2 = px.line(dem, x="demanda", y="pct_sin_lugar", markers=True,
                           labels={"demanda": "Demanda (% del pronóstico)", "pct_sin_lugar": "Pasajeros sin lugar"},
                           title="Pasajeros sin lugar según la demanda")
            for f in (fig1, fig2):
                f.update_traces(line=dict(width=2, color=PALETA[0]), marker=dict(size=9))
                f.update_xaxes(tickformat=".0%")
                f.update_layout(height=340, margin=dict(l=10, r=10, t=50, b=10), separators=",.")
            fig1.update_yaxes(tickformat=",.0f")
            fig2.update_yaxes(tickformat=".0%")
            g1.plotly_chart(fig1, width="stretch", key="esc_costo")
            g2.plotly_chart(fig2, width="stretch", key="esc_pp")
