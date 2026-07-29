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
        requests.post(url, data=data, timeout=5)
    except Exception as e:
        print(f"Error enviando mensaje a Telegram: {e}")

# ==========================================
# 2. INICIALIZAR EXCHANGE CON TIMEOUT
# ==========================================
exchange = ccxt.bingx({
    'enableRateLimit': True,
    'timeout': 5000,  # 5 segundos máximo por petición para evitar cuelgues
})

# ==========================================
# 3. DETERMINACIÓN DE TENDENCIA ROBUSTA
# ==========================================
def obtener_tendencia(symbol, timeframe):
    """Devuelve True (Alcista 🟢) o False (Bajista 🔴) basado en la EMA rápida"""
    try:
        # Para semanal pedimos pocas velas para evitar cuelgues de historial
        limite_velas = 15 if timeframe == '1w' else 60
        
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limite_velas)
        if not ohlcv or len(ohlcv) < 5:
            return None
        
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        # Calculamos EMA según los datos disponibles
        periodo_ema = min(20, len(df)-1)
        df['ema'] = ta.trend.ema_indicator(df['close'], window=periodo_ema)
        
        precio_actual = df['close'].iloc[-1]
        ema_actual = df['ema'].iloc[-1]
        
        return precio_actual > ema_actual
    except Exception:
        return None

# ==========================================
# 4. RASTREO Y CLASIFICACIÓN DE PARES
# ==========================================
def analizar_mercado():
    print("🔎 Rastreando mercado de criptomonedas...")
    
    try:
        exchange.load_markets()
        tickers = exchange.fetch_tickers()
        
        pares_usdt = []
        for symbol, ticker in tickers.items():
            if symbol.endswith('/USDT') and ticker.get('quoteVolume') is not None:
                pares_usdt.append({
                    'symbol': symbol,
                    'volume': ticker['quoteVolume']
                })
        
        # Filtrar las 150 monedas con mayor volumen para un escaneo fluido
        pares_usdt = sorted(pares_usdt, key=lambda x: x['volume'], reverse=True)
        pares_filtrados = [item['symbol'] for item in pares_usdt[:150]]
        
        print(f"🚀 Escaneando {len(pares_filtrados)} pares principales...")
        
        longs_perfectos = []
        longs_diario_semanal = []
        shorts_perfectos = []
        shorts_diario_semanal = []
        
        temporalidades = ['1h', '4h', '1d', '1w']

        for count, par in enumerate(pares_filtrados, 1):
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

            # Imprimir progreso en los logs cada 30 pares
            if count % 30 == 0:
                print(f"⏳ Progreso: {count}/{len(pares_filtrados)} pares analizados...")

        def enviar_lista_telegram(titulo, descripcion, lista):
            if not lista:
                return
            mensaje = f"{titulo}\n_{descripcion}_\n\n"
            for i, res in enumerate(lista[:20], 1):
                mensaje += f"*{i}. {res['symbol']}*\n"
                mensaje += f"1H {res['1h']} | 4H {res['4h']} | 1D {res['1d']} | 1S {res['1w']}\n\n"
            enviar_telegram(mensaje)
            time.sleep(1)

        # Enviar reportes a Telegram
        enviar_lista_telegram("🟢 *TOP 20 PERFECCIÓN ALCISTA*", "Criptos con 1H, 4H, 1D y 1S en Verde", longs_perfectos)
        enviar_lista_telegram("📈 *TOP 20 TENDENCIA ALCISTA (1D + 1S)*", "Criptos con Gráfico Diario y Semanal en Verde", longs_diario_semanal)
        enviar_lista_telegram("🔴 *TOP 20 PERFECCIÓN BAJISTA*", "Criptos con 1H, 4H, 1D y 1S en Rojo", shorts_perfectos)
        enviar_lista_telegram("📉 *TOP 20 TENDENCIA BAJISTA (1D + 1S)*", "Criptos con Gráfico Diario y Semanal en Rojo", shorts_diario_semanal)

        print("✅ Escaneo y reporte completados exitosamente.")

    except Exception as e:
        print(f"Error en el escaneo general: {e}")

# ==========================================
# 5. BUCLE DE EJECUCIÓN 24/7
# ==========================================
if __name__ == "__main__":
    enviar_telegram("🤖 *Bot Optimizado Iniciado en Railway*")
    
    while True:
        try:
            analizar_mercado()
        except Exception as e:
            print(f"Error en el ciclo principal: {e}")
            
        print("Esperando 15 minutos para el próximo ciclo...")
        time.sleep(900)
