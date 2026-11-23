"""Datos ficticios con el MISMO formato que el Excel de Mars (mismas columnas y mismos nombres).

Se usan solo cuando la app no está conectada a Google Drive (modo demo), para que el tablero
nunca se caiga en una presentación. Los números son inventados, pero siguen la misma "historia"
que la base real: muchas tiendas con inventario de más, algunas con producto sin venta,
algunos agotados, y la cadena más grande con tiendas que faltan en las últimas semanas.
"""
import numpy as np
import pandas as pd

CADENAS = {"WALMART": 260, "Soriana": 70, "Chedraui": 45, "SAMS": 30, "HEB": 18, "COSTCO": 8, "City Club": 6}
PREFIJO = {"WALMART": ["BA", "SC", "WE"], "Soriana": ["SOR"], "Chedraui": ["CHE"], "SAMS": ["SM"], "HEB": ["HEB"],
           "COSTCO": ["COS"], "City Club": ["CC"]}
ESTADOS = {"Ciudad De Mexico": ["Coyoacan", "Tlalpan", "Iztapalapa"], "Estado De Mexico": ["Toluca", "Ecatepec", "Naucalpan"],
           "Nuevo Leon": ["Monterrey", "Apodaca", "Guadalupe"], "Jalisco": ["Guadalajara", "Zapopan"],
           "Puebla": ["Puebla"], "Veracruz": ["Veracruz", "Xalapa"], "Guanajuato": ["Leon", "Irapuato"],
           "Yucatan": ["Merida"], "Baja California": ["Tijuana", "Mexicali"], "Coahuila": ["Saltillo", "Torreon"]}
REGIONAL = {"Ciudad De Mexico": "AN1", "Estado De Mexico": "AN1", "Puebla": "AN2", "Veracruz": "AN2", "Yucatan": "AN3",
            "Nuevo Leon": "AN5", "Coahuila": "AN5", "Jalisco": "AN4-AN6", "Guanajuato": "AN4-AN6", "Baja California": "AN4-AN6"}
PRODUCTOS = [  # descripción, categoría, piezas por caja, venta base por semana
    ("MIX MARS", "Choco Mars", 6, 4.0), ("M&Ms Milk Mega Bag", "Choco Mars", 8, 2.5), ("Bolsa Peanut", "Choco Mars", 12, 1.6),
    ("Snickers Core", "Choco Mars", 12, 2.2), ("Milky Way 8 Pack", "Choco Mars", 6, 0.6), ("CONEJITO TURIN", "Choco Turin", 6, 3.0),
    ("CHOCOLATE BAILEYS", "Choco Turin", 10, 1.8), ("Tubo Baileys", "Choco Turin", 6, 1.2), ("Tubo Jose Cuervo", "Choco Turin", 6, 0.7),
    ("Carajillo Tubo", "Choco Turin", 6, 0.5), ("Lucas Lenwas Sandia 140g", "Spicy", 8, 0.9), ("Lucas Lenwas Mango 140g", "Spicy", 8, 0.8),
    ("Skittles Original", "Confections", 12, 0.7), ("Starburst", "Confections", 12, 0.4), ("Orbit Menta", "Gum & mints", 12, 0.2),
]


def generar(semilla: int = 11) -> pd.DataFrame:
    """Devuelve un DataFrame con las columnas del Excel de Mars (periodo 11 de 2026, semanas 1 a 4)."""
    rng = np.random.default_rng(semilla)
    filas = []
    n = 0
    for cadena, cuantas in CADENAS.items():
        for i in range(cuantas):
            n += 1
            edo = rng.choice(list(ESTADOS))
            ciudad = rng.choice(ESTADOS[edo])
            visita = rng.random() < (0.35 if cadena == "WALMART" else 0.75) and cadena not in ("COSTCO", "City Club")
            nombre = f"{rng.choice(PREFIJO[cadena])} {ciudad.upper()} {i + 1}"
            tam = rng.lognormal(0, 0.7) * (3 if cadena in ("SAMS", "COSTCO") else 1) * (1.6 if visita else 1)
            fantasma = rng.random() < 0.12             # tiendas con producto que "no se vende"
            surtido = rng.choice(len(PRODUCTOS), size=rng.integers(6, len(PRODUCTOS) + 1), replace=False)
            ejecutivo = f"Ejecutivo {rng.integers(1, 30)}" if visita else "TBC"
            sale_en_s2 = cadena == "WALMART" and not visita and rng.random() < 0.45   # tiendas que dejan de reportar
            for p in surtido:
                desc, cat, caja, vbase = PRODUCTOS[p]
                venta = max(0.0, vbase * tam * rng.lognormal(0, 0.5))
                if fantasma and rng.random() < 0.6:
                    venta = 0.0
                elif rng.random() < 0.15:
                    venta = round(rng.uniform(0.05, 0.4), 1)
                dias_obj = rng.choice([20, 60, 120, 250], p=[0.25, 0.3, 0.3, 0.15])
                oh = np.round(venta / 7 * dias_obj + rng.integers(0, caja * 2)) if venta else rng.integers(caja, caja * 4)
                for sem in (1, 2, 3, 4):
                    if sale_en_s2 and sem >= 2:
                        continue
                    v = venta * (1 + rng.normal(0, 0.05)) * (0.6 if sem == 4 else 1)   # semana 4 con venta "rara"
                    o = max(0, oh - rng.integers(0, 2) * (sem - 1))
                    if v > 0 and rng.random() < 0.04:
                        o = 0
                    tr = int(caja * rng.integers(1, 4)) if (v > 0 and o / max(v / 7, 0.01) < 10 and rng.random() < 0.5) else 0
                    filas.append({
                        "Client": cadena, "ESTADO": edo, "CIUDAD": ciudad.upper(), "Ruteo.¿SE VISITA?": "Si" if visita else "NO",
                        "Ruteo.REGIONAL": REGIONAL[edo] if visita else "TBC",
                        "Ruteo.EJECUTIVO": ejecutivo,
                        "NOMBRE_AFS / NOMBRE DE TIENDA": nombre, "CLIENTE_AFS": f"{cadena}_{1000 + n}",
                        "Departamento": "Gondola", "Categoría": cat, "Item Nbr": 100000 + p, "Descripcion Unificada": desc,
                        "Case Count QTY": caja, "Dias de Inventario": np.nan, "Dias de Inventario con Tuberia": np.nan,
                        "Inventario On Hand": float(o), "Inventario en Transito": tr, "Inventario en Orden": 0,
                        "~Tuberia(OH+Orden+Transito)": float(o + tr), "~Prom Venta L5W": round(float(v), 1),
                        "Periodo Mars": 11, "Semana Mars": sem, "Año": 2026})
    return pd.DataFrame(filas)
