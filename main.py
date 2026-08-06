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
        e10_prev, e20_prev = df['ema10'].iloc[-2], df['ema20'].iloc[-2]
        
        cruce_ema_alcista = (e10_prev <= e20_prev) and (e10 > e20)
        cruce_ema_bajista = (e10_prev >= e20_prev) and (e10 < e20)

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

        recent_df = df.tail(30)
        swing_high = recent_df['high'].max()
        swing_low = recent_df['low'].min()
        rango_fibo = swing_high - swing_low

        fibo_tp1_long = precio + (rango_fibo * 0.618)
        fibo_tp2_long = swing_high if swing_high > precio else (precio + rango_fibo)
        fibo_tp3_long = precio + (rango_fibo * 1.618)

        fibo_tp1_short = precio - (rango_fibo * 0.618)
        fibo_tp2_short = swing_low if swing_low < precio else (precio - rango_fibo)
        fibo_tp3_short = precio - (rango_fibo * 1.618)

        es_alcista_flexible = (precio > e55) or (st_dir == 1)
        es_bajista_flexible = (precio < e55) or (st_dir == -1)

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
            'cruce_ema_alcista': cruce_ema_alcista,
            'cruce_ema_bajista': cruce_ema_bajista,
            'supertrend_buy': supertrend_buy,
            'supertrend_sell': supertrend_sell,
            'supertrend_estado': "🟢 ALCISTA" if st_dir == 1 else "🔴 BAJISTA",
            'stoch_estado': "🟢 COMPRA" if stoch_k > stoch_d else "🔴 VENTA",
            'stoch_k': stoch_k,
            'ema10': e10,
            'ema20': e20,
            'ema55': e55,
            'soporte': soporte_key,
            'resistencia': resistencia_key,
            'bb_upper': df['bb_upper'].iloc[-1],
            'bb_lower': df['bb_lower'].iloc[-1],
            'fibo_long': {'tp1': fibo_tp1_long, 'tp2': fibo_tp2_long, 'tp3': fibo_tp3_long},
            'fibo_short': {'tp1': fibo_tp1_short, 'tp2': fibo_tp2_short, 'tp3': fibo_tp3_short}
        }
    except Exception as e:
        return None

# ==========================================
# 5. MOTOR DE EVALUACIÓN MULTI-ESTRATEGIA (EN PARALELO)
# ==========================================
def evaluar_todas_las_estrategias(simbolo_limpio, analisis_tf):
    if '1h' not in analisis_tf or '4h' not in analisis_tf or '1d' not in analisis_tf or '15m' not in analisis_tf:
        return None, None

    d1 = analisis_tf['1d']
    h4 = analisis_tf['4h']
    h1 = analisis_tf['1h']
    m15 = analisis_tf['15m']
    
    precio_act = h1['precio']
    atr_act = h1['atr']
    
    sniper_res = []
    spot_res = []

    # ==========================================
    # ESTRATEGIA SNIPER 10X A: PULLBACK CLÁSICO
    # ==========================================
    adx_aprobado_long = h1['adx'] >= 12 and h1['rsi'] > 40 and h1['rsi'] < 68
    adx_aprobado_short = h1['adx'] >= 12 and h1['rsi'] > 32 and h1['rsi'] < 60

    pullback_long = h1['precio'] <= (h1['ema10'] * 1.015) and h1['precio'] >= (h1['ema20'] * 0.985)
    pullback_short = h1['precio'] >= (h1['ema10'] * 0.985) and h1['precio'] <= (h1['ema20'] * 1.015)

    filtro_estocastico_long = h1['cruce_alcista'] and h1['stoch_k'] < 45
    filtro_estocastico_short = h1['cruce_bajista'] and h1['stoch_k'] > 55

    filtro_15m_long = m15['precio'] > m15['ema20']
    filtro_15m_short = m15['precio'] < m15['ema20']

    gatillo_long_pullback = (
        d1['es_alcista'] and h4['es_alcista'] and  
        adx_aprobado_long and
        (h1['supertrend_estado'] == "🟢 ALCISTA") and
        pullback_long and
        filtro_estocastico_long and  
        filtro_15m_long             
    )

    if gatillo_long_pullback:
        sl_final = h1['soporte'] - (1.0 * atr_act)
        pct_sl = abs((precio_act - sl_final) / precio_act) * 100
        riesgo = precio_act - sl_final
        tp1 = precio_act + (riesgo * 1.5)
        tp2 = precio_act + (riesgo * 2.5)
        tp3 = precio_act + (riesgo * 3.5)
        beneficio = tp1 - precio_act
        ratio_actual = beneficio / riesgo if riesgo > 0 else 0
        
        if riesgo > 0 and ratio_actual >= 1.2:  
            sniper_res.append({
                'symbol': simbolo_limpio, 'tipo': 'LONG 10X [PULLBACK] 🟢',
                'precio': precio_act, 'sl': sl_final, 'pct_sl': pct_sl,
                'tp1': tp1, 'pct_tp1': abs((tp1 - precio_act)/precio_act)*100,
                'tp2': tp2, 'pct_tp2': abs((tp2 - precio_act)/precio_act)*100,
                'tp3': tp3, 'pct_tp3': abs((tp3 - precio_act)/precio_act)*100,
                'supertrend': h1['supertrend_estado'],
                'rr': f"1:{ratio_actual:.2f}",
                'motivos': [
                    f"Tendencia Diaria (1D) y 4H Alcista aprobada",
                    f"SuperTrend 1H Alcista (🟢) con ADX activo ({h1['adx']:.1f})",
                    f"Pullback validado sobre EMA10 / EMA20 con cruce estocástico y filtro 15m"
                ]
            })

    gatillo_short_pullback = (
        d1['es_bajista'] and h4['es_bajista'] and  
        adx_aprobado_short and
        (h1['supertrend_estado'] == "🔴 BAJISTA") and
        pullback_short and
        filtro_estocastico_short and 
        filtro_15m_short            
    )

    if gatillo_short_pullback:
        sl_final = h1['resistencia'] + (1.0 * atr_act)
        pct_sl = abs((sl_final - precio_act) / precio_act) * 100
        riesgo = sl_final - precio_act
        tp1 = precio_act - (riesgo * 1.5)
        tp2 = precio_act - (riesgo * 2.5)
        tp3 = precio_act - (riesgo * 3.5)
        beneficio = precio_act - tp1
        ratio_actual = beneficio / riesgo if riesgo > 0 else 0

        if riesgo > 0 and ratio_actual >= 1.2:  
            sniper_res.append({
                'symbol': simbolo_limpio, 'tipo': 'SHORT 10X [PULLBACK] 🔴',
                'precio': precio_act, 'sl': sl_final, 'pct_sl': pct_sl,
                'tp1': tp1, 'pct_tp1': abs((precio_act - tp1)/precio_act)*100,
                'tp2': tp2, 'pct_tp2': abs((tp2 - precio_act)/precio_act)*100,
                'tp3': tp3, 'pct_tp3': abs((tp3 - precio_act)/precio_act)*100,
                'supertrend': h1['supertrend_estado'],
                'rr': f"1:{ratio_actual:.2f}",
                'motivos': [
                    f"Tendencia Diaria (1D) y 4H Bajista aprobada",
                    f"SuperTrend 1H Bajista (🔴) con ADX activo ({h1['adx']:.1f})",
                    f"Pullback bajista validado con cruce de estocástico y filtro 15m"
                ]
            })

    # ==========================================
    # ESTRATEGIA SNIPER 10X B: CRUCE DE MEDIAS (EMA 10 / 20) + FILTRO 15M
    # ==========================================
    gatillo_long_cruce = (
        d1['es_alcista'] and h4['es_alcista'] and
        h1['adx'] >= 14 and (40 < h1['rsi'] < 70) and
        h1['cruce_ema_alcista'] and
        (h1['supertrend_estado'] == "🟢 ALCISTA") and
        filtro_15m_long
    )

    if gatillo_long_cruce:
        sl_final = h1['soporte'] - (1.2 * atr_act)
        pct_sl = abs((precio_act - sl_final) / precio_act) * 100
        riesgo = precio_act - sl_final
        tp1 = precio_act + (riesgo * 1.8)
        tp2 = precio_act + (riesgo * 2.8)
        tp3 = precio_act + (riesgo * 3.8)
        beneficio = tp1 - precio_act
        ratio_actual = beneficio / riesgo if riesgo > 0 else 0

        if riesgo > 0 and ratio_actual >= 1.2:
            sniper_res.append({
                'symbol': simbolo_limpio, 'tipo': 'LONG 10X [CRUCE EMA] 🟢',
                'precio': precio_act, 'sl': sl_final, 'pct_sl': pct_sl,
                'tp1': tp1, 'pct_tp1': abs((tp1 - precio_act)/precio_act)*100,
                'tp2': tp2, 'pct_tp2': abs((tp2 - precio_act)/precio_act)*100,
                'tp3': tp3, 'pct_tp3': abs((tp3 - precio_act)/precio_act)*100,
                'supertrend': h1['supertrend_estado'],
                'rr': f"1:{ratio_actual:.2f}",
                'motivos': [
                    f"Tendencia macro favorable (1D y 4H Alcistas)",
                    f"Cruce alcista directo de EMA 10 sobre EMA 20 en 1H con filtro 15m",
                    f"ADX fuerte ({h1['adx']:.1f}) y RSI en zona óptima ({h1['rsi']:.1f})"
                ]
            })

    gatillo_short_cruce = (
        d1['es_bajista'] and h4['es_bajista'] and
        h1['adx'] >= 14 and (30 < h1['rsi'] < 60) and
        h1['cruce_ema_bajista'] and
        (h1['supertrend_estado'] == "🔴 BAJISTA") and
        filtro_15m_short
    )

    if gatillo_short_cruce:
        sl_final = h1['resistencia'] + (1.2 * atr_act)
        pct_sl = abs((sl_final - precio_act) / precio_act) * 100
        riesgo = sl_final - precio_act
        tp1 = precio_act - (riesgo * 1.8)
        tp2 = precio_act - (riesgo * 2.8)
        tp3 = precio_act - (riesgo * 3.8)
        beneficio = precio_act - tp1
        ratio_actual = beneficio / riesgo if riesgo > 0 else 0

        if riesgo > 0 and ratio_actual >= 1.2:
            sniper_res.append({
                'symbol': simbolo_limpio, 'tipo': 'SHORT 10X [CRUCE EMA] 🔴',
                'precio': precio_act, 'sl': sl_final, 'pct_sl': pct_sl,
                'tp1': tp1, 'pct_tp1': abs((precio_act - tp1)/precio_act)*100,
                'tp2': tp2, 'pct_tp2': abs((tp2 - precio_act)/precio_act)*100,
                'tp3': tp3, 'pct_tp3': abs((tp3 - precio_act)/precio_act)*100,
                'supertrend': h1['supertrend_estado'],
                'rr': f"1:{ratio_actual:.2f}",
                'motivos': [
                    f"Tendencia macro favorable (1D y 4H Bajistas)",
                    f"Cruce bajista directo de EMA 10 bajo EMA 20 en 1H con filtro 15m",
                    f"ADX fuerte ({h1['adx']:.1f}) y SuperTrend en 1H BAJISTA"
                ]
            })

    # ==========================================
    # ESTRATEGIA SPOT
    # ==========================================
    h4_rsi_valido_spot = h4['rsi'] < 75
    h4_adx_valido_spot = h4['adx'] >= 15
    h1_rsi_valido_spot = h1['rsi'] < 75
    h1_adx_valido_spot = h1['adx'] >= 15
    estocastico_valido_spot = h1['stoch_k'] < 50  
    pullback_spot_valido = h1['precio'] <= (h1['ema10'] * 1.03) and h1['precio'] >= (h1['ema20'] * 0.97)

    gatillo_spot = (
        d1['es_alcista'] and
        h4_adx_valido_spot and h4_rsi_valido_spot and (h4['supertrend_estado'] == "🟢 ALCISTA") and
        h1_adx_valido_spot and h1_rsi_valido_spot and (h1['supertrend_estado'] == "🟢 ALCISTA") and 
        estocastico_valido_spot and
        pullback_spot_valido
    )

    if gatillo_spot:
        sl_spot = h1['soporte'] - (1.5 * atr_act)
        pct_sl_spot = abs((precio_act - sl_spot) / precio_act) * 100
        resistencia_spot = h1['resistencia'] if h1['resistencia'] > precio_act else (precio_act + (atr_act * 4))
        fibo_s = h1['fibo_long']
        tp1_s = min(resistencia_spot, max(fibo_s['tp1'], precio_act + (atr_act * 2)))
        tp2_s = max(fibo_s['tp2'], tp1_s * 1.02)
        tp3_s = max(fibo_s['tp3'], tp2_s * 1.02)

        riesgo_s = precio_act - sl_spot
        beneficio_s = tp1_s - precio_act
        ratio_spot = beneficio_s / riesgo_s if riesgo_s > 0 else 0
        
        if riesgo_s > 0 and ratio_spot >= 1.2:
            spot_res.append({
                'symbol': simbolo_limpio,
                'precio': precio_act, 'sl': sl_spot, 'pct_sl': pct_sl_spot,
                'tp1': tp1_s, 'pct_tp1': abs((tp1_s - precio_act)/precio_act)*100,
                'tp2': tp2_s, 'pct_tp2': abs((tp2_s - precio_act)/precio_act)*100,
                'tp3': tp3_s, 'pct_tp3': abs((tp3_s - precio_act)/precio_act)*100,
                'supertrend': h1['supertrend_estado'],
                'rr': f"1:{ratio_spot:.2f}",
                'motivos': [
                    f"Tendencia Diaria Alcista con SuperTrend 4H/1H activos",
                    f"RSI 1H controlado ({h1['rsi']:.1f}) y Pullback armónico"
                ]
            })

    return sniper_res, spot_res

# ==========================================
# 6. FUNCIONES DE ESCANEO Y COMANDOS DE TELEGRAM
# ==========================================
def obtener_pares_top():
    try:
        exchange.load_markets()
        tickers = exchange.fetch_tickers()
        estables_ignoradas = ['USDC', 'USDT', 'BUSD', 'FDUSD', 'EUR', 'DAI', 'TUSD', 'USD1']
        
        pares_usdt = [
            {'symbol': symbol, 'volume': ticker['quoteVolume']}
            for symbol, ticker in tickers.items()
            if symbol.endswith('/USDT') 
            and ticker.get('quoteVolume') is not None
            and symbol.split('/')[0] not in estables_ignoradas
            and not symbol.split('/')[0].startswith('USD1')
        ]
        
        pares_usdt = sorted(pares_usdt, key=lambda x: x['volume'], reverse=True)
        return [item['symbol'] for item in pares_usdt[:250]]
    except Exception as e:
        logging.error(f"Error obteniendo pares top: {e}")
        return []

def analizar_cripto_individual(ticker_raw):
    ticker = ticker_raw.upper().replace("$", "").replace("USDT", "") + "/USDT"
    simbolo_limpio = ticker.split('/')[0]
    
    temporalidades = ['15m', '1h', '4h', '1d', '1w']
    msj = f"🤖 **BOT ACTIVO ✅**\n\n📊 *ANÁLISIS TÉCNICO DETALLADO: ${simbolo_limpio}*\n\n"
    
    for tf in temporalidades:
        res = analizar_par_completo(ticker, tf)
        if res is not None:
            msj += f"• *Temporalidad {tf.upper()}*:\n"
            msj += f"  - Precio: `{fmt_precio(res['precio'])}`\n"
            msj += f"  - SuperTrend: `{res['supertrend_estado']}`\n"
            msj += f"  - RSI: `{res['rsi']:.1f}` | MFI: `{res['mfi']:.1f}`\n"
            msj += f"  - ADX: `{res['adx']:.1f}` ({res['adx_fuerza']})\n"
            msj += f"  - Soporte: `{fmt_precio(res['soporte'])}` | Resistencia: `{fmt_precio(res['resistencia'])}`\n\n"
        else:
            msj += f"• *Temporalidad {tf.upper()}*: Sin datos suficientes.\n\n"
            
    enviar_telegram(msj)

def evaluar_trade_manual(ticker_raw):
    ticker = ticker_raw.upper().replace("$", "").replace("USDT", "") + "/USDT"
    simbolo_limpio = ticker.split('/')[0]
    
    temporalidades = ['15m', '1h', '4h', '1d', '1w']
    analisis_tf = {}
    
    for tf in temporalidades:
        res = analizar_par_completo(ticker, tf)
        if res is None:
            enviar_telegram(f"🤖 **BOT ACTIVO ✅**\n\n❌ No se pudo encontrar o analizar la cripto `{ticker_raw}` en BingX.")
            return
        analisis_tf[tf] = res

    sniper, spot = evaluar_todas_las_estrategias(simbolo_limpio, analisis_tf)
    
    msj = f"🤖 **BOT ACTIVO ✅**\n\n🎯 *EVALUACIÓN MANUAL: ${simbolo_limpio}*\n\n"

    if sniper:
        for op in sniper:
            msj += f"⚡ *{op['tipo']}* _(R:R {op['rr']})_\n"
            msj += f"🔮 *SuperTrend:* `{op['supertrend']}`\n"
            msj += f"💵 *Entrada:* `{fmt_precio(op['precio'])}`\n"
            msj += f"🛑 *Stop Loss:* `{fmt_precio(op['sl'])}` _(-{op['pct_sl']:.2f}%)_\n"
            msj += f"🎯 *TP1:* `{fmt_precio(op['tp1'])}` _(+{op['pct_tp1']:.2f}%)_\n"
            msj += f"🎯 *TP2:* `{fmt_precio(op['tp2'])}` _(+{op['pct_tp2']:.2f}%)_\n"
            msj += f"🎯 *TP3:* `{fmt_precio(op['tp3'])}` _(+{op['pct_tp3']:.2f}%)_\n"
            msj += f"📋 *Condiciones Cumplidas:*\n"
            for m in op.get('motivos', []):
                msj += f"  • {m}\n"
            msj += "\n"
    else:
        msj += "⚪ *ESTRATEGIAS SNIPER 10X:* Sin disparos activos para este par.\n\n"

    if spot:
        for op in spot:
            msj += f"🎯 *SPOT APROBADO 🟢* _(R:R {op['rr']})_\n"
            msj += f"💵 *Precio Entrada:* `{fmt_precio(op['precio'])}`\n"
            msj += f"🛑 *Stop Loss:* `{fmt_precio(op['sl'])}` _(-{op['pct_sl']:.2f}%)_\n"
            msj += f"🎯 *TP1:* `{fmt_precio(op['tp1'])}` _(+{op['pct_tp1']:.2f}%)_\n\n"

    enviar_telegram(msj)

def procesar_par_paralelo(par):
    temporalidades = ['15m', '1h', '4h', '1d', '1w']
    analisis_tf = {}
    for tf in temporalidades:
        res = analizar_par_completo(par, tf)
        if res is not None:
            analisis_tf[tf] = res
    
    simbolo_limpio = par.split('/')[0]
    sniper, spot = evaluar_todas_las_estrategias(simbolo_limpio, analisis_tf)
    return sniper, spot

def escanear_senales_sniper_manual():
    enviar_telegram("🤖 **BOT ACTIVO ✅**\n\n🔍 Escaneando mercado concurrentemente (Pullback + Cruce de Medias + Filtro 15m)...")
    
    pares_filtrados = obtener_pares_top()
    if not pares_filtrados:
        enviar_telegram("🤖 **BOT ACTIVO ✅**\n\n❌ Error al obtener los pares del mercado.")
        return

    entradas_sniper = []
    entradas_spot = []

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(procesar_par_paralelo, par): par for par in pares_filtrados}
        for future in as_completed(futures):
            try:
                sniper, spot = future.result()
                if sniper:
                    entradas_sniper.extend(sniper)
                if spot:
                    entradas_spot.extend(spot)
            except Exception as e:
                logging.error(f"Error procesando hilo: {e}")

    enviar_resultados_escaneo(entradas_sniper, entradas_spot)

def enviar_resultados_escaneo(entradas_sniper, entradas_spot):
    if not entradas_sniper and not entradas_spot:
        enviar_telegram("🤖 **BOT ACTIVO ✅**\n\n❌ *NO HAY SEÑALES ACTIVAS*\n\nNingún par cumple las condiciones estrictas actuales.")
        return

    if entradas_sniper:
        msj = "🤖 **BOT ACTIVO ✅**\n\n⚡ *SEÑALES SNIPER 10X DETECTADAS:* ⚡\n\n"
        for op in entradas_sniper[:6]:
            msj += f"🪙 *{op['symbol']}* -> *{op['tipo']}* _(R:R {op['rr']})_\n"
            msj += f"💵 *Entrada:* `{fmt_precio(op['precio'])}`\n"
            msj += f"🛑 *SL:* `{fmt_precio(op['sl'])}` _(-{op['pct_sl']:.2f}%)_ | 🎯 *TP1:* `{fmt_precio(op['tp1'])}`\n"
            msj += f"📋 *Motivos:* {op['motivos'][0]}\n\n"
        enviar_telegram(msj)

    if entradas_spot:
        msj_spot = "🤖 **BOT ACTIVO ✅**\n\n🎯 *SEÑALES SPOT DETECTADAS:* 🎯\n\n"
        for op in entradas_spot[:4]:
            msj_spot += f"🪙 *{op['symbol']}* -> *LONG SPOT 🟢* _(R:R {op['rr']})_\n"
            msj_spot += f"💵 *Entrada:* `{fmt_precio(op['precio'])}` | SL: `{fmt_precio(op['sl'])}`\n\n"
        enviar_telegram(msj_spot)

# ==========================================
# 7. ESCUCHADOR DE TELEGRAM BLINDADO
# ==========================================
def escuchar_mensajes_telegram():
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    offset = None
    
    try:
        init_resp = requests.get(url, params={"timeout": 1}, timeout=5).json()
        if init_resp.get("ok") and init_resp.get("result"):
            offset = init_resp["result"][-1]["update_id"] + 1
    except Exception:
        pass

    while True:
        try:
            params = {"timeout": 15, "offset": offset}
            resp = requests.get(url, params=params, timeout=20).json()
            
            if resp.get("ok"):
                for result in resp.get("result", []):
                    offset = result["update_id"] + 1
                    message = result.get("message", {})
                    text = message.get("text", "").strip()
                    
                    if text.startswith("/analizar"):
                        partes = text.split()
                        if len(partes) > 1:
                            enviar_telegram(f"🤖 **BOT ACTIVO ✅**\n\n⏳ Analizando `${partes[1].upper()}`...")
                            analizar_cripto_individual(partes[1])
                        else:
                            enviar_telegram("🤖 Indica la moneda. Ejemplo: `/analizar BTC`")
                            
                    elif text.startswith("/trade"):
                        partes = text.split()
                        if len(partes) > 1:
                            enviar_telegram(f"🤖 **BOT ACTIVO ✅**\n\n⏳ Evaluando estrategias para `${partes[1].upper()}`...")
                            evaluar_trade_manual(partes[1])
                        else:
                            enviar_telegram("🤖 Indica la moneda. Ejemplo: `/trade BTC`")

                    elif text.startswith("/comprobar") or text.startswith("/senales"):
                        threading.Thread(target=escanear_senales_sniper_manual, daemon=True).start()
        except Exception as e:
            logging.error(f"Error en bucle de Telegram: {e}")
        time.sleep(1)

# ==========================================
# 8. ESCANEO AUTOMÁTICO GENERAL
# ==========================================
ARCHIVO_BLOQUEO = "ultimo_escaneo.txt"

def analizar_mercado():
    if os.path.exists(ARCHIVO_BLOQUEO):
        if (time.time() - os.path.getmtime(ARCHIVO_BLOQUEO)) < 1800:
            return

    try:
        with open(ARCHIVO_BLOQUEO, "w") as f:
            f.write(str(time.time()))
    except Exception:
        pass

    logging.info("🔎 Escaneo automático de mercado iniciado...")
    
    try:
        pares_filtrados = obtener_pares_top()
        if not pares_filtrados:
            return
            
        entradas_sniper = []
        entradas_spot = []
        
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(procesar_par_paralelo, par): par for par in pares_filtrados}
            for future in as_completed(futures):
                try:
                    sniper, spot = future.result()
                    if sniper:
                        entradas_sniper.extend(sniper)
                    if spot:
                        entradas_spot.extend(spot)
                except Exception as e:
                    logging.error(f"Error en tarea paralela: {e}")

        enviar_resultados_escaneo(entradas_sniper, entradas_spot)
        logging.info("✅ Escaneo automático completado.")

    except Exception as e:
        logging.error(f"Error en escaneo general: {e}")

# ==========================================
# 9. BUCLE PRINCIPAL
# ==========================================
if __name__ == "__main__":
    lock_file = "app.lock"
    if os.path.exists(lock_file):
        if (time.time() - os.path.getmtime(lock_file)) < 10:
            sys.exit(0)
        
    try:
        with open(lock_file, "w") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass

    threading.Thread(target=escuchar_mensajes_telegram, daemon=True).start()
    
    try:
        res_btc = analizar_par_completo("BTC/USDT", '1h')
        if res_btc:
            enviar_telegram(f"🤖 **BOT ACTIVO ✅**\n\n🪙 **Bitcoin (BTC)** -> `{fmt_precio(res_btc['precio'])}` USDT\n🔮 SuperTrend 1H: `{res_btc['supertrend_estado']}` | RSI: `{res_btc['rsi']:.1f}`")
    except Exception:
        pass

    logging.info("🚀 Bot multitendencia actualizado y operando en paralelo.")
    analizar_mercado()
    
    while True:
        time.sleep(3600)
        try:
            with open(lock_file, "w") as f:
                f.write(str(os.getpid()))
        except Exception:
            pass
        analizar_mercado()
