import time
import requests
import ccxt
import pandas as pd
import ta
import os
os.environ['HTTP_PROXY'] = 'http://proxy.server:3128'
os.environ['HTTPS_PROXY'] = 'http://proxy.server:3128'

# ==========================================
# ⚙️ CONFIGURACIÓN DE TELEGRAM
# ==========================================
TELEGRAM_TOKEN = "8810680096:AAGPSrNFFWpbUHuj0laurGLxuepKIZDexys"
TELEGRAM_CHAT_ID = "1473411725"

def enviar_telegram(mensaje):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": mensaje, "parse_mode": "Markdown"}
    try:
        requests.post(url, data=data)
    except Exception as e:
        print(f"Error enviando mensaje a Telegram: {e}")

# ==========================================
# 📊 CONFIGURACIÓN DE BINANCE Y ESTRATEGIA
# ==========================================
exchange = ccxt.binance({
    'enableRateLimit': True,
    'options': {
        'defaultType': 'spot',
    }
})

def obtener_datos(symbol, timeframe, limit=100):
    # Indicadores con la librería 'ta'
        df['ema50'] = ta.trend.ema_indicator(df['close'], window=50)
        df['ema200'] = ta.trend.ema_indicator(df['close'], window=200)
        df['rsi'] = ta.momentum.rsi(df['close'], window=14)
        df['adx'] = ta.trend.adx(df['close'], df['high'], df['low'], window=14)
        df['macd_hist'] = ta.trend.macd_diff(df['close'])
        df['atr'] = ta.volatility.average_true_range(df['high'], df['low'], df['close'], window=14)

def analizar_mercado():
    print("🔎 Iniciando escaneo completo...")
    
    try:
        markets = exchange.fetch_tickers()
        usdt_pairs = [symbol for symbol in markets.keys() if symbol.endswith('/USDT') and 'UP/' not in symbol and 'DOWN/' not in symbol]
    except Exception as e:
        print(f"Error obteniendo pares: {e}")
        return

    resultados_alcistas = []
    
    for symbol in usdt_pairs[:50]:  # Analiza los principales 50 pares
        try:
            df_15m = obtener_datos(symbol, '15m')
            df_1h = obtener_datos(symbol, '1h')
            df_4h = obtener_datos(symbol, '4h')
            
            if df_15m is None or df_1h is None or df_4h is None:
                continue
                
            p_15m = df_15m.iloc[-1]
            p_1h = df_1h.iloc[-1]
            p_4h = df_4h.iloc[-1]
            
            # Filtro Trend Following Alcista
            cond_4h = p_4h['close'] > p_4h['ema200']
            cond_1h = p_1h['close'] > p_1h['ema50']
            cond_15m = (p_15m['close'] > p_15m['open']) and (p_15m['rsi'] > 50) and (p_15m['adx'] > 20) and (p_15m['macd_hist'] > 0)
            
            if cond_4h and cond_1h and cond_15m:
                precio = p_15m['close']
                atr = p_15m['atr']
                tp = precio + (1.5 * atr)
                sl = precio - (1.0 * atr)
                
                ticker_limpio = symbol.replace('/USDT', '')
                resultados_alcistas.append({
                    'symbol': ticker_limpio,
                    'precio': precio,
                    'tp': tp,
                    'sl': sl,
                    'rsi': p_15m['rsi'],
                    'adx': p_15m['adx']
                })
        except Exception as e:
            continue

    # Construir reporte
    if resultados_alcistas:
        msj = "🚀 **ALERTAS ALCISTAS ENCONTRADAS** 🚀\n\n"
        for res in resultados_alcistas:
            msj += f"🔹 **#{res['symbol']}**\n"
            msj += f"  • Precio: `{res['precio']:.4f}`\n"
            msj += f"  • Target (TP): `{res['tp']:.4f}`\n"
            msj += f"  • Stop (SL): `{res['sl']:.4f}`\n"
            msj += f"  • RSI: `{res['rsi']:.1f}` | ADX: `{res['adx']:.1f}`\n\n"
        enviar_telegram(msj)
    else:
        print("No se encontraron oportunidades en este escaneo.")

# ==========================================
# 🔄 BUCLE DE EJECUCIÓN (CADA 15 MINUTOS)
# ==========================================
if __name__ == "__main__":
    enviar_telegram("🤖 *Bot iniciado exitosamente en PythonAnywhere*")
    while True:
        analizar_mercado()
        time.sleep(900)  # Espera 15 minutos (900 segundos)
