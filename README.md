# TAF – Optimizador de flota de una aerolínea

Prototipo en Python (Streamlit + PuLP) que decide **qué tipo de avión hace cada vuelo** y
**arma el recorrido de cada avión en el día**, al menor costo, para cualquier itinerario que
respete la estructura de columnas de abajo.

## Cómo abrir la app

**En Windows:** doble clic en `iniciar_app.bat`. La primera vez instala las librerías
(hace falta tener Python instalado, con la opción *Add Python to PATH*). Se abre sola en el navegador.

**Desde una terminal** (en esta carpeta):

```bash
pip install -r requirements.txt
streamlit run app.py
```

### Qué hace la app

1. **Datos:** usar uno de los ejemplos o subir los tuyos (3 CSV o 1 Excel con hojas `vuelos`,
   `flota` y `aeropuertos`). Las tablas se pueden editar en la misma pantalla. Antes de optimizar
   se validan: columnas, horas, aeropuertos que no existen, vuelos sin vuelta, vuelos que ningún
   avión puede hacer por alcance. Si algo está mal, dice qué y en qué fila.
2. **Escenario:** demanda (50 % a 150 % del pronóstico), minutos extra de tiempo de giro y la
   opción *sin límite de aviones* (responde qué flota convendría tener).
3. **Resultados**, en pestañas:
   - **Resumen:** costo total, aviones usados, pasajeros sin lugar, ocupación, desglose del costo.
   - **Asignación:** tipo y avión de cada vuelo, con descarga en CSV y Excel.
   - **Rotaciones:** diagrama de Gantt con el recorrido de cada avión y dónde duerme.
   - **Mapa:** red de rutas por tipo de avión (con fondo de mapa o liso, sin internet).
   - **Sensibilidad:** precio sombra de la flota, ahorro real de sumar un avión de cada tipo y
     escenarios de demanda y tiempo de giro.

Si la flota no alcanza, la app no se queda en "infactible": calcula **cuántos aviones de cada
tipo faltan**.

## Archivos

| Archivo | Qué es |
|---|---|
| `app.py` | la app (interfaz) |
| `optimizador.py` | lectura, validación, modelo, rotaciones y sensibilidad (se puede usar sin la app) |
| `requirements.txt` | librerías necesarias |
| `iniciar_app.bat` | lanzador para Windows |
| `.streamlit/config.toml` | colores y opciones de la app |
| `datos/` | ejemplo chico: 14 aeropuertos, 3 tipos de avión, 102 vuelos |
| `datos_grande/` | ejemplo grande: 31 aeropuertos, 4 tipos (+ ATR 72), 200 vuelos |
| `datos_uruguay/` | aerolínea uruguaya ficticia con base en Carrasco: 18 aeropuertos, 3 tipos (ATR 42, E195-E2, A320neo), 76 vuelos; también en un solo Excel (`uruguay.xlsx`) |
| `generar_dataset.py` | genera los datos de ejemplo (reproducible con semilla) |
| `verificar_modelo.py` | primera versión del modelo, sin interfaz (quedó como referencia) |

Los datos de ejemplo son **inventados** (aerolínea ficticia, código `XX`). Las distancias entre
aeropuertos son reales; horarios, demanda, tarifas y costos son sintéticos pero realistas.

## El modelo

Red tiempo-espacio: cada nodo es un aeropuerto en un momento del día, por tipo de avión.

- **Variables:** `x[f,k]` = 1 si el vuelo f lo hace un avión tipo k; `y` = aviones esperando en
  tierra entre dos eventos; `N[k]` = aviones tipo k usados (los que hay a la hora de conteo).
- **Objetivo:** minimizar costo de operación (costo por hora × duración) + costo fijo de cada
  avión usado + tarifa de cada pasajero que no entra.
- **Restricciones:** cada vuelo lo hace exactamente un avión; balance de aviones en cada nodo
  (un avión sale de donde aterrizó y después del tiempo de giro); no usar más aviones de los
  disponibles; un tipo no hace vuelos más largos que su alcance.
- **Rotaciones:** después de resolver, los flujos se convierten en el recorrido de cada avión
  (el primero en quedar listo en un aeropuerto es el primero en salir).

## Diccionario de datos

### `vuelos.csv` (una fila por vuelo): conjunto **F**

| Columna | Tipo | Unidad | Ejemplo | Uso en el modelo |
|---|---|---|---|---|
| `id_vuelo` | texto | – | XX1002 | índice f |
| `origen` | texto (IATA) | – | AEP | nodo de salida |
| `destino` | texto (IATA) | – | COR | nodo de llegada |
| `hora_salida` | HH:MM | hora local | 08:25 | ubica el nodo en el tiempo |
| `hora_llegada` | HH:MM | hora local | 09:50 | + tiempo de giro = cuándo el avión vuelve a estar disponible |
| `duracion_min` | entero | minutos | 85 | horas de bloque → costo operativo |
| `distancia_km` | entero | km | 649 | se compara con el alcance de cada tipo |
| `demanda_esperada` | entero | pasajeros | 155 | pronóstico; si supera los asientos → pasajeros sin lugar |
| `tarifa_promedio_usd` | decimal | USD/pasajero | 84.59 | costo de cada pasajero sin lugar (ingreso perdido) |

### `flota.csv` (una fila por tipo de avión): conjunto **K**

| Columna | Tipo | Unidad | Ejemplo | Uso en el modelo |
|---|---|---|---|---|
| `tipo_avion` | texto | – | B737-800 | índice k |
| `cantidad_disponible` | entero | aviones | 14 | lado derecho de la restricción de flota |
| `asientos` | entero | asientos | 170 | capacidad de cada vuelo |
| `costo_hora_bloque_usd` | entero | USD/hora | 5000 | combustible + tripulación + mantenimiento + tasas |
| `costo_fijo_diario_usd` | entero | USD/avión/día | 8500 | leasing/propiedad de cada avión que se usa |
| `tiempo_giro_min` | entero | minutos | 45 | tiempo mínimo en tierra entre aterrizaje y próximo despegue |
| `alcance_km` | entero | km | 5400 | el tipo k no puede hacer vuelos más largos que esto |

### `aeropuertos.csv` (una fila por aeropuerto): conjunto **A**

| Columna | Tipo | Ejemplo | Uso |
|---|---|---|---|
| `codigo_iata` | texto | AEP | clave que une con `vuelos.origen/destino` |
| `ciudad`, `provincia` | texto | Córdoba | para mostrar en la app |
| `latitud`, `longitud` | decimal | -31.32, -64.21 | mapa y cálculo de distancias |
| `es_base` | booleano | True | opcional: dónde pueden pernoctar o hacer mantenimiento |

## Supuestos

- El itinerario es **diario y cíclico**: el avión que termina el día en Bariloche arranca
  el día siguiente desde Bariloche. Los aviones se cuentan a las 04:00, cuando están todos en tierra.
- `demanda_esperada` es un **pronóstico** (por ejemplo, el promedio de la temporada anterior),
  no los pasajeros reales del día.
- La duración de cada vuelo es la del itinerario y se asume igual para todos los tipos
  (en la realidad el ATR, que es turbohélice, tarda un poco más).
- Todos los horarios están en hora argentina (un solo huso horario).
- Costos en USD, aproximados (no son de ninguna aerolínea real).

## Resultados de referencia

PuLP + CBC. En los dos casos la relajación lineal da la misma solución entera, así que los
precios sombra son válidos. Igual difieren un poco del ahorro real de sumar un avión entero:
el precio sombra vale para cambios chicos (y cuando el problema es degenerado puede variar
levemente entre corridas), mientras que el ahorro real se obtiene re-optimizando.
Eso es material directo para el análisis de sensibilidad del informe.

### `datos/` (102 vuelos): se resuelve en 0,04 s

| Tipo | Aviones usados / disponibles | Vuelos | Precio sombra | Ahorro real de sumar 1 avión |
|---|---|---|---|---|
| E190 | 10 / 10 | 30 | 6.086 USD/día | 5.181 USD/día |
| B737-800 | 12 / 14 | 32 | 0 (sobran 2) | 0 |
| B737-MAX8 | 8 / 8 | 40 | 3.596 USD/día | 3.417 USD/día |

Costo total USD 1.146.548 por día; pasajeros sin lugar 312 de 12.783 (2,4 %).
Con demanda +20 % sigue siendo factible (8,6 % sin lugar).

### `datos_grande/` (200 vuelos): se resuelve en 0,1 s

| Tipo | Aviones usados / disponibles | Vuelos | Precio sombra | Ahorro real de sumar 1 avión |
|---|---|---|---|---|
| ATR 72 | 10 / 10 | 44 | ≈ 11.000 USD/día | 10.887 USD/día |
| E190 | 16 / 16 | 62 | 8.570 USD/día | 8.516 USD/día |
| B737-800 | 11 / 13 | 38 | 0 (sobran 2) | 0 |
| B737-MAX8 | 12 / 12 | 56 | 3.005 USD/día | 2.950 USD/día |

Costo total USD 1.940.415 por día; pasajeros sin lugar 1.046 de 22.525 (4,6 %).
Con demanda +20 % sigue siendo factible (9,4 % sin lugar).
14 vuelos superan los 1.500 km y el ATR no los puede hacer.

> La flota está calibrada para la semilla 42. Con otra semilla los horarios cambian y,
> en el dataset grande, puede hacer falta subir `cantidad_disponible` para que haya solución.

## Cómo regenerar

```bash
pip install pandas numpy "pulp<4"
python generar_dataset.py                          # chico  → datos/
python generar_dataset.py --tamano grande          # grande → datos_grande/
python generar_dataset.py --tamano uruguay         # Uruguay → datos_uruguay/
python generar_dataset.py --tamano grande --factor-demanda 1.2 --salida datos_demanda_alta
streamlit run app.py                               # la app
```

> **Ojo con PuLP 4:** la versión 4.x cambió la API (ya no existe `pulp.LpVariable(...)`
> y no trae CBC incluido). Casi todos los tutoriales usan la 3.x, por eso conviene `pulp<4`.

## Cómo agregar vuelos o aviones a mano

- **Más aviones:** subir `cantidad_disponible` en `flota.csv`, o agregar una fila con un tipo nuevo.
- **Más vuelos:** agregar filas en `vuelos.csv`, siempre con su vuelta (en cada aeropuerto
  tienen que salir tantos vuelos como llegan, porque el día se repite).
- **Aeropuerto nuevo:** primero agregar su fila en `aeropuertos.csv`.
- Si la flota no alcanza para el itinerario, el modelo da "Infeasible".

## Si se reemplaza por datos reales

| Tabla | Fuente posible |
|---|---|
| `vuelos` | ANAC – aterrizajes y despegues (origen, destino, horarios, pasajeros por movimiento) |
| `aeropuertos` | OpenFlights (código IATA y coordenadas) |
| `flota` | Fichas técnicas de cada modelo (asientos, alcance) + costos como supuestos documentados |
