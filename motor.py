"""Motor de cálculo de Mars: lee el Excel, lo limpia, calcula estatus, KPIs, urgencia, alertas y calidad.

No depende de Streamlit (lo usan app.py y report.py, y se puede probar solo).

Grano después de preparar(): una fila = tienda × producto (Descripcion Unificada) × semana.
Si un producto tiene varios códigos (Item Nbr) en la misma tienda, se suman: así no sale un
"agotado falso" cuando el inventario quedó en un código y la venta en otro.
"""
from __future__ import annotations

import io

import numpy as np
import pandas as pd

ORDEN_KPIS = ["agotados", "excedente", "estancado", "prioritarias", "dias", "venta", "cobertura"]
ESTADOS = ["Agotado", "Por agotarse", "Excedente", "Sano", "Estancado", "Inactivo"]

COLUMNAS = {
    "Client": "cadena", "ESTADO": "estado", "CIUDAD": "ciudad",
    "Ruteo.¿SE VISITA?": "se_visita", "Ruteo.REGIONAL": "regional", "Ruteo.EJECUTIVO": "ejecutivo",
    "NOMBRE_AFS / NOMBRE DE TIENDA": "nombre_tienda", "CLIENTE_AFS": "tienda_id",
    "Categoría": "categoria", "Item Nbr": "item_nbr", "Descripcion Unificada": "producto",
    "Case Count QTY": "pzs_caja", "Inventario On Hand": "on_hand", "Inventario en Transito": "transito",
    "Inventario en Orden": "orden", "~Tuberia(OH+Orden+Transito)": "tuberia", "~Prom Venta L5W": "venta",
    "Periodo Mars": "periodo", "Semana Mars": "semana", "Año": "anio",
}
NUMERICAS = ["pzs_caja", "on_hand", "transito", "orden", "tuberia", "venta", "periodo", "semana", "anio"]
ATRIB_TIENDA = ["cadena", "estado", "ciudad", "se_visita", "regional", "ejecutivo", "nombre_tienda"]


# ------------------------------------------------------------------ lectura y limpieza
def leer_excel(contenido: bytes | str) -> pd.DataFrame:
    """Lee el Excel de Mars tal cual llega. Busca solo el renglón de encabezados (en la base viene en la fila 3)."""
    fuente = io.BytesIO(contenido) if isinstance(contenido, (bytes, bytearray)) else contenido
    try:
        crudo = pd.read_excel(fuente, header=None, engine="calamine")
    except Exception:  # noqa: BLE001  (si no está calamine, openpyxl es más lento pero funciona)
        if hasattr(fuente, "seek"):
            fuente.seek(0)
        crudo = pd.read_excel(fuente, header=None)
    fila = next((i for i in range(min(30, len(crudo)))
                 if crudo.iloc[i].astype(str).str.strip().isin(["Client", "CLIENTE_AFS"]).any()), 0)
    df = crudo.iloc[fila + 1:].copy()
    df.columns = [str(c).strip() for c in crudo.iloc[fila]]
    df = df.loc[:, [c for c in df.columns if c not in ("nan", "None", "")]]
    return df.dropna(how="all").reset_index(drop=True)


def preparar(raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """Excel crudo -> (hechos, tiendas, avisos).

    hechos: semana_key | tienda_id | producto | categoria | pzs_caja | on_hand | transito | orden | tuberia | venta | invalido
    tiendas: una fila por tienda con cadena, estado, ciudad, se_visita, regional, ejecutivo, nombre_tienda
    """
    avisos = []
    faltan = [c for c in COLUMNAS if c not in raw.columns]
    if faltan:
        avisos.append("Al Excel le faltan columnas: " + ", ".join(faltan))
    df = raw.rename(columns=COLUMNAS)[[v for k, v in COLUMNAS.items() if k in raw.columns]].copy()
    for c in NUMERICAS:
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["tienda_id", "semana"])
    for c in ["cadena", "estado", "ciudad", "se_visita", "regional", "ejecutivo", "nombre_tienda", "tienda_id", "producto", "categoria"]:
        df[c] = df[c].astype("string").str.strip()

    # Faltantes "disfrazados": se reemplazan por textos que se entienden en el tablero
    df["nombre_tienda"] = df["nombre_tienda"].fillna("SIN NOMBRE").replace({"0": "SIN NOMBRE", "": "SIN NOMBRE"})
    df["se_visita"] = np.where(df["se_visita"].str.upper().str.startswith("S"), "Sí", "No")
    df[["regional", "ejecutivo"]] = df[["regional", "ejecutivo"]].replace({"TBC": "Sin visita"}).fillna("Sin visita")
    df["estado"] = df["estado"].replace({"Tbc": "Por confirmar", "TBC": "Por confirmar"}).fillna("Por confirmar")
    df["ciudad"] = df["ciudad"].replace({"TBC": "Por confirmar"}).fillna("Por confirmar")
    df["producto"] = df["producto"].fillna("Sin descripción")
    df["anio"] = df["anio"].fillna(0)
    df["periodo"] = df["periodo"].fillna(0)
    df["semana_key"] = (df["anio"] * 1000 + df["periodo"] * 10 + df["semana"]).astype(int)

    # Negativos: no existen piezas negativas. Se marcan y cuentan como 0 al sumar.
    df["invalido"] = (df["on_hand"] < 0) | (df["venta"] < 0) | (df["tuberia"] < 0)
    for c in ["on_hand", "transito", "orden", "tuberia", "venta"]:
        df[c] = df[c].fillna(0).clip(lower=0)

    tiendas = (df.sort_values("semana_key").drop_duplicates("tienda_id", keep="last")
                 .set_index("tienda_id")[ATRIB_TIENDA])

    hechos = (df.groupby(["semana_key", "tienda_id", "producto"], as_index=False, observed=True)
                .agg(categoria=("categoria", "first"), pzs_caja=("pzs_caja", "max"), on_hand=("on_hand", "sum"),
                     transito=("transito", "sum"), orden=("orden", "sum"), tuberia=("tuberia", "sum"),
                     venta=("venta", "sum"), invalido=("invalido", "any"), codigos=("item_nbr", "nunique")))
    hechos["pzs_caja"] = hechos["pzs_caja"].fillna(1).clip(lower=1)
    hechos["cadena"] = hechos["tienda_id"].map(tiendas["cadena"])
    n_neg = int(df["invalido"].sum())
    if n_neg:
        avisos.append(f"{n_neg:,} renglones traen inventario o venta negativos: se cuentan como 0 (ver Calidad del dato).")
    return hechos, tiendas, avisos


def etiqueta_semana(key: int) -> str:
    anio, resto = divmod(int(key), 1000)
    periodo, semana = divmod(resto, 10)
    return f"P{periodo} S{semana} · {anio}" if anio else f"Semana {semana}"


# ------------------------------------------------------------------ estatus por tienda-producto
def umbral(marca: dict, cadena: pd.Series, nombre: str) -> pd.Series:
    u = marca["umbrales"]
    base = float(u[nombre])
    extra = {c: v.get(nombre, base) for c, v in (u.get("por_cadena") or {}).items()}
    return cadena.map(extra).fillna(base).astype(float)


def clasificar(h: pd.DataFrame, marca: dict) -> pd.DataFrame:
    """Agrega días, estatus, piezas de excedente/estancadas, venta en riesgo y cajas a una tabla de hechos."""
    h = h.copy()
    riesgo, dmax, dobj = (umbral(marca, h["cadena"], n) for n in ("dias_riesgo", "dias_max", "dias_objetivo"))
    vd = h["venta"] / 7
    con_venta = h["venta"] > 0
    h["dias"] = np.where(con_venta, h["on_hand"] / vd.where(con_venta), np.nan)
    h["dias_tub"] = np.where(con_venta, h["tuberia"] / vd.where(con_venta), np.nan)
    h["estatus"] = np.select(
        [con_venta & (h["on_hand"] <= 0), con_venta & (h["dias_tub"] < riesgo), con_venta & (h["dias"] > dmax),
         con_venta, h["on_hand"] > 0],
        ["Agotado", "Por agotarse", "Excedente", "Sano", "Estancado"], "Inactivo")
    h["excedente_pzs"] = np.where(con_venta, (h["on_hand"] - vd * dmax).clip(lower=0), 0.0)
    h["estancado_pzs"] = np.where(~con_venta, h["on_hand"], 0.0)
    h["venta_riesgo"] = np.where(h["estatus"].isin(["Agotado", "Por agotarse"]), h["venta"], 0.0)
    h["cajas_pedir"] = np.where(con_venta, np.ceil((vd * dobj - h["tuberia"]).clip(lower=0) / h["pzs_caja"]), 0)
    h["cajas_sobran"] = np.floor(h["excedente_pzs"] / h["pzs_caja"])
    return h


# ------------------------------------------------------------------ calidad del dato
def calidad(hechos: pd.DataFrame, marca: dict) -> dict:
    """Revisa cada semana: tiendas reportadas por cadena, cambio de venta en pares comunes y negativos."""
    sem = sorted(hechos["semana_key"].unique())
    tiendas = hechos.pivot_table(index="cadena", columns="semana_key", values="tienda_id", aggfunc="nunique").fillna(0)
    cobertura = tiendas.div(tiendas.max(axis=1), axis=0) * 100
    minimo = marca.get("cobertura_minima", 95)

    pares = hechos[["semana_key", "tienda_id", "producto", "venta"]]
    orden = {k: i for i, k in enumerate(sem)}
    previa = pares.assign(semana_key=pares["semana_key"].map({k: sem[i + 1] for i, k in enumerate(sem[:-1])}))
    comunes = pares.merge(previa.dropna(subset=["semana_key"]).astype({"semana_key": int}),
                          on=["semana_key", "tienda_id", "producto"], suffixes=("", "_prev"))
    cambio = comunes.groupby("semana_key")[["venta", "venta_prev"]].sum()
    cambio["cambio_%"] = (cambio["venta"] / cambio["venta_prev"] - 1) * 100

    lim = marca.get("cambio_venta_max", 25)
    filas = []
    for k in sem:
        incompletas = [c for c in cobertura.index if cobertura.loc[c, k] < minimo]
        cv = cambio["cambio_%"].get(k, np.nan)
        tot = tiendas[k].sum()
        filas.append({"semana_key": k, "Semana": etiqueta_semana(k), "Tiendas": int(tot),
                      "Tiendas reportadas %": tot / tiendas.max(axis=1).sum() * 100,
                      "Cadenas incompletas": ", ".join(incompletas) or "—",
                      "Venta vs. semana anterior %": cv,
                      "Venta sospechosa": bool(pd.notna(cv) and abs(cv) > lim),
                      "Renglones con negativos": int(hechos.loc[hechos["semana_key"] == k, "invalido"].sum())})
    resumen = pd.DataFrame(filas).set_index("semana_key")
    confiables = [k for k in sem if not resumen.loc[k, "Venta sospechosa"]]
    recomendada = confiables[-1] if confiables else sem[-1]
    _ = orden
    return {"resumen": resumen, "tiendas": tiendas, "cobertura": cobertura, "recomendada": recomendada}


# ------------------------------------------------------------------ urgencia por tienda
def tabla_tiendas(h: pd.DataFrame, tiendas: pd.DataFrame, marca: dict) -> pd.DataFrame:
    """Una fila por tienda con sus números y la calificación de urgencia (0 a 100)."""
    con_venta = h["venta"] > 0
    g = h.assign(agot=con_venta & (h["estatus"] == "Agotado"), cv=con_venta).groupby("tienda_id").agg(
        venta=("venta", "sum"), on_hand=("on_hand", "sum"), venta_riesgo=("venta_riesgo", "sum"),
        estancado=("estancado_pzs", "sum"), excedente=("excedente_pzs", "sum"),
        agotados=("agot", "sum"), con_venta=("cv", "sum"))
    g = g.join(tiendas, how="left")
    pesos = marca["pesos_urgencia"]
    partes = {"venta_en_riesgo": "venta_riesgo", "estancado": "estancado", "excedente": "excedente", "venta_tienda": "venta"}
    total = 0
    for nombre, col in partes.items():
        pr = g[col].rank(pct=True).where(g[col] > 0, 0)   # 0 si no tiene el problema
        g[f"_p_{col}"] = pr
        total = total + pr * pesos[nombre]
    g["urgencia"] = (total / sum(pesos.values()) * 100).round(1)
    corte = g["urgencia"].quantile(1 - marca.get("pct_prioritarias", 20) / 100)
    g["prioritaria"] = g["urgencia"] >= corte
    g["pct_agotados"] = np.where(g["con_venta"] > 0, g["agotados"] / g["con_venta"] * 100, np.nan)
    return g.sort_values("urgencia", ascending=False)


# ------------------------------------------------------------------ KPIs
def kpis(h: pd.DataFrame, T: pd.DataFrame, cobertura_pct: float) -> dict:
    con_venta = h["venta"] > 0
    oh = h["on_hand"].sum()
    pri = T[T["prioritaria"]]
    d = h.loc[con_venta & (h["on_hand"] > 0), "dias"]
    return {
        "agotados": (h.loc[con_venta, "estatus"] == "Agotado").mean() * 100 if con_venta.any() else np.nan,
        "excedente": h["excedente_pzs"].sum() / oh * 100 if oh else np.nan,
        "estancado": h["estancado_pzs"].sum() / oh * 100 if oh else np.nan,
        "prioritarias": (pri["se_visita"] == "Sí").mean() * 100 if len(pri) else np.nan,
        "dias": d.median() if len(d) else np.nan,
        "venta": h["venta"].sum(),
        "cobertura": cobertura_pct,
        # de apoyo
        "piezas_estancadas": h["estancado_pzs"].sum(), "piezas_excedente": h["excedente_pzs"].sum(),
        "venta_riesgo": h["venta_riesgo"].sum(), "n_prioritarias": len(pri),
        "pri_no_visitadas": int((pri["se_visita"] == "No").sum()), "tiendas": len(T),
    }


def estado(kpi: str, valor, cfg: dict, previo=None) -> tuple[str, str]:
    """('verde'|'amarillo'|'rojo'|'gris', texto)."""
    if valor is None or pd.isna(valor):
        return "gris", "Sin datos"
    if kpi == "venta":
        if previo is None or pd.isna(previo) or not previo:
            return "gris", "Sin comparación"
        cambio = (valor / previo - 1) * 100
        return ("verde", "Estable o creciendo") if cambio >= -2 else (("amarillo", "Baja leve") if cambio >= -8 else ("rojo", "Baja fuerte"))
    meta, ini, mas = cfg["meta"], cfg["inicio"], cfg["mas_es_mejor"]
    if (valor >= meta) if mas else (valor <= meta):
        return "verde", "En meta"
    avance = (valor - ini) / (meta - ini) if meta != ini else 0
    return ("amarillo", "En camino") if avance >= 0.5 else ("rojo", "Lejos de la meta")


def fmt(kpi: str, v, cfg: dict | None = None) -> str:
    if v is None or pd.isna(v):
        return "—"
    if kpi == "venta":
        return f"{v / 1000:,.1f} mil pzs" if v >= 10000 else f"{v:,.0f} pzs"
    if kpi == "dias":
        return f"{v:,.0f} días"
    return f"{v:.1f}%"


def pareto(T: pd.DataFrame, col: str = "estancado") -> tuple[pd.DataFrame, int]:
    """(curva, cuántas tiendas juntan el 80 % del problema)."""
    s = T[col][T[col] > 0].sort_values(ascending=False)
    if s.empty:
        return pd.DataFrame(columns=["n", "acum"]), 0
    acum = s.cumsum() / s.sum() * 100
    curva = pd.DataFrame({"n": np.arange(1, len(s) + 1), "acum": acum.values, "tienda": s.index})
    return curva, int((acum < 80).sum() + 1)


# ------------------------------------------------------------------ alertas
def nivel(prio: float) -> str:
    return "🔴" if prio >= 70 else ("🟠" if prio >= 40 else "🟢")


def alertas(h: pd.DataFrame, T: pd.DataFrame, k: dict, marca: dict, cal: dict, semana_key: int) -> list[tuple]:
    """Alertas generales por reglas: [(prioridad, título, texto)], de mayor a menor."""
    cfg = marca["kpis"]
    out = []
    pri_nv = T[T["prioritaria"] & (T["se_visita"] == "No")]
    if len(pri_nv):
        top = ", ".join(f"{r.nombre_tienda} ({r.cadena})" for r in pri_nv.head(3).itertuples())
        out.append((90, f"{len(pri_nv)} tiendas prioritarias no se visitan",
                    f"Son {len(pri_nv)} de las {k['n_prioritarias']} tiendas con más urgencia y nadie de Mars va a ellas. "
                    f"Las tres más urgentes: {top}. Conviene sumarlas a la ruta antes que tiendas visitadas con urgencia baja."))

    cv = h[h["venta"] > 0].groupby("cadena")["estatus"].apply(lambda s: (s == "Agotado").mean() * 100)
    if not cv.empty:
        peor = cv.idxmax()
        if cv[peor] > cfg["agotados"]["meta"]:
            out.append((75 + min(15, cv[peor] - cfg["agotados"]["meta"]), f"{peor} tiene la mayor tasa de agotados",
                        f"{cv[peor]:.1f}% de sus productos con venta está en 0 en tienda (meta {cfg['agotados']['meta']}%). "
                        f"Se están perdiendo unas {k['venta_riesgo']:,.0f} piezas por semana entre todas las cadenas. "
                        "Revisar la lista de Alertas filtrando por esta cadena."))

    curva, n80 = pareto(T, "estancado")
    if n80:
        out.append((65, "El inventario estancado se concentra en pocas tiendas",
                    f"{k['piezas_estancadas']:,.0f} piezas están en tienda sin venderse. {n80:,} tiendas "
                    f"({n80 / len(curva):.0%} de las que tienen este problema) juntan el 80 %. "
                    "Visitarlas para sacar producto de bodega, revisar inventario fantasma o pedir devolución."))

    ex = h.groupby("producto")["excedente_pzs"].sum().sort_values(ascending=False)
    if not ex.empty and ex.iloc[0] > 0:
        out.append((50, f"Sobra {ex.index[0]}",
                    f"Es el producto con más excedente: {ex.iloc[0]:,.0f} piezas por encima de 90 días de venta. "
                    "No resurtirlo en esas tiendas y moverlo a donde está agotado."))

    r = cal["resumen"].loc[semana_key]
    if r["Venta sospechosa"]:
        out.append((80, "La venta de esta semana no cuadra",
                    f"Cambia {r['Venta vs. semana anterior %']:+.0f}% contra la semana anterior con las mismas tiendas y productos. "
                    "Un promedio de 5 semanas casi no puede moverse tanto: probablemente cambió el cálculo o la carga. "
                    f"Usar la semana {etiqueta_semana(cal['recomendada'])} hasta confirmar con Mars."))
    if r["Cadenas incompletas"] != "—":
        out.append((45, "Faltan tiendas en la base de esta semana",
                    f"Reportaron menos del {marca.get('cobertura_minima', 95)}% de sus tiendas: {r['Cadenas incompletas']}. "
                    "Los totales de esas cadenas son más bajos de lo real; los porcentajes sí se pueden comparar."))

    ok = [c for c, v in cv.items() if v <= cfg["agotados"]["meta"]]
    if ok:
        out.append((20, "Cadenas con agotados en meta", f"{', '.join(ok)}: menos del {cfg['agotados']['meta']}% de productos agotados."))
    return sorted(out, key=lambda x: -x[0])


ACCION = {"Agotado": "Resurtir ya", "Por agotarse": "Resurtir esta semana",
          "Estancado": "Revisar en tienda (bodega o inventario fantasma)", "Excedente": "No resurtir / mover a otra tienda"}


def lista_operativa(h: pd.DataFrame, tiendas: pd.DataFrame) -> pd.DataFrame:
    """Lista de acciones por tienda-producto para Operaciones, ordenada por prioridad."""
    a = h[h["estatus"].isin(ACCION)].copy()
    base = {"Agotado": 85, "Por agotarse": 70, "Estancado": 55, "Excedente": 40}
    tam = np.where(a["estatus"].isin(["Agotado", "Por agotarse"]), a["venta"],
                   np.where(a["estatus"] == "Estancado", a["on_hand"], a["excedente_pzs"]))
    a["prioridad"] = a["estatus"].map(base) + 14 * pd.Series(tam, index=a.index).rank(pct=True)
    a = a.join(tiendas[["nombre_tienda", "ejecutivo", "estado", "se_visita"]], on="tienda_id")
    a["accion"] = a["estatus"].map(ACCION)
    a["cajas"] = np.where(a["estatus"].isin(["Agotado", "Por agotarse"]), a["cajas_pedir"],
                          np.where(a["estatus"] == "Excedente", a["cajas_sobran"], np.nan))
    return a.sort_values("prioridad", ascending=False)


# ------------------------------------------------------------------ resumen en palabras
def resumen(k: dict, kprev: dict, marca: dict, h: pd.DataFrame, T: pd.DataFrame, semana: str) -> str:
    cfg = marca["kpis"]
    partes = [f"En la semana **{semana}**, **{fmt('agotados', k['agotados'])}** de los productos que se venden está agotado "
              f"(meta {cfg['agotados']['meta']}%) y **{fmt('excedente', k['excedente'])}** del inventario es excedente."]
    if kprev and pd.notna(kprev.get("agotados")):
        dif = k["agotados"] - kprev["agotados"]
        if abs(dif) >= 0.1:
            partes.append(f"Los agotados {'bajaron' if dif < 0 else 'subieron'} {abs(dif):.1f} puntos contra la semana anterior.")
    partes.append(f"**{k['piezas_estancadas']:,.0f} piezas** están en tienda sin venderse.")
    if k["n_prioritarias"]:
        partes.append(f"De las **{k['n_prioritarias']:,} tiendas prioritarias**, se visita el **{fmt('prioritarias', k['prioritarias'])}**.")
    est = T[T["estado"] != "Por confirmar"].groupby("estado")["estancado"].sum()
    if not est.empty and est.max() > 0:
        partes.append(f"El estado con más inventario estancado es **{est.idxmax()}**.")
    return " ".join(partes)
