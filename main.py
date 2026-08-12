import time
import requests
import ccxt
import pandas as pd
import ta
import threading
import os
import sys
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

# ==========================================
# CONFIGURACIÓN DE LOGS
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

# ==========================================
# 0. FUNCIÓN AUXILIAR PARA FORMATO DE PRECIOS DINÁMICOS
# ==========================================
def fmt_precio(val):
    if val is None:
        return "0"
    if abs(val) < 0.01:
        return f"{val:.8f}".rstrip('0').rstrip('.')
    else:
        return f"{val:.4f}".rstrip('0').rstrip('.')

# ==========================================
# 1. CONFIGURACIÓN DE TELEGRAM Y FILTRO MULTI-MENSAJE
# ==========================================
TELEGRAM_TOKEN = "8810680096:AAGPSrNFFWpbUHuj0laurGLxuepKIZDexys"
CHAT_ID = "1473411725"

historial_mensajes_enviados = []
tiempo_ultimo_envio = 0
lock_telegram = threading.Lock()

def enviar_telegram(mensaje):
    global historial_mensajes_enviados, tiempo_ultimo_envio
    if not mensaje or not mensaje.strip():
        return
    
    with lock_telegram:
        tiempo_actual = time.time()
        
        if mensaje in historial_mensajes_enviados:
            return
            
        if (tiempo_actual - tiempo_ultimo_envio) < 3.0:
            time.sleep(3.0)

        if len(mensaje) > 3500:
            mensaje = mensaje[:3500] + "\n\n⚠️ _(Mensaje recortado por tamaño)_"

        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            data = {
                "chat_id": CHAT_ID,
                "text": mensaje,
                "parse_mode": "Markdown"
            }
            res = requests.post(url, data=data, timeout=10)
            
            if res.status_code == 429:
                retry_after = int(res.json().get('parameters', {}).get('retry_after', 5))
                logging.warning(f"Telegram FloodWait. Pausando por {retry_after} segundos.")
                time.sleep(retry_after)
                requests.post(url, data=data, timeout=10)

            if res.status_code == 200:
                tiempo_ultimo_envio = time.time()
                historial_mensajes_enviados.append(mensaje)
                if len(historial_mensajes_enviados) > 15:
                    historial_mensajes_enviados.pop(0)
        except Exception as e:
            logging.error(f"Error enviando mensaje a Telegram: {e}")

# ==========================================
# 2. INICIALIZAR EXCHANGE
# ==========================================
exchange = ccxt.bingx({
    'enableRateLimit': True,
    'timeout': 5000,
})

# ==========================================
# 3. SOPORTES Y RESISTENCIAS
# ==========================================
def calcular_soportes_resistencias(df, precio_actual):
    pivots_high, pivots_low = [], []
    n = len(df)
    
    if n < 5:
        return precio_actual * 0.95, precio_actual * 1.05
    
    for i in range(2, n - 2):
        if df['high'].iloc[i] > df['high'].iloc[i-1] and df['high'].iloc[i] > df['high'].iloc[i-2] and \
           df['high'].iloc[i] > df['high'].iloc[i+1] and df['high'].iloc[i] > df['high'].iloc[i+2]:
            pivots_high.append(df['high'].iloc[i])
            
        if df['low'].iloc[i] < df['low'].iloc[i-1] and df['low'].iloc[i] < df['low'].iloc[i-2] and \
           df['low'].iloc[i] < df['low'].iloc[i+1] and df['low'].iloc[i] < df['low'].iloc[i+2]:
            pivots_low.append(df['low'].iloc[i])
            
    por_encima = [p for p in pivots_high if p > precio_actual]
    resistencia = min(por_encima) if por_encima else df['high'].tail(min(30, n)).max()
    
    por_debajo = [p for p in pivots_low if p < precio_actual]
    soporte = max(por_debajo) if por_debajo else df['low'].tail(min(30, n)).min()
    
    return soporte, resistencia

# ==========================================
# 4. MOTOR DE ANÁLISIS MULTI-TEMPORAL
# ==========================================
def analizar_par_completo(symbol, timeframe):
    try:
        limit_velas = 60 if timeframe == '1w' else 80
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit_velas)
        
        if not ohlcv or len(ohlcv) < 20:
            return None
        
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        n_velas = len(df)
        precio = df['close'].iloc[-1]

        df['ema10'] = ta.trend.ema_indicator(df['close'], window=10)
        df['ema20'] = ta.trend.ema_indicator(df['close'], window=20)
        df['ema55'] = ta.trend.ema_indicator(df['close'], window=55)
        
        df['rsi'] = ta.momentum.rsi(df['close'], window=14)
        df['mfi'] = ta.volume.money_flow_index(df['high'], df['low'], df['close'], df['volume'], window=14)
        
        indicator_bb = ta.volatility.BollingerBands(close=df['close'], window=20, window_dev=2)
        df['bb_upper'] = indicator_bb.bollinger_hband()
        df['bb_lower'] = indicator_bb.bollinger_lband()
        
        adx_ind = ta.trend.ADXIndicator(df['high'], df['low'], df['close'], window=14)
        df['adx'] = adx_ind.adx()
        df['plus_di'] = adx_ind.adx_pos()
        df['minus_di'] = adx_ind.adx_neg()
        
        df['atr'] = ta.volatility.average_true_range(df['high'], df['low'], df['close'], window=14)
        multiplicador = 2.0
        hl2 = (df['high'] + df['low']) / 2
        df['up_basic'] = hl2 - (multiplicador * df['atr'])
        df['dn_basic'] = hl2 + (multiplicador * df['atr'])
        
        df['supertrend_direction'] = 1
        for i in range(1, n_velas):
            if df['close'].iloc[i] > df['dn_basic'].iloc[i-1]:
                df.loc[df.index[i], 'supertrend_direction'] = 1
            elif df['close'].iloc[i] < df['up_basic'].iloc[i-1]:
                df.loc[df.index[i], 'supertrend_direction'] = -1
            else:
                df.loc[df.index[i], 'supertrend_direction'] = df['supertrend_direction'].iloc[i-1]

        low_min = df['low'].rolling(window=14).min()
        high_max = df['high'].rolling(window=14).max()
        df['stoch_k'] = ((df['close'] - low_min) / (high_max - low_min)) * 100
        df['stoch_d'] = df['stoch_k'].rolling(window=3).mean()

        e10, e20, e55 = df['ema10'].iloc[-1], df['ema20'].iloc[-1], df['ema55'].iloc[-1]
        st_dir = df['supertrend_direction'].iloc[-1]
        st_dir_prev = df['supertrend_direction'].iloc[-2]
        
        stoch_k = df['stoch_k'].iloc[-1]
        stoch_d = df['stoch_d'].iloc[-1]
        stoch_k_prev = df['stoch_k'].iloc[-2]
        stoch_d_prev = df['stoch_d'].iloc[-2]
        
        adx = df['adx'].iloc[-1]
        plus_di = df['plus_di'].iloc[-1]
        minus_di = df['minus_di'].iloc[-1]
        rsi = df['rsi'].iloc[-1]
        mfi = df['mfi'].iloc[-1]
        atr = df['atr'].iloc[-1] if not df['atr'].empty else (precio * 0.02)
        
        supertrend_buy = (st_dir_prev == -1) and (st_dir == 1)
        supertrend_sell = (st_dir_prev == 1) and (st_dir == -1)
        
        cruce_alcista_estocastico = (stoch_k_prev <= stoch_d_prev) and (stoch_k > stoch_d)
        cruce_bajista_estocastico = (stoch_k_prev >= stoch_d_prev) and (stoch_k < stoch_d)

        soporte_key, resistencia_key = calcular_soportes_resistencias(df, precio)

        es_alcista_flexible = (precio > e55) and (st_dir == 1)
        es_bajista_flexible = (precio < e55) and (st_dir == -1)

        adx_direccion = "ALCISTA 🟢" if plus_di > minus_di else "BAJISTA 🔴"
        adx_fuerza = "Fuerte 💪" if adx >= 26 else "Débil / Rango 😴"

        return {
            'precio': precio,
            'atr': atr,
            'rsi': rsi,
            'mfi': mfi,
            'adx': adx,
            'adx_direccion': adx_direccion,
            'adx_fuerza': adx_fuerza,
            'es_alcista': es_alcista_flexible,
            'es_bajista': es_bajista_flexible,
            'cruce_alcista': cruce_alcista_estocastico,
            'cruce_bajista': cruce_bajista_estocastico,
            'supertrend_buy': supertrend_buy,
            'supertrend_sell': supertrend_sell,
            'supertrend_estado': "🟢 ALCISTA" if st_dir == 1 else "🔴 BAJISTA",
            'stoch_estado': "🟢 COMPRA" if stoch_k > stoch_d else "🔴 VENTA",
            'stoch_k': stoch_k,
            'cierra_arriba_ema10': precio > df['ema10'].iloc[-1],
            'cierra_abajo_ema10': precio < df['ema10'].iloc[-1],
            'ema10': df['ema10'].iloc[-1],
            'ema20': df['ema20'].iloc[-1],
            'ema55': e55,
            'soporte': soporte_key,
            'resistencia': resistencia_key,
            'bb_upper': df['bb_upper'].iloc[-1],
            'bb_lower': df['bb_lower'].iloc[-1],
        }
    except Exception as e:
        return None

# ==========================================
# MÓDULO UNIFICADO DE EVALUACIÓN
# ==========================================
def evaluar_todas_las_estrategias(simbolo_limpio, analisis_tf):
    if '1h' not in analisis_tf or '4h' not in analisis_tf or '1d' not in analisis_tf:
        return None

    d1 = analisis_tf['1d']
    h4 = analisis_tf['4h']
    h1 = analisis_tf['1h']
    
    precio_act = h1['precio']
    atr_act = h1['atr']
    
    sniper_res = []

    adx_aprobado_long = h1['adx'] >= 12 and h1['rsi'] > 25 and h1['rsi'] < 80
    adx_aprobado_short = h1['adx'] >= 12 and h1['rsi'] > 20 and h1['rsi'] < 75

    # Estructura 4H
    h4_alcista_real = (h4['supertrend_estado'] == "🟢 ALCISTA") and (h4['precio'] > h4['ema20'])
    h4_bajista_real = (h4['supertrend_estado'] == "🔴 BAJISTA") and (h4['precio'] < h4['ema20'])

    # Gatillos 1H 
    quiebre_inicial_long = h1.get('supertrend_buy', False)
    quiebre_inicial_short = h1.get('supertrend_sell', False)
    continuacion_pausa_long = (h1['supertrend_estado'] == "🟢 ALCISTA") and h1['cierra_arriba_ema10'] and (h1['ema10'] > h1['ema55'])
    continuacion_pausa_short = (h1['supertrend_estado'] == "🔴 BAJISTA") and h1['cierra_abajo_ema10'] and (h1['ema10'] < h1['ema55'])

    gatillo_1h_long = quiebre_inicial_long or continuacion_pausa_long
    gatillo_1h_short = quiebre_inicial_short or continuacion_pausa_short

    # Estocástico 1H
    stoch_k_1h = h1.get('stoch_k', 50)
    stoch_d_1h = h1.get('stoch_d', 50)
    filtro_estocastico_long = (stoch_k_1h < 55) and (stoch_k_1h > stoch_d_1h)
    filtro_estocastico_short = (stoch_k_1h > 45) and (stoch_k_1h < stoch_d_1h)

    # Filtro Anti-Persecución 1H
    distancia_1h_ema = abs(h1['precio'] - h1['ema10'])
    max_extension_1h = h1['atr'] * 2.2
    filtro_1h_no_extendido = distancia_1h_ema <= max_extension_1h

    emas_1h_alcistas = h1['ema10'] > h1['ema55']
    emas_1h_bajistas = h1['ema10'] < h1['ema55']

    # GATILLOS FINALES
    gatillo_long_10x = (
        d1['es_alcista'] and 
        h4_alcista_real and  
        emas_1h_alcistas and 
        adx_aprobado_long and
        gatillo_1h_long and  
        filtro_estocastico_long and  
        filtro_1h_no_extendido
    )

    if gatillo_long_10x:
        sl_final = h1['soporte'] - (1.0 * atr_act)
        pct_sl = abs((precio_act - sl_final) / precio_act) * 100
        riesgo = precio_act - sl_final
        
        if riesgo > 0: 
            sniper_res.append({
                'symbol': simbolo_limpio, 'tipo': 'LONG 🟢',
                'precio': precio_act, 'sl': sl_final, 'pct_sl': pct_sl,
                'tp1': precio_act + (riesgo * 1.5), 'tp2': precio_act + (riesgo * 2.5), 'tp3': precio_act + (riesgo * 3.5),
                'supertrend': h1['supertrend_estado'],
                'motivos': ["Estructura alcista confirmada en D1/H4/H1", "SuperTrend H1 alcista", "Sin sobre-extensión en 1H"]
            })

    gatillo_short_10x = (
        d1['es_bajista'] and 
        h4_bajista_real and  
        emas_1h_bajistas and 
        adx_aprobado_short and
        gatillo_1h_short and 
        filtro_estocastico_short and 
        filtro_1h_no_extendido
    )

    if gatillo_short_10x:
        sl_final = h1['resistencia'] + (1.0 * atr_act)
        pct_sl = abs((sl_final - precio_act) / precio_act) * 100
        riesgo = sl_final - precio_act
        
        if riesgo > 0: 
            sniper_res.append({
                'symbol': simbolo_limpio, 'tipo': 'SHORT 🔴',
                'precio': precio_act, 'sl': sl_final, 'pct_sl': pct_sl,
                'tp1': precio_act - (riesgo * 1.5), 'tp2': precio_act - (riesgo * 2.5), 'tp3': precio_act - (riesgo * 3.5),
                'supertrend': h1['supertrend_estado'],
                'motivos': ["Estructura bajista confirmada en D1/H4/H1", "SuperTrend H1 bajista", "Sin sobre-extensión en 1H"]
            })

    return sniper_res

# ==========================================
# 5. FUNCIONES DE ESCANEO / CONSULTA MANUAL
# ==========================================
def obtener_pares_top():
    try:
        exchange.load_markets()
        tickers = exchange.fetch_tickers()
        estables_ignoradas = ['USDC', 'USDT', 'BUSD', 'FDUSD', 'EUR', 'DAI', 'TUSD', 'USD1']
        pares_usdt = [{'symbol': s, 'volume': t['quoteVolume']} for s, t in tickers.items() if s.endswith('/USDT') and t.get('quoteVolume') and s.split('/')[0] not in estables_ignoradas]
        return [item['symbol'] for item in sorted(pares_usdt, key=lambda x: x['volume'], reverse=True)[:350]]
    except Exception as e:
        return []

def analizar_cripto_individual(ticker_raw):
    ticker = ticker_raw.upper().replace("$", "").replace("USDT", "") + "/USDT"
    msj = f"🤖 **BOT ACTIVO ✅**\n\n📊 *ANÁLISIS ${ticker.split('/')[0]}*\n\n"
    for tf in ['1h', '4h', '1d', '1w']:
        res = analizar_par_completo(ticker, tf)
        if res:
            msj += f"• {tf.upper()}: ST `{res['supertrend_estado']}` | RSI `{res['rsi']:.1f}`\n"
    enviar_telegram(msj)

def evaluar_trade_manual(ticker_raw):
    ticker = ticker_raw.upper().replace("$", "").replace("USDT", "") + "/USDT"
    analisis_tf = {tf: analizar_par_completo(ticker, tf) for tf in ['1h', '4h', '1d']}
    if None in analisis_tf.values():
        enviar_telegram("❌ Datos insuficientes.")
        return
    sniper = evaluar_todas_las_estrategias(ticker.split('/')[0], analisis_tf)
    msj = f"🎯 *EVALUACIÓN MANUAL ${ticker.split('/')[0]}*\n\n"
    if sniper:
        for op in sniper:
            msj += f"⚡ {op['tipo']} | SL `{fmt_precio(op['sl'])}`\n"
    else:
        msj += "⚪ No cumple condiciones."
    enviar_telegram(msj)

# ==========================================
# 6. ESCANEO RÁPIDO Y BUCLE PRINCIPAL
# ==========================================
def procesar_par_paralelo(par):
    analisis_tf = {tf: analizar_par_completo(par, tf) for tf in ['1h', '4h', '1d']}
    return evaluar_todas_las_estrategias(par.split('/')[0], analisis_tf)

def escanear_senales_sniper_manual():
    enviar_telegram("🔍 Escaneando mercado...")
    pares = obtener_pares_top()
    entradas = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        for sniper in [f.result() for f in [executor.submit(procesar_par_paralelo, p) for p in pares] if f.result()]:
            entradas.extend(sniper)
    
    if not entradas:
        enviar_telegram("❌ Sin entradas activas.")
        return
        
    msj = "⚡ *ENTRADAS DETECTADAS:*\n\n"
    for op in entradas[:5]:
        msj += f"🪙 {op['symbol']} | {op['tipo']} | SL `{fmt_precio(op['sl'])}`\n"
    enviar_telegram(msj)

def escuchar_mensajes_telegram():
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    offset = None
    while True:
        try:
            resp = requests.get(url, params={"timeout": 15, "offset": offset}, timeout=20).json()
            if resp.get("ok"):
                for result in resp.get("result", []):
                    offset = result["update_id"] + 1
                    text = result.get("message", {}).get("text", "").strip()
                    if text.startswith("/trade"):
                        threading.Thread(target=evaluar_trade_manual, args=(text.split()[1],)).start()
                    elif text.startswith("/senales"):
                        threading.Thread(target=escanear_senales_sniper_manual).start()
        except: pass
        time.sleep(1)

if __name__ == "__main__":
    threading.Thread(target=escuchar_mensajes_telegram, daemon=True).start()
    while True:
        time.sleep(7200)
        # analizar_mercado()...
