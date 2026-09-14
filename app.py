import streamlit as st
import pandas as pd
import subprocess
import sys
import requests
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

# --- CONFIGURACIÓN DE TELEGRAM ---
# Guarda estos valores en st.secrets o en variables de entorno para producción
TELEGRAM_BOT_TOKEN = "8923959866:AAES1dc4LAsedUKUsGR4p5D1SkaMt7nKyes"
TELEGRAM_CHAT_ID = "7272170952"

def enviar_alerta_telegram(mensaje: str):
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
    # Filtrar solo estadísticas principales para no saturar el chat
    stats_clave = [k for k in reg.keys() if any(term in k.lower() for term in ["tiro", "córner", "posesión", "ataque"])]
    if stats_clave:
        stats_texto = "\n\n📊 <b>Estadísticas clave:</b>\n" + "\n".join([f"• {k}: {reg[k]}" for k in stats_clave[:6]])

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
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
        return True
    except:
        return False

instalar_navegadores_playwright()

# --- FUNCIÓN DE EXTRACCIÓN ---
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
        
        try:
            page.wait_for_selector("button[data-analytics-bookmaker-id='660']", timeout=5000)
            page.wait_for_timeout(1500)
        except: pass 

        soup = BeautifulSoup(page.content(), "html.parser")
        
        score = soup.select_one("div.detailScore__wrapper")
        if score: datos_partido["Marcador"] = score.get_text(strip=True)
        
        status = soup.select_one("span.fixedHeaderDuel__detailStatus")
        if status: datos_partido["Tiempo/Estado"] = status.get_text(strip=True)
        
        minuto = soup.select_one("span.eventTime")
        if minuto: datos_partido["Minuto"] = minuto.get_text(strip=True)

        botones = soup.find_all("button", {"data-analytics-bookmaker-id": "660"})
        valores = []
        for btn in botones:
            span = btn.find("span", {"data-testid": "wcl-oddsValue"})
            if span: valores.append(span.get_text(strip=True))
        
        if len(valores) >= 3:
            datos_partido["Cuotas"] = f"1:{valores[0]} X:{valores[1]} 2:{valores[2]}"

        selector_boton = "//button[@role='tab' and contains(., 'Stats')]"
        if page.locator(selector_boton).count() > 0:
            page.locator(selector_boton).first.click(force=True)
            page.wait_for_timeout(1000)
            soup_s = BeautifulSoup(page.content(), "html.parser")
            for fila in soup_s.find_all("div", {"data-testid": "wcl-statistics"}):
                cat = fila.find("div", {"data-testid": "wcl-statistics-category"})
                if cat:
                    h = fila.find("div", class_=lambda x: x and 'wcl-homeValue' in x)
                    v = fila.find("div", class_=lambda x: x and 'wcl-awayValue' in x)
                    datos_partido["Stats"][f"{cat.get_text(strip=True)} (L)"] = h.get_text(strip=True) if h else "0"
                    datos_partido["Stats"][f"{cat.get_text(strip=True)} (V)"] = v.get_text(strip=True) if v else "0"
    except: pass
    finally:
        if page: page.close()
    return datos_partido

# --- INTERFAZ ---
st.set_page_config(page_title="Bot de Estadísticas", layout="wide")
st.title("📊 Monitor de Estadísticas en Vivo y Alertas Telegram")

# Checkbox en la barra lateral para activar o desactivar envíos
enviar_telegram = st.sidebar.checkbox("Activar alertas a Telegram", value=True)

if st.button("🔄 Ejecutar Escaneo Completo"):
    with st.spinner("Conectando y analizando partidos..."):
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
            main = context.new_page()
            main.goto("https://www.flashscore.pe/")
            
            btn_live = "//div[contains(@class, 'filters__text') and text()='EN DIRECTO']"
            main.wait_for_selector(btn_live)
            main.locator(btn_live).click()
            main.wait_for_timeout(3000)
            
            soup = BeautifulSoup(main.content(), "html.parser")
            partidos = soup.find_all("div", id=lambda x: x and x.startswith("g_1_"))
            
            if partidos:
                res = []
                bar = st.progress(0)
                for i, p_div in enumerate(partidos[:10]):
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

                    # Enviar alerta individual si está activado
                    if enviar_telegram and TELEGRAM_BOT_TOKEN != "TU_BOT_TOKEN_AQUI":
                        # OPCIONAL: Aquí puedes agregar una condición 'if' específica, 
                        # ej: enviar solo si el minuto está entre 70-80 y van empatando.
                        mensaje = formatear_mensaje_partido(reg)
                        enviar_alerta_telegram(mensaje)

                    bar.progress((i + 1) / len(partidos[:10]))
                
                st.dataframe(pd.DataFrame(res).fillna("-"), use_container_width=True)
                st.success("Escaneo finalizado y alertas enviadas correctamente.")
            browser.close()
