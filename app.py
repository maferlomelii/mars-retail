"""Mars · Retail Intelligence (prototipo Reto Tec AD2007B).

Lee el Excel de Mars directo de Google Drive (tal cual llega, sin convertirlo), lo limpia y muestra
una vista por tipo de usuario: Dirección (Resumen), Mandos medios (Regiones y tiendas) y
Operaciones (Alertas). Para actualizar, basta con reemplazar el Excel en Drive: la app lo detecta.

Modos:
  - Drive: si hay secretos GOOGLE_SERVICE_ACCOUNT_JSON y DRIVE_FILE_ID.
  - Demo: si no los hay, usa datos ficticios con el mismo formato (datos_demo.py).
  - Además, en la barra lateral se puede subir otro Excel solo para la sesión.
Todo lo propio del cliente (colores, metas, umbrales) está en marca.yaml.
"""
import json
import os
import re
from datetime import datetime

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st
import streamlit_authenticator as stauth
import yaml
from yaml.loader import SafeLoader

import agente
import datos_demo
import motor
from report import build_pdf

HERE = os.path.dirname(os.path.abspath(__file__))
REVISAR_CADA = 60  # segundos entre cada revisión de si cambió el Excel en Drive

with open(os.path.join(HERE, "marca.yaml"), encoding="utf-8") as _f:
    MARCA = yaml.load(_f, Loader=SafeLoader)
C = MARCA["colores"]
KCFG = MARCA["kpis"]

st.set_page_config(page_title=MARCA["titulo_app"], page_icon=MARCA.get("icono", "📊"), layout="wide")

st.markdown(f"""
<style>
[data-testid="stMain"] h1, [data-testid="stMain"] h2, [data-testid="stMain"] h3 {{ color: {C['oscuro']}; }}
[data-testid="stMain"] .block-container {{ padding-top: 2.2rem; max-width: 1350px; }}
.brand {{ font-family: 'Outfit', sans-serif; font-weight: 800; font-size: 2.1rem; color: #fff; line-height: 1; letter-spacing: -.01em; }}
.brand span {{ color: {C['rosa']}; }}
.brand small {{ display:block; font-family: 'DM Sans', sans-serif; font-weight: 600; font-size: .74rem;
letter-spacing: .14em; text-transform: uppercase; color: {C['acento_claro']}; margin-top: .45rem; }}
.kpi {{ background: {C['tarjeta']}; border: 1px solid {C['linea']}; border-radius: 16px; padding: 14px 16px 12px; height: 100%;
box-shadow: 0 1px 2px rgba(10,10,92,.04); }}
.kpi .u {{ font-size: .68rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; color: {C['acento_claro']}; }}
.kpi .n {{ font-size: .88rem; color: {C['gris']}; font-weight: 600; }}
.kpi .v {{ font-family: 'Outfit', sans-serif; font-size: 2.05rem; font-weight: 800; color: {C['oscuro']}; line-height: 1.15; }}
.kpi .m {{ font-size: .8rem; color: {C['gris']}; margin: 2px 0 8px; }}
.kpi .d-bien {{ color: {C['verde']}; font-weight: 700; }}
.kpi .d-mal {{ color: {C['rojo']}; font-weight: 700; }}
.pill {{ display:inline-block; font-size: .74rem; font-weight: 700; padding: 2px 10px; border-radius: 99px; color: #fff; }}
.pill.verde {{ background: {C['verde']}; }} .pill.amarillo {{ background: {C['amarillo']}; }}
.pill.rojo {{ background: {C['rojo']}; }} .pill.gris {{ background: {C['gris']}; }}
.palabras {{ background: {C['acento']}; color: #fff; border-radius: 16px; padding: 16px 20px; font-size: 1.03rem; line-height: 1.6; }}
.palabras b {{ color: #FFD2E0; }}
.alerta {{ background: {C['tarjeta']}; border: 1px solid {C['linea']}; border-left: 6px solid {C['gris']};
border-radius: 12px; padding: 12px 16px; margin-bottom: 10px; }}
.alerta.a-rojo {{ border-left-color: {C['rojo']}; }} .alerta.a-naranja {{ border-left-color: {C['amarillo']}; }}
.alerta.a-verde {{ border-left-color: {C['verde']}; }}
.alerta .t {{ font-weight: 700; color: {C['oscuro']}; }} .alerta .x {{ color: #333A5C; font-size: .95rem; }}
.vivo {{ display:inline-block; background: {C['verde']}; color:#fff; font-size:.72rem; font-weight:700;
padding: 2px 9px; border-radius: 99px; margin-left: 8px; vertical-align: middle; }}
.chip {{ display:inline-block; background:{C['tarjeta']}; border:1px solid {C['linea']}; border-radius: 99px;
padding: 4px 12px; margin: 0 6px 6px 0; font-size:.88rem; color:{C['oscuro']}; }}
.chip b {{ font-family: 'Outfit', sans-serif; }}
[data-testid="stSidebar"] [data-testid="stRadioOption"] {{ padding: .45rem .8rem; border-radius: 10px; margin-bottom: .2rem; }}
[data-testid="stSidebar"] [data-testid="stRadioOption"][data-selected="true"] {{ background: {C['rosa']}; }}
[data-testid="stSidebar"] [data-testid="stRadioOption"][data-selected="true"] p {{ color: #fff; font-weight: 700; }}
[data-testid="stSidebar"] [data-testid="stRadioOption"] > div > div:first-child {{ display: none; }}
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------- conexión a Google Drive
def secreto(nombre, defecto=None):
    try:
        if nombre in st.secrets:
            return st.secrets[nombre]
    except Exception:  # noqa: BLE001
        pass
    return os.environ.get(nombre, defecto)


def creds_google():
    raw = secreto("GOOGLE_SERVICE_ACCOUNT_JSON")
    if raw:
        return json.loads(raw) if isinstance(raw, str) else dict(raw)
    p = os.path.join(HERE, "service_account.json")
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return None


def id_de_drive(valor):
    """Acepta el id solo o el link completo del archivo de Drive."""
    if not valor:
        return None
    m = re.search(r"/d/([A-Za-z0-9_-]{20,})", str(valor)) or re.search(r"id=([A-Za-z0-9_-]{20,})", str(valor))
    return m.group(1) if m else str(valor).strip()


FILE_ID = id_de_drive(secreto("DRIVE_FILE_ID"))
EXCEL_LOCAL = secreto("EXCEL_LOCAL")  # solo para probar en la computadora
MODO = "drive" if (creds_google() and FILE_ID) else ("local" if EXCEL_LOCAL and os.path.exists(EXCEL_LOCAL) else "demo")
API = "https://www.googleapis.com/drive/v3/files/"


@st.cache_resource(show_spinner=False)
def sesion_drive():
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2.service_account import Credentials
    cr = Credentials.from_service_account_info(creds_google(), scopes=["https://www.googleapis.com/auth/drive.readonly"])
    return AuthorizedSession(cr)


@st.cache_data(ttl=REVISAR_CADA, show_spinner=False)
def info_drive(file_id):
    r = sesion_drive().get(API + file_id, params={"fields": "name,modifiedTime,size", "supportsAllDrives": "true"}, timeout=30)
    if r.status_code == 404:
        raise RuntimeError("Drive no encuentra el archivo. Revisa el DRIVE_FILE_ID y que el Excel esté compartido "
                           "con el correo de la cuenta de servicio (client_email del JSON).")
    r.raise_for_status()
    return r.json()


@st.cache_data(max_entries=2, show_spinner="Leyendo el Excel de Mars desde Google Drive…")
def base_drive(file_id, modificado):
    r = sesion_drive().get(API + file_id, params={"alt": "media", "supportsAllDrives": "true"}, timeout=300)
    r.raise_for_status()
    if r.content[:2] != b"PK":
        raise RuntimeError("El archivo de Drive no es un Excel (.xlsx). Súbelo como Excel, sin convertirlo a Hojas de cálculo de Google.")
    return motor.preparar(motor.leer_excel(r.content))


@st.cache_data(max_entries=1, show_spinner="Leyendo el Excel…")
def base_bytes(_contenido, clave):
    return motor.preparar(motor.leer_excel(_contenido))


@st.cache_data(show_spinner="Preparando datos de ejemplo…")
def base_demo():
    return motor.preparar(datos_demo.generar())


# ----------------------------------------------------------------------- login
def to_plain(obj):
    if hasattr(obj, "items"):
        return {k: to_plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_plain(v) for v in obj]
    return obj


def config_login():
    try:
        if "credentials" in st.secrets and "auth_cookie" in st.secrets:
            return {"credentials": to_plain(st.secrets["credentials"]), "cookie": to_plain(st.secrets["auth_cookie"])}
    except Exception:  # noqa: BLE001
        pass
    p = os.path.join(HERE, "config.yaml")
    if not os.path.exists(p):
        p = os.path.join(HERE, "config.example.yaml")
    with open(p, encoding="utf-8") as f:
        return yaml.load(f, Loader=SafeLoader)


cfg_login = config_login()
auth = stauth.Authenticate(cfg_login["credentials"], cfg_login["cookie"]["name"],
                           cfg_login["cookie"]["key"], cfg_login["cookie"]["expiry_days"])
if st.session_state.get("authentication_status") is not True:
    st.markdown(f"<style>.stApp {{ background: {C['acento']}; }} [data-testid='stHeader'] {{ background: transparent; }}"
                f"[data-testid='stForm'] {{ background: #fff !important; border-radius: 18px !important; border: none !important;"
                f" padding: 1.4rem 1.6rem !important; box-shadow: 0 12px 40px rgba(0,0,40,.35); }}</style>",
                unsafe_allow_html=True)
_, col_login, _ = st.columns([1, 1.4, 1])
with col_login:
    marca_slot = st.empty()
    if st.session_state.get("authentication_status") is not True:
        marca_slot.markdown("<div style='text-align:center;margin:3rem 0 1.5rem'><div class='brand' style='font-size:3.4rem'>"
                            "MARS<span>.</span><small>Retail Intelligence · Reto Tec</small></div></div>",
                            unsafe_allow_html=True)
    auth.login(location="main", fields={"Form name": "Iniciar sesión", "Username": "Usuario",
                                        "Password": "Contraseña", "Login": "Entrar"})
estado_login = st.session_state.get("authentication_status")
if estado_login is False:
    with col_login:
        st.error("Usuario o contraseña incorrectos.")
    st.stop()
if estado_login is None:
    with col_login:
        st.markdown("<p style='color:#fff;text-align:center'>Demo: usuario <b>demo</b> · contraseña <b>demo1234</b></p>",
                    unsafe_allow_html=True)
    st.stop()
marca_slot.empty()

# ------------------------------------------------------------------- barra lateral
PAGINAS = ["📊 Resumen", "🗺️ Regiones y tiendas", "🚨 Alertas", "🤖 Pregúntale a la base", "🧪 Calidad del dato", "📄 Reporte"]
QUIEN = {"📊 Resumen": "Dirección", "🗺️ Regiones y tiendas": "Mandos medios", "🚨 Alertas": "Operaciones",
         "🤖 Pregúntale a la base": "Todos", "🧪 Calidad del dato": "Todos", "📄 Reporte": "Dirección"}
with st.sidebar:
    st.markdown("<div class='brand'>MARS<span>.</span><small>Retail Intelligence</small></div>", unsafe_allow_html=True)
    st.write("")
    pagina = st.radio("Sección", PAGINAS, label_visibility="collapsed", key="pagina",
                      format_func=lambda p: f"{p}  ·  {QUIEN[p]}")
    st.divider()
    zona_filtros = st.container()
    st.divider()
    if st.button("🔄 Actualizar ahora", width="stretch"):
        info_drive.clear()
        st.rerun()
    with st.expander("Probar con otro Excel"):
        subido = st.file_uploader("Excel con el formato de Mars", type=["xlsx"], label_visibility="collapsed")
        st.caption("Solo se usa en esta sesión; no reemplaza el de Drive.")
    st.caption(f"👤 {st.session_state.get('name', '')}")
    auth.logout("Cerrar sesión", "sidebar")


# ------------------------------------------------------------------- cargar datos
try:
    if subido is not None:
        H, TI, avisos = base_bytes(subido.getvalue(), f"{subido.name}-{subido.size}")
        origen, actualizado = f"Excel subido «{subido.name}» (solo esta sesión)", None
    elif MODO == "drive":
        meta = info_drive(FILE_ID)
        H, TI, avisos = base_drive(FILE_ID, meta["modifiedTime"])
        origen = f"Google Drive «{meta['name']}»"
        actualizado = pd.Timestamp(meta["modifiedTime"]).tz_convert("America/Mexico_City")
        st.session_state["modificado"] = meta["modifiedTime"]
    elif MODO == "local":
        H, TI, avisos = base_bytes(open(EXCEL_LOCAL, "rb").read(), EXCEL_LOCAL)
        origen, actualizado = f"archivo local «{os.path.basename(EXCEL_LOCAL)}»", None
    else:
        H, TI, avisos = base_demo()
        origen, actualizado = "datos ficticios (no hay Excel conectado)", None
except Exception as e:  # noqa: BLE001
    st.error(f"No pude leer el Excel. {type(e).__name__}: {str(e)[:300]}")
    st.stop()

if H.empty:
    st.warning("El Excel no tiene renglones con tienda y semana.")
    st.stop()

CAL = motor.calidad(H, MARCA)
SEMANAS = sorted(H["semana_key"].unique(), reverse=True)


def etiqueta(k):
    r = CAL["resumen"].loc[k]
    extra = " · recomendada" if k == CAL["recomendada"] else (" · ⚠️ venta sospechosa" if r["Venta sospechosa"] else "")
    return motor.etiqueta_semana(k) + extra


with zona_filtros:
    semana = st.selectbox("Semana", SEMANAS, index=SEMANAS.index(CAL["recomendada"]), format_func=etiqueta, key="semana")
    cadenas_todas = sorted(H["cadena"].dropna().unique())
    cadenas = st.multiselect("Cadena", cadenas_todas, placeholder="Todas las cadenas", key="cadenas")
CADS = tuple(cadenas) if cadenas else tuple(cadenas_todas)
ORIGEN_KEY = f"{origen}|{st.session_state.get('modificado', '')}"


@st.cache_data(max_entries=40, show_spinner=False)
def semana_calc(_H, _TI, clave, k, cads):
    """(h, T, K) de una semana para las cadenas elegidas."""
    h = motor.clasificar(_H[(_H["semana_key"] == k) & _H["cadena"].isin(cads)], MARCA)
    T = motor.tabla_tiendas(h, _TI, MARCA)
    tt = CAL["tiendas"].reindex(list(cads)).fillna(0)
    cob = tt[k].sum() / tt.max(axis=1).sum() * 100 if k in tt and tt.max(axis=1).sum() else np.nan
    return h, T, motor.kpis(h, T, cob)


def calc(k):
    return semana_calc(H, TI, ORIGEN_KEY, k, CADS)


h, T, K = calc(semana)
i_sem = SEMANAS.index(semana)
KPREV = calc(SEMANAS[i_sem + 1])[2] if i_sem + 1 < len(SEMANAS) else {}
SEM_TXT = motor.etiqueta_semana(semana)


# ------------------------------------------------------------------- piezas visuales
def meta_txt(kp):
    cfg = KCFG[kp]
    if kp == "venta":
        return "Sin caer vs. semana anterior"
    signo = "≥" if cfg["mas_es_mejor"] else "≤"
    return f"Meta {signo} {motor.fmt(kp, cfg['meta'])}"


def tarjeta(kp, v, vprev, col):
    cfg = KCFG[kp]
    e, txt = motor.estado(kp, v, cfg, vprev)
    delta = "&nbsp;"
    if pd.notna(v) and vprev is not None and pd.notna(vprev):
        dif = (v / vprev - 1) * 100 if kp == "venta" and vprev else v - vprev
        if abs(dif) >= 0.05:
            bien = (dif > 0) == cfg["mas_es_mejor"]
            u = {"venta": "%", "dias": " días"}.get(kp, " pts")
            delta = f"<span class='{'d-bien' if bien else 'd-mal'}'>{'▲' if dif > 0 else '▼'} {abs(dif):.1f}{u}</span> vs. semana anterior"
    col.markdown(f"<div class='kpi'><div class='u'>{cfg['usuario']}</div><div class='n'>{cfg['nombre']}</div>"
                 f"<div class='v'>{motor.fmt(kp, v)}</div><div class='m'>{meta_txt(kp)}<br>{delta}</div>"
                 f"<span class='pill {e}'>{txt}</span></div>", unsafe_allow_html=True)


def tarjetas(k, kprev, cuales=motor.ORDEN_KPIS):
    for i in range(0, len(cuales), 4):
        cols = st.columns(4)
        for kp, col in zip(cuales[i:i + 4], cols):
            tarjeta(kp, k.get(kp), kprev.get(kp) if kprev else None, col)
        st.write("")


def alerta_html(prio, titulo, texto):
    n = motor.nivel(prio)
    clase = {"🔴": "a-rojo", "🟠": "a-naranja", "🟢": "a-verde"}[n]
    etiq = {"🔴": "Urgente", "🟠": "Importante", "🟢": "Bien"}[n]
    st.markdown(f"<div class='alerta {clase}'><div class='t'>{n} {etiq} · {titulo}</div><div class='x'>{texto}</div></div>",
                unsafe_allow_html=True)


def negritas(texto):
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", texto)


FONDO = {"verde": "#D8F1E8", "amarillo": "#FBEFC8", "rojo": "#F9D7DC", "gris": "#EEEEEE"}


# ------------------------------------------------------------------------ páginas
def pagina_resumen():
    st.markdown(f"<div class='palabras'>{negritas(motor.resumen(K, KPREV, MARCA, h, T, SEM_TXT))}</div>", unsafe_allow_html=True)
    st.write("")
    tarjetas(K, KPREV)
    st.caption("Venta en piezas por semana (promedio de las últimas 5 semanas, columna ~Prom Venta L5W). "
               "Los porcentajes de inventario son sobre las piezas en tienda (On Hand).")

    c1, c2 = st.columns([1, 1.25])
    with c1:
        st.subheader("¿Visitar la tienda sirve?")
        g = h.join(T["se_visita"], on="tienda_id")
        filas = []
        for v in ["Sí", "No"]:
            gg, tt = g[g["se_visita"] == v], T[T["se_visita"] == v]
            cv = gg[gg["venta"] > 0]
            oh = gg["on_hand"].sum()
            filas.append({"Se visita": v, "Tiendas": len(tt), "Venta (pzs/sem)": gg["venta"].sum(),
                          "Venta por tienda": gg["venta"].sum() / len(tt) if len(tt) else np.nan,
                          "Agotados %": (cv["estatus"] == "Agotado").mean() * 100 if len(cv) else np.nan,
                          "Estancado %": gg["estancado_pzs"].sum() / oh * 100 if oh else np.nan})
        vis = pd.DataFrame(filas)
        vis.insert(3, "% de la venta", vis["Venta (pzs/sem)"] / vis["Venta (pzs/sem)"].sum() * 100)
        st.dataframe(vis, hide_index=True, width="stretch", column_config={
            "Venta (pzs/sem)": st.column_config.NumberColumn(format="%,.0f"),
            "% de la venta": st.column_config.NumberColumn(format="%.0f%%"),
            "Venta por tienda": st.column_config.NumberColumn(format="%.1f"),
            "Agotados %": st.column_config.NumberColumn(format="%.1f%%"),
            "Estancado %": st.column_config.NumberColumn(format="%.1f%%")})
        if len(vis) == 2 and vis["Venta por tienda"].iloc[1]:
            x = vis["Venta por tienda"].iloc[0] / vis["Venta por tienda"].iloc[1]
            st.caption(f"Una tienda visitada vende {x:.1f} veces lo que una no visitada. Ojo: Mars visita sobre todo "
                       "tiendas grandes, así que esto no prueba por sí solo que la visita cause la venta.")
    with c2:
        st.subheader("Cómo va cada cadena")
        filas = []
        for cad, gg in h.groupby("cadena"):
            cv = gg[gg["venta"] > 0]
            oh = gg["on_hand"].sum()
            filas.append({"Cadena": cad, "Tiendas": gg["tienda_id"].nunique(), "Venta (pzs/sem)": gg["venta"].sum(),
                          "agotados": (cv["estatus"] == "Agotado").mean() * 100 if len(cv) else np.nan,
                          "excedente": gg["excedente_pzs"].sum() / oh * 100 if oh else np.nan,
                          "estancado": gg["estancado_pzs"].sum() / oh * 100 if oh else np.nan})
        cad = pd.DataFrame(filas).sort_values("Venta (pzs/sem)", ascending=False)
        est = {kp: [motor.estado(kp, v, KCFG[kp])[0] for v in cad[kp]] for kp in ["agotados", "excedente", "estancado"]}
        nombres = {"agotados": "Agotados %", "excedente": "Excedente %", "estancado": "Estancado %"}

        def pintar(col):
            kp = next((k for k, n in nombres.items() if n == col.name), None)
            return [f"background-color: {FONDO[e]}" for e in est[kp]] if kp else [""] * len(col)

        vista = cad.rename(columns=nombres)
        st.dataframe(vista.style.apply(pintar).format({"Venta (pzs/sem)": "{:,.0f}", "Agotados %": "{:.1f}%",
                                                       "Excedente %": "{:.1f}%", "Estancado %": "{:.1f}%"}),
                     hide_index=True, width="stretch")
        st.caption("Verde: en meta · Amarillo: en camino · Rojo: lejos de la meta.")

    st.subheader("Semana a semana")
    kp = st.segmented_control("Indicador", motor.ORDEN_KPIS, format_func=lambda x: KCFG[x]["nombre"],
                              default="agotados", key="kp_res", label_visibility="collapsed") or "agotados"
    serie = pd.DataFrame([{"Semana": motor.etiqueta_semana(k).split(" · ")[0], "orden": k, "valor": calc(k)[2][kp],
                           "Aviso": "⚠️ venta sospechosa" if CAL["resumen"].loc[k, "Venta sospechosa"] else ""}
                          for k in sorted(SEMANAS)])
    base = alt.Chart(serie).encode(x=alt.X("Semana:N", sort=None, title=None, axis=alt.Axis(labelAngle=0)),
                                   y=alt.Y("valor:Q", title=KCFG[kp]["nombre"], scale=alt.Scale(zero=kp == "venta")),
                                   tooltip=["Semana", alt.Tooltip("valor:Q", format=",.1f", title="Valor"), "Aviso"])
    capas = [base.mark_line(color=C["acento"], strokeWidth=3),
             base.mark_circle(size=110).encode(color=alt.condition("datum.Aviso != ''", alt.value(C["rosa"]), alt.value(C["acento"]))),
             base.mark_text(dy=-14, fontWeight="bold", color=C["oscuro"]).encode(text=alt.Text("valor:Q", format=",.1f"))]
    if kp != "venta":
        capas.append(alt.Chart(pd.DataFrame({"y": [KCFG[kp]["meta"]]})).mark_rule(strokeDash=[6, 4], color=C["verde"], strokeWidth=2)
                     .encode(y="y:Q"))
    st.altair_chart(alt.layer(*capas).properties(height=280), width="stretch")
    st.caption("Línea verde punteada: meta. Punto rosa: semana con venta sospechosa (ver Calidad del dato).")


def pagina_regiones():
    st.subheader("Mapa de calor por estado")
    st.caption("Cada columna es un indicador. Más intenso = peor dentro de ese indicador. El número es el valor real.")
    filas = []
    for edo, gg in h.join(T["estado"], on="tienda_id").groupby("estado"):
        cv = gg[gg["venta"] > 0]
        oh = gg["on_hand"].sum()
        te = T[T["estado"] == edo]
        filas.append({"Estado": edo, "Tiendas": len(te), "Agotados %": (cv["estatus"] == "Agotado").mean() * 100 if len(cv) else 0,
                      "Excedente %": gg["excedente_pzs"].sum() / oh * 100 if oh else 0,
                      "Estancado %": gg["estancado_pzs"].sum() / oh * 100 if oh else 0,
                      "Piezas estancadas": gg["estancado_pzs"].sum(),
                      "Prioritarias sin visita": int((te["prioritaria"] & (te["se_visita"] == "No")).sum())})
    E = pd.DataFrame(filas).sort_values("Tiendas", ascending=False)
    largo = E.melt(id_vars=["Estado"], var_name="Indicador", value_name="valor")
    largo["intensidad"] = largo.groupby("Indicador")["valor"].rank(pct=True)
    largo.loc[largo["Indicador"] == "Tiendas", "intensidad"] = 0.05
    largo["texto"] = np.where(largo["Indicador"].str.endswith("%"), largo["valor"].map("{:.1f}".format),
                              largo["valor"].map("{:,.0f}".format))
    orden_y = E["Estado"].tolist()
    base = alt.Chart(largo).encode(x=alt.X("Indicador:N", sort=list(E.columns[1:]), title=None,
                                           axis=alt.Axis(orient="top", labelAngle=0, labelLimit=160)),
                                   y=alt.Y("Estado:N", sort=orden_y, title=None))
    mapa = base.mark_rect(stroke="#fff", strokeWidth=2).encode(
        color=alt.Color("intensidad:Q", scale=alt.Scale(range=["#F4F6FF", C["rosa"]]), legend=None),
        tooltip=["Estado", "Indicador", alt.Tooltip("valor:Q", format=",.1f")])
    texto = base.mark_text(fontSize=11).encode(text="texto:N", color=alt.condition("datum.intensidad > 0.75", alt.value("#fff"), alt.value(C["oscuro"])))
    st.altair_chart((mapa + texto).properties(height=max(300, 24 * len(E))), width="stretch")

    st.subheader("Ranking de tiendas por urgencia")
    st.caption(f"La urgencia (0 a 100) suma: venta en riesgo por agotados (40 %), piezas estancadas (25 %), excedente (15 %) "
               f"y tamaño de la tienda (20 %). Las prioritarias son el {MARCA['pct_prioritarias']}% más urgente.")
    a, b, c = st.columns([1, 1, 1.4])
    solo_pri = a.toggle("Solo prioritarias", value=True)
    solo_nv = b.toggle("Solo las que no se visitan", value=False)
    edo = c.selectbox("Estado", ["Todos"] + sorted(T["estado"].dropna().unique()), key="edo_rank")
    R = T.copy()
    if solo_pri:
        R = R[R["prioritaria"]]
    if solo_nv:
        R = R[R["se_visita"] == "No"]
    if edo != "Todos":
        R = R[R["estado"] == edo]
    vista = pd.DataFrame({"Tienda": R["nombre_tienda"], "Cadena": R["cadena"], "Estado": R["estado"], "Ciudad": R["ciudad"],
                          "¿Se visita?": R["se_visita"], "Urgencia": R["urgencia"], "Venta (pzs/sem)": R["venta"],
                          "Agotados %": R["pct_agotados"], "Piezas estancadas": R["estancado"],
                          "Piezas de excedente": R["excedente"], "Ejecutivo": R["ejecutivo"]})
    st.write(f"**{len(R):,} tiendas** · {int((R['se_visita'] == 'No').sum()):,} no se visitan")
    st.dataframe(vista, hide_index=True, width="stretch", height=420, column_config={
        "Urgencia": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f"),
        "Venta (pzs/sem)": st.column_config.NumberColumn(format="%,.1f"),
        "Agotados %": st.column_config.NumberColumn(format="%.0f%%"),
        "Piezas estancadas": st.column_config.NumberColumn(format="%,.0f"),
        "Piezas de excedente": st.column_config.NumberColumn(format="%,.0f")})
    st.download_button("⬇️ Descargar ranking (CSV)", vista.to_csv(index=False).encode("utf-8-sig"),
                       f"ranking_tiendas_{semana}.csv", "text/csv")

    st.subheader("¿Cuántas tiendas tienen casi todo el inventario estancado?")
    curva, n80 = motor.pareto(T, "estancado")
    if n80:
        st.markdown(f"<span class='chip'><b>{n80:,} tiendas</b> juntan el 80 % de las piezas estancadas</span>"
                    f"<span class='chip'>de {len(curva):,} tiendas con el problema ({n80 / len(curva):.0%})</span>", unsafe_allow_html=True)
        linea = alt.Chart(curva).mark_area(line={"color": C["acento"], "strokeWidth": 2}, color=alt.Gradient(
            gradient="linear", stops=[alt.GradientStop(color="#fff", offset=0), alt.GradientStop(color=C["acento_claro"], offset=1)],
            x1=1, x2=1, y1=1, y2=0), opacity=.5).encode(
            x=alt.X("n:Q", title="Tiendas, de la que más tiene a la que menos"), y=alt.Y("acum:Q", title="% acumulado de piezas estancadas"),
            tooltip=[alt.Tooltip("n:Q", title="Tiendas"), alt.Tooltip("acum:Q", title="% acumulado", format=".1f")])
        r80 = alt.Chart(pd.DataFrame({"y": [80], "x": [n80]}))
        st.altair_chart((linea + r80.mark_rule(color=C["rosa"], strokeDash=[5, 4]).encode(y="y:Q")
                         + r80.mark_rule(color=C["rosa"], strokeDash=[5, 4]).encode(x="x:Q")).properties(height=260), width="stretch")
    else:
        st.info("No hay inventario estancado en esta selección.")


def pagina_alertas():
    st.subheader("Qué atender primero")
    st.caption("Se calculan solas con el Excel. Rojo: urgente · Naranja: importante · Verde: va bien.")
    for prio, titulo, texto in motor.alertas(h, T, K, MARCA, CAL, semana):
        alerta_html(prio, titulo, texto)

    st.subheader("Lista de acciones por tienda y producto")
    Lop = motor.lista_operativa(h, TI)
    a, b, c = st.columns([1.2, 1.6, 1.2])
    ejec = a.selectbox("Ejecutivo", ["Todos"] + sorted(Lop["ejecutivo"].dropna().unique(), key=lambda x: (len(x), x)), key="ejec")
    tipos = b.multiselect("Tipo", list(motor.ACCION), default=["Agotado", "Por agotarse", "Estancado"], key="tipos")
    buscar = c.text_input("Buscar tienda", placeholder="ej. SM PUEBLA", key="buscar")
    if ejec != "Todos":
        Lop = Lop[Lop["ejecutivo"] == ejec]
    if tipos:
        Lop = Lop[Lop["estatus"].isin(tipos)]
    if buscar:
        Lop = Lop[Lop["nombre_tienda"].str.contains(buscar, case=False, na=False)]
    icono = {"Agotado": "🔴", "Por agotarse": "🟠", "Estancado": "⚫", "Excedente": "🔵"}
    conteo = Lop["estatus"].value_counts()
    st.markdown("".join(f"<span class='chip'>{icono[e]} {e}: <b>{conteo.get(e, 0):,}</b></span>" for e in motor.ACCION),
                unsafe_allow_html=True)
    vista = pd.DataFrame({"": Lop["estatus"].map(icono), "Tienda": Lop["nombre_tienda"], "Cadena": Lop["cadena"],
                          "Producto": Lop["producto"], "Qué hacer": Lop["accion"], "Inventario (pzs)": Lop["on_hand"],
                          "Venta (pzs/sem)": Lop["venta"], "Días de inventario": Lop["dias"],
                          "Días con tubería": Lop["dias_tub"], "Cajas": Lop["cajas"], "Ejecutivo": Lop["ejecutivo"]})
    st.dataframe(vista.head(1000), hide_index=True, width="stretch", height=460, column_config={
        "Inventario (pzs)": st.column_config.NumberColumn(format="%,.0f"),
        "Venta (pzs/sem)": st.column_config.NumberColumn(format="%.1f"),
        "Días de inventario": st.column_config.NumberColumn(format="%,.0f"),
        "Días con tubería": st.column_config.NumberColumn(format="%,.0f"),
        "Cajas": st.column_config.NumberColumn(help="A pedir (agotado o por agotarse) o que sobran (excedente)", format="%,.0f")})
    st.caption(f"Mostrando {min(len(vista), 1000):,} de {len(vista):,} renglones, de lo más urgente a lo menos. "
               "Cajas a pedir = lo que falta para cubrir 30 días de venta, descontando lo que viene en camino.")
    st.download_button("⬇️ Descargar lista completa (CSV)", vista.to_csv(index=False).encode("utf-8-sig"),
                       f"acciones_{semana}.csv", "text/csv")

SUGERENCIAS = ["¿Cuánto vendió SM PUEBLA esta semana?", "¿Qué cadena tiene más productos agotados?",
               "Top 5 tiendas más urgentes que no se visitan", "¿Cómo va MIX MARS?",
               "¿Qué tiene que hacer el Ejecutivo 14?", "¿Cómo cambió la venta de Soriana semana a semana?"]


@st.cache_resource(show_spinner=False)
def cliente_claude(llave):
    import anthropic
    return anthropic.Anthropic(api_key=llave)


def pagina_chat():
    llave = secreto("ANTHROPIC_API_KEY")
    if not llave:
        st.info("Para usar el asistente falta la llave de Claude. En Streamlit Cloud ve a **Settings → Secrets** y agrega:\n\n"
                "`ANTHROPIC_API_KEY = \"sk-ant-...\"`")
        return
    st.caption("Pregunta en español sobre venta, inventario, agotados, tiendas, productos o semanas. Los números salen "
               "directo de la base (no los inventa la IA). Responde sobre todas las cadenas; si no dices semana, usa "
               f"la recomendada ({motor.etiqueta_semana(CAL['recomendada'])}).")
    D = agente.Datos(H, TI, CAL, MARCA, lambda k, cads: semana_calc(H, TI, ORIGEN_KEY, k, cads))
    chat = st.session_state.setdefault("chat", [])
    elegida = None
    if not chat:
        st.write("**Prueba con:**")
        cols = st.columns(3)
        for i, q in enumerate(SUGERENCIAS):
            if cols[i % 3].button(q, key=f"sug_{i}", width="stretch"):
                elegida = q
    for m in chat:
        with st.chat_message(m["role"], avatar="🧑" if m["role"] == "user" else "🍫"):
            st.markdown(m["content"])
            if m.get("herr"):
                st.caption("🔎 Consulté: " + " · ".join(m["herr"]))
    pregunta = st.chat_input("Ej. ¿cuánto vendió BA ENSENADA la semana 2?") or elegida
    if pregunta:
        with st.chat_message("user", avatar="🧑"):
            st.markdown(pregunta)
        with st.chat_message("assistant", avatar="🍫"):
            with st.spinner("Consultando la base…"):
                historial = [{"role": m["role"], "content": m["content"]} for m in chat]
                try:
                    texto, herr = agente.responder(cliente_claude(llave), D, historial, pregunta)
                except Exception as e:  # noqa: BLE001
                    msg = str(e).lower()
                    if "credit" in msg or "billing" in msg:
                        texto = "Se acabaron los créditos de la API de Claude. Recarga en platform.claude.com → Billing."
                    elif "authentication" in msg or "api key" in msg or "401" in msg:
                        texto = "La llave ANTHROPIC_API_KEY no es válida. Revísala en Settings → Secrets."
                    elif "rate" in msg or "429" in msg or "overloaded" in msg:
                        texto = "Hay muchas preguntas al mismo tiempo. Espera unos segundos y vuelve a intentar."
                    else:
                        texto = f"No pude responder ({type(e).__name__}). Intenta de nuevo."
                    herr = []
            st.markdown(texto)
            if herr:
                st.caption("🔎 Consulté: " + " · ".join(herr))
        chat += [{"role": "user", "content": pregunta}, {"role": "assistant", "content": texto, "herr": herr}]
    if chat and st.button("🗑️ Borrar conversación"):
        st.session_state["chat"] = []
        st.rerun()


def pagina_calidad():
    st.caption("Antes de decidir con los datos, hay que saber si la semana llegó completa y si los números cuadran.")
    r = CAL["resumen"].copy()
    r["Venta vs. semana anterior %"] = pd.to_numeric(r["Venta vs. semana anterior %"], errors="coerce").astype(float)
    rec = motor.etiqueta_semana(CAL["recomendada"])
    st.info(f"La app usa por defecto la semana **{rec}**: la más reciente cuya venta cuadra con la semana anterior.")
    st.dataframe(r.drop(columns=["Semana"]).set_index(r["Semana"]), width="stretch", column_config={
        "Tiendas reportadas %": st.column_config.NumberColumn(format="%.1f%%"),
        "Venta vs. semana anterior %": st.column_config.NumberColumn(format="%+.1f%%"),
        "Venta sospechosa": st.column_config.CheckboxColumn()})
    st.caption(f"Venta sospechosa: cambia más de ±{MARCA.get('cambio_venta_max', 25)} % con las mismas tiendas y productos. "
               "Como la venta es un promedio de 5 semanas, un cambio así casi siempre es un problema de carga, no de demanda.")

    st.subheader("Tiendas reportadas por cadena y semana")
    cob = CAL["cobertura"].copy()
    tiendas = CAL["tiendas"]
    cob.columns = [motor.etiqueta_semana(c).split(" · ")[0] for c in cob.columns]
    largo = cob.reset_index().melt(id_vars="cadena", var_name="Semana", value_name="pct")
    largo["n"] = tiendas.reset_index().melt(id_vars="cadena")["value"].values
    base = alt.Chart(largo).encode(x=alt.X("Semana:N", title=None, axis=alt.Axis(orient="top", labelAngle=0)),
                                   y=alt.Y("cadena:N", title=None))
    mapa = base.mark_rect(stroke="#fff", strokeWidth=2).encode(
        color=alt.Color("pct:Q", scale=alt.Scale(domain=[50, 95, 100], range=[C["rosa"], "#FFE3EC", "#E8F7F1"]), legend=None),
        tooltip=["cadena", "Semana", alt.Tooltip("n:Q", title="Tiendas"), alt.Tooltip("pct:Q", format=".1f", title="% vs. su mejor semana")])
    txt = base.mark_text(fontSize=12).encode(text=alt.Text("n:Q", format=",.0f"))
    st.altair_chart((mapa + txt).properties(height=40 * len(cob) + 30), width="stretch")
    st.caption("Número = tiendas que reportó la cadena esa semana. Rosa = faltan tiendas contra su mejor semana.")

    st.subheader("Renglones con inventario o venta negativos")
    neg = H[H["invalido"]].groupby("cadena").size().rename("Renglones").sort_values(ascending=False)
    if neg.empty:
        st.success("No hay negativos.")
    else:
        st.dataframe(neg.reset_index(), hide_index=True)
        st.caption("No existen piezas negativas: en los cálculos se cuentan como 0. Conviene preguntarle a Mars si son ajustes del sistema.")
    for a in avisos:
        st.caption("ℹ️ " + a)


def pagina_reporte():
    st.subheader("Reporte semanal en PDF")
    st.caption(f"Una página con los 7 indicadores de la semana {SEM_TXT}, la comparación contra la semana anterior, "
               "las tiendas más urgentes y las 3 alertas principales.")
    A = motor.alertas(h, T, K, MARCA, CAL, semana)
    pdf = build_pdf(MARCA, SEM_TXT, K, KPREV, T, A, motor.resumen(K, KPREV, MARCA, h, T, SEM_TXT))
    st.download_button("⬇️ Descargar reporte PDF", pdf, file_name=f"Mars_reporte_{semana}.pdf",
                       mime="application/pdf", type="primary")


# ------------------------------------------------------------------------ cuerpo
vivo = "<span class='vivo'>● EN VIVO</span>" if MODO == "drive" and subido is None else ""
st.markdown(f"<h1 style='margin-bottom:0'>{pagina.split(' ', 1)[1]}{vivo}</h1>", unsafe_allow_html=True)
cuando = f" · Excel actualizado el {actualizado:%d/%m/%Y a las %H:%M}" if actualizado is not None else ""
st.caption(f"Semana {SEM_TXT} · {', '.join(cadenas) if cadenas else 'Todas las cadenas'} · Fuente: {origen}{cuando}")
if MODO == "demo" and subido is None:
    st.info("Modo demo: no hay Excel de Drive conectado, así que se muestran datos ficticios con el formato de Mars.")
if CAL["resumen"].loc[semana, "Venta sospechosa"]:
    st.warning(f"La venta de esta semana no cuadra con la anterior. Se recomienda usar {motor.etiqueta_semana(CAL['recomendada'])}.")

{"📊 Resumen": pagina_resumen, "🗺️ Regiones y tiendas": pagina_regiones, "🚨 Alertas": pagina_alertas,
 "🤖 Pregúntale a la base": pagina_chat, "🧪 Calidad del dato": pagina_calidad, "📄 Reporte": pagina_reporte}[pagina]()


# Revisa cada minuto si el Excel de Drive cambió; si sí, recarga todo solo.
if MODO == "drive" and subido is None:
    @st.fragment(run_every=REVISAR_CADA)
    def vigilar():
        try:
            info_drive.clear()
            nuevo = info_drive(FILE_ID)["modifiedTime"]
        except Exception:  # noqa: BLE001
            return
        if nuevo != st.session_state.get("modificado"):
            st.rerun(scope="app")
        st.caption(f"Última revisión de Drive: {datetime.now():%H:%M:%S}")
    vigilar()
