"""Simulador "¿qué pasaría si…?" para los KPIs de Mars.

Toma la tabla de una semana (h, ya clasificada) y aplica cambios en este orden:
  1. demanda      : la venta sube o baja X% (en todas o en algunas cadenas)
  2. visitas      : N tiendas prioritarias que hoy no se visitan entran a la ruta (opcional: reasignando
                    las visitadas con menos urgencia, para usar el mismo equipo). La visita resuelve
                    una parte de sus agotados y de su inventario estancado (supuesto ajustable).
  3. resurtir     : se resurte un % de los productos agotados (primero los que más venden)
  4. rebalanceo   : se mueve un % del excedente a tiendas de la misma cadena y estado donde ese
                    producto está agotado o por agotarse
  5. estancado    : se recupera un % de las piezas sin venta (bodega, inventario fantasma, devolución)
  6. dias_max     : cambia la regla de cuántos días cuentan como excedente
Después vuelve a calcular todo con el mismo motor que usa el tablero, así los números son comparables.
Son estimaciones con supuestos, no predicciones.
"""
from __future__ import annotations

import copy

import numpy as np
import pandas as pd

import motor

BASE_COLS = ["semana_key", "tienda_id", "producto", "categoria", "pzs_caja", "on_hand", "transito", "orden",
             "tuberia", "venta", "invalido", "codigos", "cadena"]

PARAMS_DEFECTO = {"demanda_pct": 0, "demanda_cadenas": [], "visitas_extra": 0, "reasignar": False,
                  "efecto_visita": 50, "resurtir_pct": 0, "rebalanceo_pct": 0, "excedente_pct": 0, "estancado_pct": 0, "dias_max": None}


def _surtir(h: pd.DataFrame, idx, dobj: pd.Series) -> float:
    """Resurte las filas `idx` hasta cubrir `dobj` días de venta (en cajas completas). Devuelve piezas agregadas."""
    if len(idx) == 0:
        return 0.0
    vd = h.loc[idx, "venta"] / 7
    falta = (vd * dobj.loc[idx] - h.loc[idx, "tuberia"]).clip(lower=0)
    cajas = np.ceil(falta / h.loc[idx, "pzs_caja"])
    agrega = cajas * h.loc[idx, "pzs_caja"]
    h.loc[idx, "on_hand"] += agrega
    h.loc[idx, "tuberia"] += agrega
    return float(agrega.sum())


def simular(h0: pd.DataFrame, T0: pd.DataFrame, tiendas: pd.DataFrame, marca: dict, p: dict):
    """Devuelve (h_sim, T_sim, K_sim, detalle). h0 y T0 son los de la semana ya calculados por el tablero."""
    p = {**PARAMS_DEFECTO, **(p or {})}
    M = copy.deepcopy(marca)
    if p["dias_max"]:
        M["umbrales"]["dias_max"] = float(p["dias_max"])
        M["umbrales"]["por_cadena"] = {c: {k: v for k, v in d.items() if k != "dias_max"}
                                      for c, d in (M["umbrales"].get("por_cadena") or {}).items()}
    h = h0[BASE_COLS].copy()
    h["on_hand"] = h["on_hand"].astype(float)
    h["tuberia"] = h["tuberia"].astype(float)
    h["venta"] = h["venta"].astype(float)
    ti = tiendas.copy()
    det = {"supuestos": [], "piezas_resurtidas": 0.0, "piezas_movidas": 0.0, "piezas_reducidas": 0.0, "piezas_recuperadas": 0.0, "tiendas_nuevas": [],
           "tiendas_quitadas": []}
    dobj = motor.umbral(M, h["cadena"], "dias_objetivo")

    # 1. demanda
    if p["demanda_pct"]:
        m = h["cadena"].isin(p["demanda_cadenas"]) if p["demanda_cadenas"] else pd.Series(True, index=h.index)
        extra = h.loc[m, "venta"] * p["demanda_pct"] / 100
        h.loc[m, "venta"] += extra
        sale = np.minimum(extra.clip(lower=0), h.loc[m, "on_hand"])   # la venta extra de la semana sale del anaquel
        h.loc[m, "on_hand"] -= sale
        h.loc[m, "tuberia"] = (h.loc[m, "tuberia"] - sale).clip(lower=0)
        donde = ", ".join(p["demanda_cadenas"]) if p["demanda_cadenas"] else "todas las cadenas"
        det["supuestos"].append(f"La venta cambia {p['demanda_pct']:+.0f}% en {donde}. La venta extra de una semana sale del "
                                "inventario en tienda, así que los productos con poco inventario se agotan.")

    # 2. visitas
    n = int(p["visitas_extra"] or 0)
    candidatas = T0[T0["prioritaria"] & (T0["se_visita"] == "No")].sort_values("urgencia", ascending=False)
    if n < 0:
        n = len(candidatas)
    nuevas = candidatas.head(n).index.tolist()
    if nuevas:
        ti.loc[nuevas, "se_visita"] = "Sí"
        det["tiendas_nuevas"] = nuevas
        ef = p["efecto_visita"] / 100
        hc = motor.clasificar(h, M)
        en = hc["tienda_id"].isin(nuevas)
        ag = hc[en & (hc["estatus"] == "Agotado")].sort_values("venta", ascending=False)
        resueltos = ag.groupby("tienda_id", group_keys=False).apply(lambda g: g.head(int(np.ceil(len(g) * ef))))
        det["piezas_resurtidas"] += _surtir(h, resueltos.index, dobj)
        est = hc.index[en & (hc["estatus"] == "Estancado")]
        rec = np.floor(h.loc[est, "on_hand"] * ef)
        h.loc[est, "on_hand"] -= rec
        h.loc[est, "tuberia"] -= rec
        det["piezas_recuperadas"] += float(rec.sum())
        texto = f"{len(nuevas)} tiendas prioritarias que no se visitaban entran a la ruta; la visita resuelve el {p['efecto_visita']:.0f}% de sus agotados y de su inventario estancado."
        if p["reasignar"]:
            quitar = T0[(T0["se_visita"] == "Sí") & ~T0["prioritaria"]].sort_values("urgencia").head(len(nuevas)).index.tolist()
            ti.loc[quitar, "se_visita"] = "No"
            det["tiendas_quitadas"] = quitar
            texto += f" Para usar el mismo equipo, salen de la ruta las {len(quitar)} tiendas visitadas con menos urgencia (se supone que esta semana no empeoran)."
        det["supuestos"].append(texto)

    # 3. resurtir agotados
    if p["resurtir_pct"]:
        hc = motor.clasificar(h, M)
        ag = hc[hc["estatus"] == "Agotado"].sort_values("venta", ascending=False)
        idx = ag.head(int(np.ceil(len(ag) * p["resurtir_pct"] / 100))).index
        det["piezas_resurtidas"] += _surtir(h, idx, dobj)
        det["supuestos"].append(f"Se resurte el {p['resurtir_pct']:.0f}% de los productos agotados (primero los que más venden), "
                                f"con cajas completas para cubrir {M['umbrales']['dias_objetivo']} días de venta.")

    # 4. rebalanceo
    if p["rebalanceo_pct"]:
        hc = motor.clasificar(h, M).join(ti[["estado"]], on="tienda_id")
        vd = hc["venta"] / 7
        hc["_dona"] = hc["excedente_pzs"] * p["rebalanceo_pct"] / 100
        hc["_necesita"] = np.where(hc["estatus"].isin(["Agotado", "Por agotarse"]),
                                   (vd * dobj - hc["tuberia"]).clip(lower=0), 0.0)
        claves = ["producto", "cadena", "estado"]
        hc = hc.sort_values("venta", ascending=False)
        g = hc.groupby(claves)
        mover = np.minimum(g["_dona"].transform("sum"), g["_necesita"].transform("sum"))
        antes = g["_necesita"].cumsum() - hc["_necesita"]
        recibe = (mover - antes).clip(lower=0).clip(upper=hc["_necesita"])
        total_recibe = recibe.groupby([hc[c] for c in claves]).transform("sum")
        dona_sum = g["_dona"].transform("sum")
        da = np.where(dona_sum > 0, hc["_dona"] / dona_sum.where(dona_sum > 0, 1) * total_recibe, 0.0)
        cambio = recibe - pd.Series(da, index=hc.index)
        h.loc[cambio.index, "on_hand"] += cambio
        h.loc[cambio.index, "tuberia"] += cambio
        movidas = float(recibe.sum())
        det["piezas_movidas"] = movidas
        det["supuestos"].append(f"Del {p['rebalanceo_pct']:.0f}% del excedente, se mueve solo lo que necesitan tiendas de la misma cadena "
                                "y estado donde ese producto está agotado o por agotarse (no se mueve entre cadenas).")

    # 4b. reducir el excedente que queda (promoción, devolución o dejar de resurtir)
    if p["excedente_pct"]:
        hc = motor.clasificar(h, M)
        quita = np.floor(hc["excedente_pzs"] * p["excedente_pct"] / 100)
        h.loc[quita.index, "on_hand"] -= quita
        h.loc[quita.index, "tuberia"] = (h.loc[quita.index, "tuberia"] - quita).clip(lower=0)
        det["piezas_reducidas"] = float(quita.sum())
        det["supuestos"].append(f"El {p['excedente_pct']:.0f}% del excedente que queda se reduce con promoción, devolución o "
                                f"dejando de resurtirlo; nunca baja de {M['umbrales']['dias_max']:.0f} días de venta.")

    # 5. estancado
    if p["estancado_pct"]:
        m = (h["venta"] <= 0) & (h["on_hand"] > 0)
        rec = np.floor(h.loc[m, "on_hand"] * p["estancado_pct"] / 100)
        h.loc[m, "on_hand"] -= rec
        h.loc[m, "tuberia"] -= rec
        det["piezas_recuperadas"] += float(rec.sum())
        det["supuestos"].append(f"Se recupera el {p['estancado_pct']:.0f}% de las piezas sin venta (sacarlas de bodega, corregir "
                                "inventario fantasma o devolverlas).")

    if p["dias_max"]:
        det["supuestos"].append(f"El excedente se cuenta a partir de {p['dias_max']:.0f} días de venta (en vez de {marca['umbrales']['dias_max']}).")

    h["on_hand"] = h["on_hand"].clip(lower=0)
    h["tuberia"] = h["tuberia"].clip(lower=0)
    hs = motor.clasificar(h, M)
    Ts = motor.tabla_tiendas(hs, ti, M)
    K0 = motor.kpis(h0, T0, np.nan)
    Ks = motor.kpis(hs, Ts, np.nan)
    # "Prioritarias visitadas" se mide sobre las MISMAS tiendas prioritarias de hoy (si no, al mejorar cambiarían cuáles son)
    pri = T0.index[T0["prioritaria"]]
    Ks["prioritarias"] = (ti.loc[pri, "se_visita"] == "Sí").mean() * 100 if len(pri) else np.nan
    Ks["n_prioritarias"] = len(pri)
    Ks["pri_no_visitadas"] = int((ti.loc[pri, "se_visita"] == "No").sum())
    era_ag = h0["estatus"] == "Agotado"
    det["venta_recuperada"] = float(h0.loc[era_ag & (hs["estatus"] != "Agotado"), "venta"].sum())
    det["agotados_hoy"] = int((h0["estatus"] == "Agotado").sum())
    det["agotados_sim"] = int((hs["estatus"] == "Agotado").sum())
    _ = K0
    return hs, Ts, Ks, det


def resumen(K0: dict, Ks: dict, det: dict, marca: dict) -> str:
    cfg = marca["kpis"]
    partes = []
    for kp in ["agotados", "excedente", "estancado", "prioritarias"]:
        a, b = K0.get(kp), Ks.get(kp)
        if pd.notna(a) and pd.notna(b) and abs(b - a) >= 0.05:
            meta = "✓ en meta" if motor.estado(kp, b, cfg[kp])[0] == "verde" else f"meta {cfg[kp]['meta']}%"
            partes.append(f"**{cfg[kp]['nombre']}** pasaría de {motor.fmt(kp, a)} a **{motor.fmt(kp, b)}** ({meta})")
    if not partes:
        return "Con estos valores los indicadores casi no cambian. Mueve algún control para simular."
    txt = "Con este escenario, " + "; ".join(partes) + "."
    if det.get("venta_recuperada"):
        txt += f" Se recuperarían unas **{det['venta_recuperada']:,.0f} piezas por semana** de productos que hoy están agotados."
    return txt
