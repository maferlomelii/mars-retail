"""Agente de preguntas rápidas sobre la base de Mars (Claude Haiku + herramientas).

Cómo funciona:
  1. La pregunta se manda a Claude junto con la lista de "herramientas" de abajo.
  2. Claude elige qué herramienta usar (ej. resumen_tienda) y con qué datos.
  3. La herramienta se ejecuta AQUÍ con pandas sobre los datos reales y regresa solo el resultado.
  4. Claude redacta la respuesta en español.
Claude nunca ve el Excel completo y no calcula números: todos salen de estas funciones.
"""
from __future__ import annotations

import difflib
import json
import re
import unicodedata

import numpy as np
import pandas as pd

import motor
import simulador

MODELO = "claude-haiku-5-5"
MAX_VUELTAS = 6


VACIAS = {"la", "el", "los", "las", "de", "del", "en", "y", "que", "tienda", "sucursal", "store", "a", "al", "por", "esta", "semana"}
# Formatos abreviados en la base -> cómo los dice la gente
FORMATOS = {"ba": "bodega aurrera", "bae": "bodega aurrera express", "mb": "mi bodega aurrera", "mib": "mi bodega aurrera",
            "sc": "walmart supercenter", "we": "walmart express", "bd": "bodega", "mt": "mi tienda del ahorro", "sm": "sams club"}
CADENA_ALIAS = {"SAMS": "sams sam club", "WALMART": "walmart", "HEB": "heb", "COSTCO": "costco", "City Club": "city club",
                "Soriana": "soriana", "Chedraui": "chedraui"}
GENERICAS = set(" ".join(list(FORMATOS.values()) + list(CADENA_ALIAS.values())).split()) | set(FORMATOS) | {"super", "selecto", "mex"}


def _norm(s) -> str:
    s = unicodedata.normalize("NFKD", str(s).replace("'", "").replace("’", "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9 ]", " ", s).strip()


def _r(v, d=1):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        return round(float(v), d)
    return v


class Datos:
    """Envuelve la base para que las herramientas la consulten. `calc(k, cadenas)` -> (h, T, K)."""

    def __init__(self, H, TI, CAL, marca, calc):
        self.H, self.TI, self.CAL, self.M, self._calc = H, TI, CAL, marca, calc
        self.semanas = sorted(H["semana_key"].unique())
        self.cadenas = sorted(H["cadena"].dropna().unique())
        def texto(r):
            nombre = _norm(r["nombre_tienda"])
            pre = nombre.split()[0] if nombre else ""
            return " ".join([nombre, _norm(r.name), _norm(r["ciudad"]), FORMATOS.get(pre, ""), CADENA_ALIAS.get(r["cadena"], "")])
        self.TI_norm = TI.assign(_n=TI.apply(texto, axis=1))
        self.TI_tokens = self.TI_norm["_n"].str.split().map(set)
        self.frec = pd.Series([w for toks in self.TI_tokens for w in toks]).value_counts()
        self.vocab = self.frec.index.tolist()
        self.nucleo = TI["nombre_tienda"].map(lambda n: frozenset(w for w in _norm(n).split() if w not in GENERICAS))

    # -------- ayudantes
    def semana(self, s=None) -> int:
        """Acepta None/'actual', la clave interna (2026113), 'S3', 'semana 3', 'P11 S3' o '3'."""
        if isinstance(s, (int, np.integer)) and int(s) in self.semanas:
            return int(s)
        txt = str(s or "").strip().lower()
        if txt in ("", "actual", "esta", "esta semana", "ultima", "recomendada", "none"):
            return int(self.CAL["recomendada"])
        ms = re.search(r"s(?:emana)?\s*(\d+)", txt) or (re.fullmatch(r"\d+", txt) and re.match(r"(\d+)", txt))
        mp = re.search(r"p(?:eriodo)?\s*(\d+)", txt)
        if ms:
            sem, per = int(ms.group(1)), int(mp.group(1)) if mp else None
            for k in reversed(self.semanas):
                periodo, semana = divmod(int(k) % 1000, 10)
                if semana == sem and (per is None or per == periodo):
                    return int(k)
        return int(self.CAL["recomendada"])

    def cads(self, cadena=None):
        if not cadena:
            return tuple(self.cadenas)
        c = [x for x in self.cadenas if _norm(x) == _norm(cadena) or _norm(cadena) in _norm(x)]
        return tuple(c) or tuple(self.cadenas)

    def calc(self, semana=None, cadena=None):
        k = self.semana(semana)
        h, T, K = self._calc(k, self.cads(cadena))
        return k, h, T, K

    def tienda(self, texto):
        """(tienda_id, opciones). Entiende mayúsculas, acentos, 'Bodega Aurrera' = BA, 'Sams' = SM,
        ignora palabras como 'la' o 'de' y corrige faltas de ortografía. Si hay varias, regresa opciones."""
        if texto in self.TI.index:
            return texto, self.TI.iloc[:0]
        t = _norm(texto)
        exact = self.TI_norm[self.TI_norm["nombre_tienda"].map(_norm) == t]
        if len(exact) == 1:
            return exact.index[0], self.TI.iloc[:0]
        palabras = [w for w in t.split() if w not in VACIAS] or t.split()

        def coinciden(ws):
            ok = self.TI_tokens.map(lambda toks: all(w in toks or (len(w) >= 4 and any(x.startswith(w) for x in toks)) for w in ws))
            return self.TI_norm[ok]

        m = coinciden(palabras)
        if m.empty:   # faltas de ortografía: cambia cada palabra por la más parecida que exista en la base
            def corregir(w):
                if w in self.frec.index:
                    return w
                cand = difflib.get_close_matches(w, self.vocab, n=5, cutoff=0.75)
                if not cand:
                    return w
                mejor = max(difflib.SequenceMatcher(None, w, c).ratio() for c in cand)
                empatados = [c for c in cand if difflib.SequenceMatcher(None, w, c).ratio() == mejor]
                return max(empatados, key=lambda c: self.frec[c])   # entre empates, la palabra más común en la base
            corr = [corregir(w) for w in palabras]
            m = coinciden(corr)
            palabras = corr
        if m.empty:   # último intento: nombres más parecidos
            nombres = self.TI_norm["nombre_tienda"].map(_norm)
            cerca = difflib.get_close_matches(t, nombres.tolist(), n=8, cutoff=0.5)
            return None, self.TI_norm[nombres.isin(cerca)]
        if len(m) == 1:
            return m.index[0], self.TI.iloc[:0]
        nucleo = frozenset(w for w in palabras if w not in GENERICAS)
        iguales = [i for i in m.index if nucleo and self.nucleo[i] == nucleo]
        if len(iguales) == 1:   # ej. "sams puebla" -> SM PUEBLA (y no SM PUEBLA VIA CAPU)
            return iguales[0], self.TI.iloc[:0]
        orden = m.assign(_extra=[len(self.nucleo[i] - nucleo) for i in m.index]).sort_values("_extra")
        return None, orden

    def aviso_semana(self, k):
        r = self.CAL["resumen"].loc[k]
        av = []
        if r["Venta sospechosa"]:
            av.append(f"La venta de esta semana no cuadra con la anterior ({r['Venta vs. semana anterior %']:+.0f}%); "
                      f"se recomienda usar {motor.etiqueta_semana(self.CAL['recomendada'])}.")
        if r["Cadenas incompletas"] != "—":
            av.append(f"Faltan tiendas de: {r['Cadenas incompletas']}.")
        return av


# ------------------------------------------------------------------ herramientas
def h_semanas(D: Datos):
    r = D.CAL["resumen"]
    return {"recomendada": motor.etiqueta_semana(D.CAL["recomendada"]),
            "semanas": [{"semana": row["Semana"], "tiendas": int(row["Tiendas"]),
                         "cadenas_incompletas": row["Cadenas incompletas"],
                         "venta_vs_anterior_%": _r(row["Venta vs. semana anterior %"]),
                         "venta_sospechosa": bool(row["Venta sospechosa"])} for _, row in r.iterrows()]}


def h_buscar_tienda(D: Datos, texto: str):
    tid, opciones = D.tienda(texto)
    if tid:
        opciones = D.TI.loc[[tid]]
    return {"encontradas": [{"tienda_id": i, "nombre": r.nombre_tienda, "cadena": r.cadena, "estado": r.estado,
                             "ciudad": r.ciudad, "se_visita": r.se_visita} for i, r in opciones.head(8).iterrows()]}


def h_resumen_tienda(D: Datos, tienda: str, semana=None):
    tid, opciones = D.tienda(tienda)
    if not tid:
        return {"error": "No encontré una sola tienda con ese nombre.",
                "opciones": [f"{r.nombre_tienda} ({r.cadena}, {r.ciudad}) id={i}" for i, r in opciones.iterrows()]}
    k, h, T, K = D.calc(semana)
    info = D.TI.loc[tid]
    g = h[h["tienda_id"] == tid]
    if g.empty:
        return {"tienda": info["nombre_tienda"], "semana": motor.etiqueta_semana(k),
                "aviso": "La tienda no aparece en esta semana de la base (no reportó).", "avisos_semana": D.aviso_semana(k)}
    i = D.semanas.index(k)
    venta_prev = None
    if i > 0:
        _, hp, _, _ = D.calc(D.semanas[i - 1])
        gp = hp[hp["tienda_id"] == tid]
        venta_prev = gp["venta"].sum() if not gp.empty else None
    fila = T.loc[tid] if tid in T.index else None
    por = lambda e: g[g["estatus"] == e].sort_values("venta", ascending=False)["producto"].tolist()
    return {"tienda": info["nombre_tienda"], "tienda_id": tid, "cadena": info["cadena"], "estado": info["estado"],
            "ciudad": info["ciudad"], "se_visita": info["se_visita"], "ejecutivo": info["ejecutivo"],
            "semana": motor.etiqueta_semana(k),
            "venta_pzs_semana": _r(g["venta"].sum()), "venta_semana_anterior": _r(venta_prev),
            "inventario_en_tienda_pzs": _r(g["on_hand"].sum(), 0), "en_camino_pzs": _r(g["transito"].sum() + g["orden"].sum(), 0),
            "productos": int(len(g)), "agotados": por("Agotado"), "por_agotarse": por("Por agotarse"),
            "estancados_venta_0": g[g["estatus"] == "Estancado"].sort_values("on_hand", ascending=False)["producto"].head(8).tolist(),
            "piezas_estancadas": _r(g["estancado_pzs"].sum(), 0), "piezas_excedente": _r(g["excedente_pzs"].sum(), 0),
            "urgencia_0_100": _r(fila["urgencia"]) if fila is not None else None,
            "prioritaria": bool(fila["prioritaria"]) if fila is not None else None,
            "lugar_en_urgencia": int(list(T.index).index(tid) + 1) if fila is not None else None, "de_tiendas": len(T),
            "top_productos_venta": [{"producto": r.producto, "venta": _r(r.venta), "dias_inv": _r(r.dias, 0)}
                                    for r in g.sort_values("venta", ascending=False).head(5).itertuples()],
            "avisos_semana": D.aviso_semana(k)}


def h_kpis(D: Datos, semana=None, cadena=None, estado=None):
    k, h, T, K = D.calc(semana, cadena)
    if estado:
        ids = T.index[T["estado"].map(_norm).str.contains(_norm(estado))]
        h, T2 = h[h["tienda_id"].isin(ids)], T.loc[ids]
        K = motor.kpis(h, T2, K["cobertura"])
    cfg = D.M["kpis"]
    return {"semana": motor.etiqueta_semana(k), "filtro": {"cadena": cadena or "todas", "estado": estado or "todos"},
            "kpis": {cfg[x]["nombre"]: {"valor": motor.fmt(x, K[x]), "meta": cfg[x]["meta"] if x != "venta" else "sin caer",
                                        "estado": motor.estado(x, K[x], cfg[x])[1]} for x in motor.ORDEN_KPIS},
            "piezas_estancadas": _r(K["piezas_estancadas"], 0), "piezas_excedente": _r(K["piezas_excedente"], 0),
            "venta_en_riesgo_pzs_semana": _r(K["venta_riesgo"], 0), "tiendas": int(K["tiendas"]),
            "tiendas_prioritarias": int(K["n_prioritarias"]), "prioritarias_sin_visita": int(K["pri_no_visitadas"]),
            "avisos_semana": D.aviso_semana(k)}


METRICAS = {"venta": "Venta (pzs/semana)", "inventario": "Inventario en tienda (pzs)", "agotados": "Productos agotados (#)",
            "pct_agotados": "% de productos con venta que están agotados", "estancado": "Piezas estancadas (venta 0)",
            "excedente": "Piezas de excedente (>90 días)", "venta_en_riesgo": "Venta de productos agotados o por agotarse",
            "urgencia": "Calificación de urgencia (solo nivel tienda)", "dias_mediana": "Mediana de días de inventario"}
NIVELES = {"tienda": "tienda_id", "cadena": "cadena", "estado": "estado", "producto": "producto",
           "categoria": "categoria", "regional": "regional", "ejecutivo": "ejecutivo", "ciudad": "ciudad"}


def _agrupar(h, T, nivel):
    col = NIVELES[nivel]
    g = h.join(T[["estado", "regional", "ejecutivo", "ciudad"]], on="tienda_id")
    cv = g["venta"] > 0
    a = g.assign(_ag=cv & (g["estatus"] == "Agotado"), _cv=cv,
                 _d=g["dias"].where(cv & (g["on_hand"] > 0))).groupby(col).agg(
        venta=("venta", "sum"), inventario=("on_hand", "sum"), agotados=("_ag", "sum"), con_venta=("_cv", "sum"),
        estancado=("estancado_pzs", "sum"), excedente=("excedente_pzs", "sum"), venta_en_riesgo=("venta_riesgo", "sum"),
        dias_mediana=("_d", "median"), tiendas=("tienda_id", "nunique"))
    a["pct_agotados"] = np.where(a["con_venta"] > 0, a["agotados"] / a["con_venta"] * 100, np.nan)
    if nivel == "tienda":
        a = a.join(T[["urgencia", "nombre_tienda", "cadena", "se_visita"]])
    return a


def h_ranking(D: Datos, metrica: str, nivel: str = "tienda", n: int = 10, orden: str = "mayor",
              semana=None, cadena=None, estado=None, solo_no_visitadas: bool = False):
    if metrica not in METRICAS or nivel not in NIVELES:
        return {"error": f"metrica debe ser una de {list(METRICAS)} y nivel uno de {list(NIVELES)}"}
    if metrica == "urgencia" and nivel != "tienda":
        return {"error": "La urgencia solo existe a nivel tienda."}
    k, h, T, K = D.calc(semana, cadena)
    if estado:
        ids = T.index[T["estado"].map(_norm).str.contains(_norm(estado))]
        h, T = h[h["tienda_id"].isin(ids)], T.loc[ids]
    if solo_no_visitadas:
        ids = T.index[T["se_visita"] == "No"]
        h, T = h[h["tienda_id"].isin(ids)], T.loc[ids]
    a = _agrupar(h, T, nivel).dropna(subset=[metrica])
    a = a.sort_values(metrica, ascending=(orden == "menor")).head(max(1, min(int(n), 25)))
    filas = []
    for idx, r in a.iterrows():
        f = {"nombre": r["nombre_tienda"] if nivel == "tienda" else idx, metrica: _r(r[metrica])}
        if nivel == "tienda":
            f.update({"cadena": r["cadena"], "se_visita": r["se_visita"]})
        else:
            f["tiendas"] = int(r["tiendas"])
        filas.append(f)
    return {"semana": motor.etiqueta_semana(k), "metrica": METRICAS[metrica], "nivel": nivel, "orden": orden,
            "resultado": filas, "avisos_semana": D.aviso_semana(k)}


def _producto_en_otras_semanas(D: Datos, p: str, k: int):
    todos = D.H.groupby("producto")["semana_key"].unique()
    hay = [x for x in todos.index if all(w in _norm(x).split() or w in _norm(x) for w in p.split())]
    if not hay:
        return None
    return {"aviso": f"No aparece en la semana {motor.etiqueta_semana(k)}, pero sí en otras.",
            "productos": {x: [motor.etiqueta_semana(s) for s in sorted(todos[x])] for x in hay[:8]},
            "sugerencia": "Vuelve a consultar con una de esas semanas."}


def h_producto(D: Datos, producto: str, semana=None, cadena=None):
    k, h, T, K = D.calc(semana, cadena)
    prods = h["producto"].unique()
    p = " ".join(w for w in _norm(producto).split() if w not in VACIAS) or _norm(producto)
    cand = [x for x in prods if p in _norm(x)] or [x for x in prods if all(w in _norm(x).split() or w in _norm(x) for w in p.split())]
    if not cand:   # faltas de ortografía
        nombres = {_norm(x): x for x in prods}
        vocab = sorted({w for n in nombres for w in n.split()})
        corr = " ".join((difflib.get_close_matches(w, vocab, n=1, cutoff=0.7) or [w])[0] for w in p.split())
        cand = [x for n, x in nombres.items() if all(w in n.split() for w in corr.split())]
        if not cand:
            return _producto_en_otras_semanas(D, p, k) or {
                "error": "Ese producto no está en la base.",
                "parecidos": [nombres[x] for x in difflib.get_close_matches(p, list(nombres), n=8, cutoff=0.4)]}
        p = corr
    if len(cand) > 1 and not any(_norm(c) == p for c in cand):
        return {"varios": sorted(cand)[:15], "nota": "Pregunta cuál, o usa el nombre exacto."}
    nombre = next((c for c in cand if _norm(c) == p), cand[0])
    g = h[h["producto"] == nombre].join(T[["nombre_tienda"]], on="tienda_id")
    ag = g[g["estatus"] == "Agotado"].sort_values("venta", ascending=False)
    return {"producto": nombre, "semana": motor.etiqueta_semana(k), "tiendas_que_lo_tienen": int(g["tienda_id"].nunique()),
            "venta_pzs_semana": _r(g["venta"].sum()), "inventario_pzs": _r(g["on_hand"].sum(), 0),
            "tiendas_agotado": int(len(ag)), "tiendas_estancado": int((g["estatus"] == "Estancado").sum()),
            "piezas_excedente": _r(g["excedente_pzs"].sum(), 0),
            "venta_por_cadena": {c: _r(v) for c, v in g.groupby("cadena")["venta"].sum().sort_values(ascending=False).items()},
            "top_tiendas_agotado": [f"{r.nombre_tienda} ({r.cadena}) vende {r.venta:.1f}/sem" for r in ag.head(5).itertuples()],
            "avisos_semana": D.aviso_semana(k)}


def h_semana_a_semana(D: Datos, metrica: str = "venta", tienda=None, cadena=None, producto=None):
    if metrica not in METRICAS or metrica == "urgencia":
        return {"error": f"metrica debe ser una de {[m for m in METRICAS if m != 'urgencia']}"}
    tid = None
    if tienda:
        tid, op = D.tienda(tienda)
        if not tid:
            return {"error": "No encontré una sola tienda.", "opciones": [f"{r.nombre_tienda} ({r.cadena})" for _, r in op.iterrows()]}
    filas = []
    for k in D.semanas:
        _, h, T, _ = D.calc(k, cadena)
        if tid:
            h = h[h["tienda_id"] == tid]
        if producto:
            h = h[h["producto"].map(_norm).str.contains(_norm(producto))]
        if h.empty:
            filas.append({"semana": motor.etiqueta_semana(k), "valor": None})
            continue
        h = h.assign(_g="total")
        cv = h["venta"] > 0
        val = {"venta": h["venta"].sum(), "inventario": h["on_hand"].sum(), "agotados": int((cv & (h["estatus"] == "Agotado")).sum()),
               "pct_agotados": (h.loc[cv, "estatus"] == "Agotado").mean() * 100 if cv.any() else np.nan,
               "estancado": h["estancado_pzs"].sum(), "excedente": h["excedente_pzs"].sum(),
               "venta_en_riesgo": h["venta_riesgo"].sum(),
               "dias_mediana": h.loc[cv & (h["on_hand"] > 0), "dias"].median()}[metrica]
        filas.append({"semana": motor.etiqueta_semana(k), "valor": _r(val),
                      "venta_sospechosa": bool(D.CAL["resumen"].loc[k, "Venta sospechosa"])})
    return {"metrica": METRICAS[metrica], "filtro": {"tienda": tienda, "cadena": cadena, "producto": producto}, "serie": filas}


def h_acciones(D: Datos, tienda=None, ejecutivo=None, tipo=None, n: int = 10, semana=None, cadena=None):
    k, h, T, K = D.calc(semana, cadena)
    L = motor.lista_operativa(h, D.TI)
    if tienda:
        tid, op = D.tienda(tienda)
        if not tid:
            return {"error": "No encontré una sola tienda.", "opciones": [f"{r.nombre_tienda} ({r.cadena})" for _, r in op.iterrows()]}
        L = L[L["tienda_id"] == tid]
    if ejecutivo:
        L = L[L["ejecutivo"].map(_norm) == _norm(ejecutivo)] if any(ch.isdigit() for ch in str(ejecutivo)) \
            else L[L["ejecutivo"].map(_norm).str.contains(_norm(ejecutivo))]
    if tipo:
        L = L[L["estatus"].map(_norm) == _norm(tipo)]
    return {"semana": motor.etiqueta_semana(k), "total": int(len(L)), "conteo": L["estatus"].value_counts().to_dict(),
            "acciones": [{"tienda": r.nombre_tienda, "cadena": r.cadena, "producto": r.producto, "estatus": r.estatus,
                          "que_hacer": r.accion, "inventario": _r(r.on_hand, 0), "venta_sem": _r(r.venta),
                          "cajas": _r(r.cajas, 0)} for r in L.head(max(1, min(int(n), 25))).itertuples()]}


def h_visitadas(D: Datos, semana=None, cadena=None):
    k, h, T, K = D.calc(semana, cadena)
    out = {}
    for v in ["Sí", "No"]:
        ids = T.index[T["se_visita"] == v]
        g = h[h["tienda_id"].isin(ids)]
        cv = g["venta"] > 0
        oh = g["on_hand"].sum()
        out[f"se_visita={v}"] = {"tiendas": int(len(ids)), "venta_pzs": _r(g["venta"].sum()),
                                 "venta_por_tienda": _r(g["venta"].sum() / len(ids)) if len(ids) else None,
                                 "pct_agotados": _r((g.loc[cv, "estatus"] == "Agotado").mean() * 100) if cv.any() else None,
                                 "pct_estancado": _r(g["estancado_pzs"].sum() / oh * 100) if oh else None}
    return {"semana": motor.etiqueta_semana(k), "comparacion": out,
            "nota": "Mars visita sobre todo tiendas grandes: la diferencia no prueba por sí sola que la visita cause la venta."}


def h_simular(D: Datos, escenario=None, demanda_pct=0, demanda_cadenas=None, visitas_extra=0, reasignar=False,
              efecto_visita=50, resurtir_pct=0, rebalanceo_pct=0, excedente_pct=0, estancado_pct=0, dias_max=None, semana=None):
    k, h, T, K = D.calc(semana)
    guardados = D.M.get("escenarios", {})
    if escenario:
        nombre = next((n for n in guardados if _norm(escenario) in _norm(n) or _norm(n).strip() == _norm(escenario)), None)
        if not nombre:
            return {"error": "No existe ese escenario guardado.", "escenarios_guardados": list(guardados)}
        params = dict(guardados[nombre]["params"])
    else:
        nombre = "personalizado"
        params = {"demanda_pct": demanda_pct, "demanda_cadenas": [c for x in (demanda_cadenas or []) for c in D.cads(x)],
                  "visitas_extra": visitas_extra, "reasignar": reasignar, "efecto_visita": efecto_visita,
                  "resurtir_pct": resurtir_pct, "rebalanceo_pct": rebalanceo_pct, "excedente_pct": excedente_pct,
                  "estancado_pct": estancado_pct, "dias_max": dias_max}
    hs, Ts, Ks, det = simulador.simular(h, T, D.TI, D.M, params)
    cfg = D.M["kpis"]
    kp_ = ["agotados", "excedente", "estancado", "prioritarias", "dias", "venta"]
    return {"semana": motor.etiqueta_semana(k), "escenario": nombre, "parametros": params,
            "kpis": {cfg[x]["nombre"]: {"hoy": motor.fmt(x, K[x]), "simulado": motor.fmt(x, Ks[x]),
                                        "estado_simulado": motor.estado(x, Ks[x], cfg[x], K[x])[1]} for x in kp_},
            "productos_agotados": f"{det['agotados_hoy']} -> {det['agotados_sim']}",
            "venta_recuperada_pzs_sem": _r(det["venta_recuperada"], 0),
            "supuestos": det["supuestos"],
            "nota": "Es una estimación con supuestos, no una predicción. Menciónalo y di los supuestos principales."}


SEMANA = {"type": "string", "description": "Semana, ej. 'S3' o 'P11 S3'. Omitir = semana recomendada."}
CADENA = {"type": "string", "description": "Cadena (WALMART, Soriana, Chedraui, HEB, SAMS, COSTCO, City Club). Omitir = todas."}

TOOLS = [
    {"name": "semanas_disponibles", "description": "Lista las semanas de la base, cuál es la recomendada y problemas de calidad de cada una.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "buscar_tienda", "description": "Busca tiendas por nombre, id o ciudad. Úsala si el nombre que dio el usuario es ambiguo.",
     "input_schema": {"type": "object", "properties": {"texto": {"type": "string"}}, "required": ["texto"]}},
    {"name": "resumen_tienda", "description": "Todo de una tienda en una semana: venta, inventario, agotados, estancados, excedente, urgencia, si se visita y venta de la semana anterior.",
     "input_schema": {"type": "object", "properties": {"tienda": {"type": "string", "description": "Nombre o id de la tienda"}, "semana": SEMANA}, "required": ["tienda"]}},
    {"name": "kpis", "description": "Los 7 KPIs (agotados, excedente, estancado, prioritarias visitadas, días, venta, tiendas reportadas) con meta y estado, para todo Mars o filtrado por cadena y/o estado.",
     "input_schema": {"type": "object", "properties": {"semana": SEMANA, "cadena": CADENA, "estado": {"type": "string", "description": "Estado de la república"}}}},
    {"name": "ranking", "description": "Top o bottom N por una métrica, agrupando por tienda, cadena, estado, producto, categoría, regional, ejecutivo o ciudad.",
     "input_schema": {"type": "object", "properties": {
         "metrica": {"type": "string", "enum": list(METRICAS)}, "nivel": {"type": "string", "enum": list(NIVELES)},
         "n": {"type": "integer", "description": "Cuántos (máx. 25)"}, "orden": {"type": "string", "enum": ["mayor", "menor"]},
         "semana": SEMANA, "cadena": CADENA, "estado": {"type": "string"},
         "solo_no_visitadas": {"type": "boolean", "description": "Solo tiendas que Mars no visita"}}, "required": ["metrica", "nivel"]}},
    {"name": "info_producto", "description": "Venta, inventario, tiendas agotadas y excedente de un producto (Descripcion Unificada).",
     "input_schema": {"type": "object", "properties": {"producto": {"type": "string"}, "semana": SEMANA, "cadena": CADENA}, "required": ["producto"]}},
    {"name": "semana_a_semana", "description": "Evolución de una métrica en todas las semanas, opcionalmente para una tienda, cadena o producto.",
     "input_schema": {"type": "object", "properties": {"metrica": {"type": "string", "enum": [m for m in METRICAS if m != "urgencia"]},
                                                       "tienda": {"type": "string"}, "cadena": CADENA, "producto": {"type": "string"}}, "required": ["metrica"]}},
    {"name": "acciones", "description": "Lista de acciones recomendadas por tienda-producto (resurtir, revisar estancado, no resurtir excedente), filtrable por tienda, ejecutivo y tipo.",
     "input_schema": {"type": "object", "properties": {"tienda": {"type": "string"}, "ejecutivo": {"type": "string", "description": "ej. 'Ejecutivo 14'"},
                                                       "tipo": {"type": "string", "enum": ["Agotado", "Por agotarse", "Estancado", "Excedente"]},
                                                       "n": {"type": "integer"}, "semana": SEMANA, "cadena": CADENA}}},
    {"name": "simular", "description": "Simula '¿qué pasaría si…?' y regresa los KPIs de hoy vs. simulados. Úsala para "
     "preguntas hipotéticas: cambios de demanda (ej. Navidad), visitar más tiendas, resurtir agotados, mover o reducir "
     "excedente, recuperar estancado o cambiar la regla de días. Puede usar un escenario guardado por nombre.",
     "input_schema": {"type": "object", "properties": {
         "escenario": {"type": "string", "description": "Nombre de un escenario guardado (Navidad sin plan, Reasignar visitas, "
                                                         "Rebalanceo de excedente, Limpieza de estancado, Plan para llegar a las metas, Navidad con plan). Si se usa, ignora lo demás."},
         "demanda_pct": {"type": "number", "description": "Cambio de venta en %, de -50 a 100"},
         "demanda_cadenas": {"type": "array", "items": {"type": "string"}, "description": "Cadenas donde cambia la venta (vacío = todas)"},
         "visitas_extra": {"type": "integer", "description": "Tiendas prioritarias sin visita que entran a la ruta (-1 = todas)"},
         "reasignar": {"type": "boolean", "description": "Mismo equipo: salen de la ruta las visitadas menos urgentes"},
         "efecto_visita": {"type": "number", "description": "% de agotados y estancado que resuelve una visita (defecto 50)"},
         "resurtir_pct": {"type": "number"}, "rebalanceo_pct": {"type": "number"}, "excedente_pct": {"type": "number"},
         "estancado_pct": {"type": "number"}, "dias_max": {"type": "number", "description": "Días a partir de los que hay excedente"},
         "semana": SEMANA}}},
    {"name": "visitadas_vs_no", "description": "Compara tiendas visitadas contra no visitadas: tiendas, venta, venta por tienda, % agotados y % estancado.",
     "input_schema": {"type": "object", "properties": {"semana": SEMANA, "cadena": CADENA}}},
]
FUNCIONES = {"semanas_disponibles": h_semanas, "buscar_tienda": h_buscar_tienda, "resumen_tienda": h_resumen_tienda,
             "kpis": h_kpis, "ranking": h_ranking, "info_producto": h_producto, "semana_a_semana": h_semana_a_semana,
             "acciones": h_acciones, "visitadas_vs_no": h_visitadas, "simular": h_simular}


def ejecutar(D: Datos, nombre: str, args: dict) -> str:
    try:
        res = FUNCIONES[nombre](D, **(args or {}))
    except Exception as e:  # noqa: BLE001
        res = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
    return json.dumps(res, ensure_ascii=False, default=str)


def instrucciones(D: Datos) -> str:
    cfg = D.M["kpis"]
    metas = "; ".join(f"{cfg[k]['nombre']}: meta {cfg[k]['meta']}" for k in motor.ORDEN_KPIS if k != "venta")
    return f"""Eres el asistente de datos de la plataforma "Mars · Retail Intelligence" (prototipo del Reto Tec con Mars).
Respondes preguntas rápidas sobre inventario y venta de productos Mars en tiendas de autoservicio de México.

Reglas:
- Responde en español, corto y directo (2 a 5 frases o una lista corta). Usa **negritas** para los números clave.
- TODOS los números deben salir de las herramientas. Nunca inventes ni estimes cifras. Si una herramienta no lo cubre, dilo.
- Si el nombre de una tienda o producto es ambiguo, usa buscar_tienda y pregunta cuál, mostrando las opciones.
- Unidades: todo está en PIEZAS (no hay pesos en la base). "Venta" = promedio de piezas por semana de las últimas 5 semanas (columna ~Prom Venta L5W).
- "Esta semana" = la semana recomendada ({motor.etiqueta_semana(D.CAL['recomendada'])}). Menciona la semana en tu respuesta.
- Si la herramienta trae "avisos_semana", menciónalos en una frase.
- Glosario: agotado = inventario 0 con venta; por agotarse = menos de {D.M['umbrales']['dias_riesgo']} días contando lo que viene en camino;
  excedente = piezas por encima de {D.M['umbrales']['dias_max']} días de venta; estancado = inventario con venta 0 (posible inventario fantasma);
  urgencia = calificación 0-100 por tienda; prioritarias = el {D.M['pct_prioritarias']}% de tiendas más urgentes.
- Metas: {metas}.
- Para preguntas hipotéticas ("¿qué pasaría si…?") usa la herramienta simular y aclara que es una estimación con supuestos.
- Si te piden algo fuera de esta base (precios, otras empresas, opiniones), di amablemente que solo puedes responder sobre estos datos.

Cadenas: {', '.join(D.cadenas)}. Semanas en la base: {', '.join(motor.etiqueta_semana(k) for k in D.semanas)}."""


def responder(cliente, D: Datos, historial: list[dict], pregunta: str) -> tuple[str, list[str]]:
    """(respuesta, herramientas_usadas). `historial` = [{'role','content'}] solo con textos de turnos previos."""
    mensajes = historial[-8:] + [{"role": "user", "content": pregunta}]
    usadas = []
    for _ in range(MAX_VUELTAS):
        r = cliente.messages.create(model=MODELO, max_tokens=1024, system=instrucciones(D), tools=TOOLS, messages=mensajes)
        if r.stop_reason != "tool_use":
            texto = "".join(b.text for b in r.content if getattr(b, "type", "") == "text").strip()
            return texto or "No tengo una respuesta para eso.", usadas
        mensajes.append({"role": "assistant", "content": r.content})
        resultados = []
        for b in r.content:
            if getattr(b, "type", "") == "tool_use":
                usadas.append(f"{b.name}({', '.join(f'{k}={v}' for k, v in (b.input or {}).items())})")
                resultados.append({"type": "tool_result", "tool_use_id": b.id, "content": ejecutar(D, b.name, b.input)})
        mensajes.append({"role": "user", "content": resultados})
    return "La pregunta necesitó demasiados pasos. ¿Puedes hacerla más específica?", usadas
