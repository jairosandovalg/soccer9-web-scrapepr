import streamlit as st
import pandas as pd
import subprocess
import sys
import requests
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

# --- CONFIGURACIÓN DE TELEGRAM ---
TELEGRAM_BOT_TOKEN = "8923959866:AAES1dc4LAsedUKUsGR4p5D1SkaMt7nKyes"
TELEGRAM_CHAT_ID = "7272170952"

def enviar_alerta_telegram(mensaje: str) -> bool:
    """Envía un mensaje formateado a Telegram mediante la API HTTP."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mensaje,
        "parse_mode": "HTML"
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        st.warning(f"Error al enviar a Telegram: {e}")
        return False

def formatear_mensaje_partido(reg: dict) -> str:
    """Da formato visual al mensaje con negritas y emojis."""
    stats_texto = ""
    # Selecciona estadísticas clave para mostrar en la alerta
    stats_clave = [
        k for k in reg.keys() 
        if any(term in k.lower() for term in [
            "remate", "xg", "posesión", "córner", "toques en el área", "falta", "parada"
        ])
    ]
    if stats_clave:
        stats_texto = "\n\n📊 <b>Estadísticas clave:</b>\n" + "\n".join([f"• {k}: {reg[k]}" for k in stats_clave[:8]])

    return (
        f"⚽ <b>ALERTA EN VIVO</b>\n\n"
        f"⚔️ <b>Partido:</b> {reg['Partido en Vivo']}\n"
        f"🔢 <b>Marcador:</b> {reg['Marcador']}\n"
        f"⏱ <b>Minuto:</b> {reg['Minuto']} ({reg['Tiempo/Estado']})\n"
        f"📈 <b>Cuotas (1X2):</b> {reg['Cuotas']}"
        f"{stats_texto}"
    )

# --- CONFIGURACIÓN E INSTALACIÓN ---
@st.cache_resource
def instalar_navegadores_playwright():
    try:
        subprocess.run([sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"], check=True)
        return True
    except Exception as e:
        st.error(f"Error instalando navegadores: {e}")
        return False

instalar_navegadores_playwright()

# --- FUNCIÓN DE EXTRACCIÓN ROBUSTA ---
def extraer_estadisticas_partido(playwright_context, url_partido):
    datos_partido = {
        "Marcador": "- - -",
        "Cuotas": "- - -",
        "Tiempo/Estado": "-",
        "Minuto": "-",
        "Stats": {}
    }
    page = None
    try:
        page = playwright_context.new_page()
        page.goto(url_partido, timeout=25000, wait_until="domcontentloaded")
        page.wait_for_selector("div.detailScore__wrapper", timeout=10000)
        
        # 1. Marcador, Estado y Minuto
        soup_base = BeautifulSoup(page.content(), "html.parser")
        score = soup_base.select_one("div.detailScore__wrapper")
        if score: 
            datos_partido["Marcador"] = score.get_text(strip=True)
            
        status = soup_base.select_one("span.fixedHeaderDuel__detailStatus")
        if status: 
            datos_partido["Tiempo/Estado"] = status.get_text(strip=True)
            
        minuto = soup_base.select_one("span.eventTime")
        if minuto: 
            datos_partido["Minuto"] = minuto.get_text(strip=True)

        # 2. Navegación a pestaña Estadísticas
        tab_stats = page.locator("a[data-analytics-alias='match-statistics'], a[href*='estadisticas']").first
        if tab_stats.is_visible(timeout=3000):
            tab_stats.click(force=True)
            page.wait_for_timeout(1500)

        page.wait_for_selector("div.tabContent__match-statistics", timeout=5000)
        soup_stats = BeautifulSoup(page.content(), "html.parser")

        # 3. Cuotas en vivo (Betano 660, Betsson 43 o primera casa disponible)
        cuotas_row = (
            soup_stats.select_one("div[data-analytics-bookmaker-id='660']") or 
            soup_stats.select_one("div[data-analytics-bookmaker-id='43']") or 
            soup_stats.select_one("div.wclOddsContent")
        )
        if cuotas_row:
            valores_cuotas = [span.get_text(strip=True) for span in cuotas_row.select("span[data-testid='wcl-oddsValue']")]
            if len(valores_cuotas) >= 3:
                datos_partido["Cuotas"] = f"1:{valores_cuotas[0]} X:{valores_cuotas[1]} 2:{valores_cuotas[2]}"

        # 4. Estadísticas estándar (xG, Posesión, Remates totales, Pases, Faltas, etc.)
        for stat_div in soup_stats.find_all("div", class_=lambda c: c and "wcl-statistics_" in c):
            label_row = stat_div.find("div", class_=lambda c: c and "wcl-labelRow_" in c)
            if not label_row:
                continue

            nombre_elem = label_row.find("span", class_=lambda c: c and "wcl-name_" in c) or \
                          label_row.find("h4", class_=lambda c: c and "wcl-name_" in c)
            if not nombre_elem:
                continue
            cat_nombre = nombre_elem.get_text(strip=True)

            h_div = label_row.find("div", recursive=False)
            h_val = h_div.find("div", class_=lambda c: c and "wcl-value_" in c) if h_div else None
            
            v_div = label_row.find("div", class_=lambda c: c and "wcl-awayValue_" in c)
            v_val = v_div.find("div", class_=lambda c: c and "wcl-value_" in c) if v_div else None

            if h_val and v_val:
                val_l = h_val.contents[0].text if hasattr(h_val.contents[0], 'text') else str(h_val.contents[0])
                val_v = v_val.contents[-1].text if hasattr(v_val.contents[-1], 'text') else str(v_val.contents[-1])
                datos_partido["Stats"][f"{cat_nombre} (L)"] = val_l.strip()
                datos_partido["Stats"][f"{cat_nombre} (V)"] = val_v.strip()

        # 5. Remates a Puerta y Fuera (Estructura de portería)
        for bar in soup_stats.find_all("div", class_=lambda c: c and ("wcl-onTargetBar_" in c or "wcl-offTargetBar_" in c)):
            label = bar.find("span", class_=lambda c: c and "wcl-label_" in c)
            vals = bar.find_all("span", class_=lambda c: c and "wcl-value_" in c)
            if label and len(vals) >= 2:
                cat_nombre = label.get_text(strip=True)
                datos_partido["Stats"][f"{cat_nombre} (L)"] = vals[0].get_text(strip=True)
                datos_partido["Stats"][f"{cat_nombre} (V)"] = vals[1].get_text(strip=True)

        # 6. Incidentes (Córneres, Tarjetas)
        for badge in soup_stats.find_all("div", class_=lambda c: c and "wcl-incidentValueBadge_" in c):
            icon = badge.find("svg")
            vals = badge.find_all("span", class_=lambda c: c and "wcl-scores-simple-text-01" in c)
            if icon and len(vals) >= 2:
                tipo = "Incidente"
                test_id = icon.get("data-testid", "")
                if "corner" in test_id:
                    tipo = "Córneres"
                elif "yellow-card" in test_id:
                    tipo = "Tarjetas amarillas"
                elif "red-card" in test_id:
                    tipo = "Tarjetas rojas"
                
                datos_partido["Stats"][f"{tipo} (L)"] = vals[0].get_text(strip=True)
                datos_partido["Stats"][f"{tipo} (V)"] = vals[1].get_text(strip=True)

    except Exception as e:
        print(f"Error procesando estadísticas de {url_partido}: {e}")
    finally:
        if page:
            page.close()
    return datos_partido

# --- INTERFAZ STREAMLIT ---
st.set_page_config(page_title="Monitor de Estadísticas en Vivo", layout="wide")
st.title("📊 Monitor de Estadísticas en Vivo y Alertas Telegram")

enviar_telegram = st.sidebar.checkbox("Activar alertas a Telegram", value=True)

if st.button("🔄 Ejecutar Escaneo Completo"):
    with st.spinner("Conectando y analizando partidos en directo..."):
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu"
                ]
            )
            context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
            main = context.new_page()
            main.goto("https://www.flashscore.pe/", timeout=30000)
            
            btn_live = "//div[contains(@class, 'filters__text') and text()='EN DIRECTO']"
            main.wait_for_selector(btn_live, timeout=15000)
            main.locator(btn_live).click()
            main.wait_for_timeout(3000)
            
            soup = BeautifulSoup(main.content(), "html.parser")
            partidos = soup.find_all("div", id=lambda x: x and x.startswith("g_1_"))
            
            if partidos:
                res = []
                bar = st.progress(0)
                limite_partidos = partidos[:10]
                
                for i, p_div in enumerate(limite_partidos):
                    id_p = p_div.get('id').split('_')[-1]
                    
                    h_team = p_div.find("div", class_=lambda c: c and "home" in c.lower() and "participant" in c.lower())
                    a_team = p_div.find("div", class_=lambda c: c and "away" in c.lower() and "participant" in c.lower())
                    nombre_partido = f"{h_team.get_text(strip=True) if h_team else 'Local'} vs {a_team.get_text(strip=True) if a_team else 'Visitante'}"
                    
                    url = f"https://www.flashscore.pe/partido/{id_p}/#/resumen/estadisticas"
                    data = extraer_estadisticas_partido(context, url)
                    
                    stats_dict = data.pop("Stats", {})
                    reg = {
                        "Partido en Vivo": nombre_partido, 
                        "Marcador": data["Marcador"],
                        "Cuotas": data["Cuotas"],
                        "Tiempo/Estado": data["Tiempo/Estado"],
                        "Minuto": data["Minuto"]
                    }
                    reg.update(stats_dict)
                    res.append(reg)

                    if enviar_telegram:
                        mensaje = formatear_mensaje_partido(reg)
                        enviar_alerta_telegram(mensaje)

                    bar.progress((i + 1) / len(limite_partidos))
                
                st.dataframe(pd.DataFrame(res).fillna("-"), use_container_width=True)
                st.success("Escaneo finalizado y alertas enviadas correctamente.")
            else:
                st.warning("No hay partidos en directo disponibles en este momento.")
            
            browser.close()
