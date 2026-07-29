import time
import requests
import ccxt
import pandas as pd
import ta

# ==========================================
# 1. CONFIGURACIÓN DE TELEGRAM
# ==========================================
TELEGRAM_TOKEN = "8810680096:AAGPSrNFFWpbUHuj0laurGLxuepKIZDexys"
CHAT_ID = "1473411725"

def enviar_telegram(mensaje):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {
            "chat_id": CHAT_ID,
            "text": mensaje,
            "parse_mode": "Markdown"
        }
        requests.post(url, data=data)
    except Exception as e:
        print(f"Error enviando mensaje a Telegram: {e}")

# ==========================================
# 2. INICIALIZAR EXCHANGE (BingX)
# ==========================================
exchange = ccxt.bingx({
    'enableRateLimit': True,
})

# ==========================================
# 3. DETERMINACIÓN DE TENDENCIA POR TEMPORALIDAD
# ==========================================
def obtener_tendencia(symbol, timeframe):
    """Devuelve True (Alcista 🟢) o False (Bajista 🔴) basado en la EMA 50"""
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=100)
        if not ohlcv or len(ohlcv) < 30:
            return None
        
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['ema50'] = ta.trend.ema_indicator(df['close'], window=min(50, len(df)-1))
        
        precio_actual = df['close'].iloc[-1]
        ema_actual = df['ema50'].iloc[-1]
        
        return precio_actual > ema_actual
    except Exception:
        return None

# ==========================================
# 4. RASTREO Y CLASIFICACIÓN DE PARES
# ==========================================
def analizar_mercado():
    print("🔎 Rastreando todo el mercado de criptomonedas (USDT)...")
    
    try:
        exchange.load_markets()
        todos_los_pares = [symbol for symbol in exchange.symbols if symbol.endswith('/USDT')]
        
        print(f"🚀 Escaneando {len(todos_los_pares)} pares en 4 temporalidades...")
        
        longs_perfectos = []       # 1H, 4H, 1D, 1S (Todos Verde)
        longs_diario_semanal = []  # 1D y 1S (Verde)
        shorts_perfectos = []      # 1H, 4H, 1D, 1S (Todos Rojo)
        shorts_diario_semanal = [] # 1D y 1S (Rojo)
        
        temporalidades = ['1h', '4h', '1d', '1w']

        for par in todos_los_pares:
            estados = {}
            es_valido = True
            
            for tf in temporalidades:
                tendencia = obtener_tendencia(par, tf)
                if tendencia is None:
                    es_valido = False
                    break
                
                estados[tf] = "🟢" if tendencia else "🔴"
            
            if es_valido:
                simbolo_limpio = par.split('/')[0]
                datos_par = {
                    'symbol': simbolo_limpio,
                    '1h': estados['1h'], '4h': estados['4h'], 
                    '1d': estados['1d'], '1w': estados['1w']
                }
                
                # 1. PERFECCIÓN ALCISTA (4 VERDES)
                if all(val == "🟢" for val in estados.values()):
                    longs_perfectos.append(datos_par)
                
                # 2. DIARIO Y SEMANAL ALCISTAS (1D 🟢 + 1S 🟢)
                elif estados['1d'] == "🟢" and estados['1w'] == "🟢":
                    longs_diario_semanal.append(datos_par)

                # 3. PERFECCIÓN BAJISTA (4 ROJOS)
                if all(val == "🔴" for val in estados.values()):
                    shorts_perfectos.append(datos_par)
                
                # 4. DIARIO Y SEMANAL BAJISTAS (1D 🔴 + 1S 🔴)
                elif estados['1d'] == "🔴" and estados['1w'] == "🔴":
                    shorts_diario_semanal.append(datos_par)

            time.sleep(0.05)

        # Función auxiliar para enviar listas a Telegram
        def enviar_lista_telegram(titulo, descripcion, lista):
            if not lista:
                return
            mensaje = f"{titulo}\n_{descripcion}_\n\n"
            for i, res in enumerate(lista[:20], 1):
                mensaje += f"*{i}. {res['symbol']}*\n"
                mensaje += f"1H {res['1h']} | 4H {res['4h']} | 1D {res['1d']} | 1S {res['1w']}\n\n"
            enviar_telegram(mensaje)
            time.sleep(1)

        # --- ENVIAR LOS 4 REPORTES A TELEGRAM ---
        enviar_lista_telegram(
            "🟢 *TOP 20 PERFECCIÓN ALCISTA*",
            "Criptos con 1H, 4H, 1D y 1S en Verde",
            longs_perfectos
        )

        enviar_lista_telegram(
            "📈 *TOP 20 TENDENCIA ALCISTA (1D + 1S)*",
            "Criptos con Gráfico Diario y Semanal en Verde",
            longs_diario_semanal
        )

        enviar_lista_telegram(
            "🔴 *TOP 20 PERFECCIÓN BAJISTA*",
            "Criptos con 1H, 4H, 1D y 1S en Rojo",
            shorts_perfectos
        )

        enviar_lista_telegram(
            "📉 *TOP 20 TENDENCIA BAJISTA (1D + 1S)*",
            "Criptos con Gráfico Diario y Semanal en Rojo",
            shorts_diario_semanal
        )

    except Exception as e:
        print(f"Error en el escaneo general: {e}")

# ==========================================
# 5. BUCLE DE EJECUCIÓN 24/7
# ==========================================
if __name__ == "__main__":
    enviar_telegram("🤖 *Bot iniciado: Rastreando MarketCap (4 Filtros de Tendencia)*")
    
    while True:
        try:
            analizar_mercado()
        except Exception as e:
            print(f"Error en el ciclo principal: {e}")
            
        print("Escaneo completado. Esperando 15 minutos para el próximo ciclo...")
        time.sleep(900)
