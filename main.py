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
# FUNCIÓN AUXILIAR DE FORMATO DE PRECIO DINÁMICO
# ==========================================
def formatear_precio(val):
    if val is None:
        return "0.00"
    if val < 0.0001:
        return f"{val:.8f}"
    elif val < 1.0:
        return f"{val:.6f}"
    else:
        return f"{val:.4f}"

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
# NUEVO MÓDULO: REBOTE DE RANGO (MEJORADO)
# ==========================================
def detectar_rebote_rango_avanzado(h1, h4=None):
    if not h1:
        return None

    if h1['adx'] > 32:
        return None
        
    precio_entrada = h1['precio']
    rsi = h1['rsi']
    soporte = h1['soporte']
    resistencia = h1['resistencia']
    atr = h1['atr']
    
    bb_lower = h1.get('bb_lower', soporte)
    bb_upper = h1.get('bb_upper', resistencia)
    
    condicion_mecha_long = precio_entrada <= (soporte + (atr * 0.8)) or precio_entrada <= bb_lower
    condicion_mecha_short = precio_entrada >= (resistencia - (atr * 0.8)) or precio_entrada >= bb_upper

    if rsi < 48 and condicion_mecha_long:
        stop_loss = soporte - (1.5 * atr) if soporte < precio_entrada else precio_entrada * 0.975
        tp1 = precio_entrada + (atr * 1.5)
        tp2 = precio_entrada + (atr * 2.5)
        tp3 = precio_entrada + (atr * 3.5)
        
        riesgo = precio_entrada - stop_loss
        beneficio = tp1 - precio_entrada
        rr_val = f"1:{(beneficio/riesgo):.1f}" if riesgo > 0 else "1:1"

        return [{
            'tipo': 'LONG RANGO 🟢',
            'sl': stop_loss,
            'pct_sl': abs((precio_entrada - stop_loss)/precio_entrada)*100*10,
            'tp1': tp1,
            'pct_tp1': abs((tp1 - precio_entrada)/precio_entrada)*100*10,
            'tp2': tp2,
            'pct_tp2': abs((tp2 - precio_entrada)/precio_entrada)*100*10,
            'tp3': tp3,
            'pct_tp3': abs((tp3 - precio_entrada)/precio_entrada)*100*10,
            'rr': rr_val
        }]
        
    if rsi > 52 and condicion_mecha_short:
        stop_loss = resistencia + (1.5 * atr) if resistencia > precio_entrada else precio_entrada * 1.025
        tp1 = precio_entrada - (atr * 1.5)
        tp2 = precio_entrada - (atr * 2.5)
        tp3 = precio_entrada - (atr * 3.5)
        
        riesgo = stop_loss - precio_entrada
        beneficio = precio_entrada - tp1
        rr_val = f"1:{(beneficio/riesgo):.1f}" if riesgo > 0 else "1:1"

        return [{
            'tipo': 'SHORT RANGO 🔴',
            'sl': stop_loss,
            'pct_sl': abs((stop_loss - precio_entrada)/precio_entrada)*100*10,
            'tp1': tp1,
            'pct_tp1': abs((precio_entrada - tp1)/precio_entrada)*100*10,
            'tp2': tp2,
            'pct_tp2': abs((precio_entrada - tp2)/precio_entrada)*100*10,
            'tp3': tp3,
            'pct_tp3': abs((precio_entrada - tp3)/precio_entrada)*100*10,
            'rr': rr_val
        }]
        
    return None

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

        puntos_alcistas = sum([precio > e55, e10 > e20, st_dir == 1, mfi > 50])
        puntos_bajistas = sum([precio <= e55, e10 <= e20, st_dir == -1, mfi <= 50])

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
            'es_alcista': puntos_alcistas >= 3,
            'es_bajista': puntos_bajistas >= 3,
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
            'soporte': soporte_key,
            'resistencia': resistencia_key,
            'bb_upper': df['bb_upper'].iloc[-1],
            'bb_lower': df['bb_lower'].iloc[-1],
        }
    except Exception as e:
        return None

# ==========================================
# MÓDULO UNIFICADO DE EVALUACIÓN (DRY)
# ==========================================
def evaluar_todas_las_estrategias(simbolo_limpio, analisis_tf):
    if '1h' not in analisis_tf or '4h' not in analisis_tf or '1d' not in analisis_tf:
        return None, None, None

    h1 = analisis_tf['1h']
    h4 = analisis_tf['4h']
    d1 = analisis_tf['1d']
    
    precio_act = h1['precio']
    atr_act = h1['atr']
    
    sniper_res = []
    spot_res = []
    rango_res = []

    # 1. Rangos
    senales_rango = detectar_rebote_rango_avanzado(h1, h4)
    if senales_rango:
        for sr in senales_rango:
            rango_res.append({
                'symbol': simbolo_limpio, 'tipo': sr['tipo'],
                'precio': precio_act, 'sl': sr['sl'], 'pct_sl': sr['pct_sl'],
                'tp1': sr['tp1'], 'pct_tp1': sr['pct_tp1'],
                'tp2': sr['tp2'], 'pct_tp2': sr['pct_tp2'],
                'tp3': sr['tp3'], 'pct_tp3': sr['pct_tp3'],
                'rr': sr['rr']
            })

    # 2. Sniper 10X (TPs cercanos y realistas por ATR)
    adx_aprobado = h1['adx'] >= 26          
    rsi_long_valido = h1['rsi'] < 70
    rsi_short_valido = h1['rsi'] > 30

    pullback_long = h1['precio'] <= (h1['ema10'] * 1.01) and h1['precio'] >= (h1['ema20'] * 0.98)
    pullback_short = h1['precio'] >= (h1['ema10'] * 0.99) and h1['precio'] <= (h1['ema20'] * 1.02)

    gatillo_long_10x = (
        d1['es_alcista'] and h4['es_alcista'] and
        adx_aprobado and rsi_long_valido and
        (h1['supertrend_estado'] == "🟢 ALCISTA") and
        pullback_long
    )

    if gatillo_long_10x:
        sl_tecnico = h1['soporte'] - (1.5 * atr_act)
        sl_max_10x = precio_act * 0.965
        sl_final = max(sl_tecnico, sl_max_10x)
        pct_sl = abs((precio_act - sl_final) / precio_act) * 100 * 10
        
        tp1 = precio_act + (atr_act * 1.5)
        tp2 = precio_act + (atr_act * 2.5)
        tp3 = precio_act + (atr_act * 3.5)

        riesgo = precio_act - sl_final
        beneficio = tp1 - precio_act
        
        if riesgo > 0 and (beneficio / riesgo) >= 1.2:
            sniper_res.append({
                'symbol': simbolo_limpio, 'tipo': 'LONG 🟢',
                'precio': precio_act, 'sl': sl_final, 'pct_sl': pct_sl,
                'tp1': tp1, 'pct_tp1': abs((tp1 - precio_act)/precio_act)*100*10,
                'tp2': tp2, 'pct_tp2': abs((tp2 - precio_act)/precio_act)*100*10,
                'tp3': tp3, 'pct_tp3': abs((tp3 - precio_act)/precio_act)*100*10,
                'supertrend': h1['supertrend_estado'],
                'rr': f"1:{(beneficio/riesgo):.1f}"
            })

    gatillo_short_10x = (
        d1['es_bajista'] and h4['es_bajista'] and
        adx_aprobado and rsi_short_valido and
        (h1['supertrend_estado'] == "🔴 BAJISTA") and
        pullback_short
    )

    if gatillo_short_10x:
        sl_tecnico = h1['resistencia'] + (1.5 * atr_act)
        sl_max_10x = precio_act * 1.035
        sl_final = min(sl_tecnico, sl_max_10x)
        pct_sl = abs((sl_final - precio_act) / precio_act) * 100 * 10
        
        tp1 = precio_act - (atr_act * 1.5)
        tp2 = precio_act - (atr_act * 2.5)
        tp3 = precio_act - (atr_act * 3.5)

        riesgo = sl_final - precio_act
        beneficio = precio_act - tp1

        if riesgo > 0 and (beneficio / riesgo) >= 1.2:
            sniper_res.append({
                'symbol': simbolo_limpio, 'tipo': 'SHORT 🔴',
                'precio': precio_act, 'sl': sl_final, 'pct_sl': pct_sl,
                'tp1': tp1, 'pct_tp1': abs((precio_act - tp1)/precio_act)*100*10,
                'tp2': tp2, 'pct_tp2': abs((tp2 - precio_act)/precio_act)*100*10,
                'tp3': tp3, 'pct_tp3': abs((tp3 - precio_act)/precio_act)*100*10,
                'supertrend': h1['supertrend_estado'],
                'rr': f"1:{(beneficio/riesgo):.1f}"
            })

    # 3. Sniper Spot (Validación de Estocástico y TPs por ATR)
    h4_rsi_valido = h4['rsi'] < 70
    h4_adx_valido = h4['adx'] >= 26
    h1_rsi_valido = h1['rsi'] < 70
    h1_adx_valido = h1['adx'] >= 26

    estocastico_valido_spot = h1['stoch_k'] < 35

    gatillo_spot = (
        d1['es_alcista'] and
        h4_adx_valido and h4_rsi_valido and (h4['supertrend_estado'] == "🟢 ALCISTA") and
        h1_adx_valido and h1_rsi_valido and (h1['supertrend_estado'] == "🟢 ALCISTA") and 
        estocastico_valido_spot
    )

    if gatillo_spot:
        sl_spot = h1['soporte'] - (1.5 * atr_act)
        pct_sl_spot = abs((precio_act - sl_spot) / precio_act) * 100
        
        tp1_s = precio_act + (atr_act * 2.0)
        tp2_s = precio_act + (atr_act * 3.5)
        tp3_s = precio_act + (atr_act * 5.0)

        riesgo_s = precio_act - sl_spot
        beneficio_s = tp1_s - precio_act
        
        if riesgo_s > 0 and (beneficio_s / riesgo_s) >= 1.2:
            spot_res.append({
                'symbol': simbolo_limpio,
                'precio': precio_act, 'sl': sl_spot, 'pct_sl': pct_sl_spot,
                'tp1': tp1_s, 'pct_tp1': abs((tp1_s - precio_act)/precio_act)*100,
                'tp2': tp2_s, 'pct_tp2': abs((tp2_s - precio_act)/precio_act)*100,
                'tp3': tp3_s, 'pct_tp3': abs((tp3_s - precio_act)/precio_act)*100,
                'supertrend': h1['supertrend_estado'],
                'rr': f"1:{(beneficio_s/riesgo_s):.1f}"
            })

    return sniper_res, spot_res, rango_res

# ==========================================
# 5. FUNCIONES DE ESCANEO / CONSULTA MANUAL
# ==========================================
def obtener_pares_top():
    try:
        exchange.load_markets()
        tickers = exchange.fetch_tickers()
        estables_ignoradas = ['USDC', 'USDT', 'BUSD', 'FDUSD', 'EUR', 'DAI', 'TUSD']
        
        pares_usdt = [
            {'symbol': symbol, 'volume': ticker['quoteVolume']}
            for symbol, ticker in tickers.items()
            if symbol.endswith('/USDT') 
            and ticker.get('quoteVolume') is not None
            and symbol.split('/')[0] not in estables_ignoradas
        ]
        
        pares_usdt = sorted(pares_usdt, key=lambda x: x['volume'], reverse=True)
        return [item['symbol'] for item in pares_usdt[:150]]
    except Exception as e:
        logging.error(f"Error obteniendo pares top: {e}")
        return []

def analizar_cripto_individual(ticker_raw):
    ticker = ticker_raw.upper().replace("$", "").replace("USDT", "") + "/USDT"
    simbolo_limpio = ticker.split('/')[0]
    
    temporalidades = ['1h', '4h', '1d', '1w']
    msj = f"🤖 **BOT ACTIVO ✅**\n\n📊 *ANÁLISIS TÉCNICO DETALLADO: ${simbolo_limpio}*\n\n"
    
    for tf in temporalidades:
        res = analizar_par_completo(ticker, tf)
        if res is not None:
            msj += f"• *Temporalidad {tf.upper()}*:\n"
            msj += f"  - Precio: `{formatear_precio(res['precio'])}`\n"
            msj += f"  - SuperTrend: `{res['supertrend_estado']}`\n"
            msj += f"  - RSI: `{res['rsi']:.1f}` | MFI: `{res['mfi']:.1f}`\n"
            msj += f"  - ADX: `{res['adx']:.1f}` ({res['adx_fuerza']})\n"
            msj += f"  - Soporte: `{formatear_precio(res['soporte'])}` | Resistencia: `{formatear_precio(res['resistencia'])}`\n\n"
        else:
            msj += f"• *Temporalidad {tf.upper()}*: Sin datos suficientes.\n\n"
            
    enviar_telegram(msj)

def evaluar_trade_manual(ticker_raw):
    ticker = ticker_raw.upper().replace("$", "").replace("USDT", "") + "/USDT"
    simbolo_limpio = ticker.split('/')[0]
    
    temporalidades = ['1h', '4h', '1d', '1w']
    analisis_tf = {}
    
    for tf in temporalidades:
        res = analizar_par_completo(ticker, tf)
        if res is None:
            enviar_telegram(f"🤖 **BOT ACTIVO ✅**\n\n❌ No se pudo encontrar o analizar la cripto `{ticker_raw}` en BingX.")
            return
        analisis_tf[tf] = res

    sniper, spot, rango = evaluar_todas_las_estrategias(simbolo_limpio, analisis_tf)
    
    msj = f"🤖 **BOT ACTIVO ✅**\n\n🎯 *EVALUACIÓN MANUAL DE TRADE: ${simbolo_limpio}*\n\n"

    if sniper:
        for op in sniper:
            msj += f"⚡ *ESTRATEGIA SNIPER 10X {op['tipo']}: APROBADA* _(R:R {op['rr']})_\n"
            msj += f"🔮 *SuperTrend:* `{op['supertrend']}`\n"
            msj += f"💵 *Entrada:* `{formatear_precio(op['precio'])}`\n"
            msj += f"🛑 *Stop Loss:* `{formatear_precio(op['sl'])}` _(-{op['pct_sl']:.1f}% en 10x)_\n"
            msj += f"🎯 *TP1:* `{formatear_precio(op['tp1'])}`\n"
            msj += f"🎯 *TP2:* `{formatear_precio(op['tp2'])}`\n"
            msj += f"🎯 *TP3:* `{formatear_precio(op['tp3'])}`\n\n"
    else:
        msj += "⚪ *SNIPER 10X:* No cumple con las reglas actuales.\n\n"

    if spot:
        for op in spot:
            msj += f"🎯 *ESTRATEGIA SNIPER SPOT: APROBADA* _(R:R {op['rr']})_\n"
            msj += f"🔮 *SuperTrend (1H):* `{op['supertrend']}`\n"
            msj += f"💵 *Precio Entrada:* `{formatear_precio(op['precio'])}`\n"
            msj += f"🛑 *Stop Loss:* `{formatear_precio(op['sl'])}` _(-{op['pct_sl']:.1f}%)_\n"
            msj += f"🎯 *TP1:* `{formatear_precio(op['tp1'])}`\n"
            msj += f"🎯 *TP2:* `{formatear_precio(op['tp2'])}`\n"
            msj += f"🎯 *TP3:* `{formatear_precio(op['tp3'])}`\n\n"
    else:
        msj += "⚪ *SNIPER SPOT:* No califica para trade en este momento.\n\n"

    if rango:
        for r in rango:
            msj += f"⚡ *{r['tipo']}*\n"
            msj += f"💵 *Entrada:* `{formatear_precio(r['precio'])}`\n"
            msj += f"🛑 *Stop Loss:* `{formatear_precio(r['sl'])}`\n"
            msj += f"🎯 *TP1:* `{formatear_precio(r['tp1'])}`\n"
            msj += f"🎯 *TP2:* `{formatear_precio(r['tp2'])}`\n"
            msj += f"🎯 *TP3:* `{formatear_precio(r['tp3'])}`\n"

    enviar_telegram(msj)

# ==========================================
# 6. ESCANEO RÁPIDO CONCURRENTE
# ==========================================
def procesar_par_paralelo(par):
    temporalidades = ['1h', '4h', '1d', '1w']
    analisis_tf = {}
    for tf in temporalidades:
        res = analizar_par_completo(par, tf)
        if res is not None:
            analisis_tf[tf] = res
    
    simbolo_limpio = par.split('/')[0]
    sniper, spot, rango = evaluar_todas_las_estrategias(simbolo_limpio, analisis_tf)
    return sniper, spot, rango

def escanear_senales_sniper_manual():
    enviar_telegram("🤖 **BOT ACTIVO ✅**\n\n🔍 Escaneando todo el mercado concurrentemente en busca de entradas Sniper y Rangos...")
    
    pares_filtrados = obtener_pares_top()
    if not pares_filtrados:
        enviar_telegram("🤖 **BOT ACTIVO ✅**\n\n❌ Error al obtener los pares del mercado en este momento.")
        return

    entradas_sniper = []
    entradas_sniper_spot = []
    entradas_rango = []

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(procesar_par_paralelo, par): par for par in pares_filtrados}
        for future in as_completed(futures):
            try:
                sniper, spot, rango = future.result()
                if sniper:
                    entradas_sniper.extend(sniper)
                if spot:
                    entradas_sniper_spot.extend(spot)
                if rango:
                    entradas_rango.extend(rango)
            except Exception as e:
                logging.error(f"Error procesando hilo de par: {e}")

    enviar_resultados_escaneo(entradas_sniper, entradas_sniper_spot, entradas_rango)

def enviar_resultados_escaneo(entradas_sniper, entradas_sniper_spot, entradas_rango):
    if not entradas_sniper and not entradas_sniper_spot and not entradas_rango:
        enviar_telegram("🤖 **BOT ACTIVO ✅**\n\n❌ *NO HAY ENTRADAS ACTIVAS*\n\nEn este momento ninguna criptomoneda cumple con las condiciones.")
        return

    if entradas_rango:
        msj_rango = "🤖 **BOT ACTIVO ✅**\n\n⚡ *REBOTES EN RANGO DETECTADOS:* ⚡\n\n"
        for op in entradas_rango[:5]:
            msj_rango += f"🪙 *{op['symbol']}* -> *{op['tipo']}* _(R:R {op['rr']})_\n"
            msj_rango += f"💵 *Entrada:* `{formatear_precio(op['precio'])}`\n"
            msj_rango += f"🛑 *Stop Loss:* `{formatear_precio(op['sl'])}` _(-{op['pct_sl']:.1f}% en 10x)_\n"
            msj_rango += f"🎯 *TP1:* `{formatear_precio(op['tp1'])}`\n🎯 *TP2:* `{formatear_precio(op['tp2'])}`\n🎯 *TP3:* `{formatear_precio(op['tp3'])}`\n\n"
        enviar_telegram(msj_rango)

    if entradas_sniper:
        msj_sniper = "🤖 **BOT ACTIVO ✅**\n\n⚡ *ENTRADAS SNIPER 10X DETECTADAS:* ⚡\n\n"
        for op in entradas_sniper[:5]:
            msj_sniper += f"🪙 *{op['symbol']}* -> *{op['tipo']}* _(R:R {op['rr']})_\n"
            msj_sniper += f"🔮 *SuperTrend:* `{op['supertrend']}`\n"
            msj_sniper += f"💵 *Entrada:* `{formatear_precio(op['precio'])}`\n"
            msj_sniper += f"🛑 *Stop Loss:* `{formatear_precio(op['sl'])}` _(-{op['pct_sl']:.1f}% en 10x)_\n"
            msj_sniper += f"🎯 *TP1:* `{formatear_precio(op['tp1'])}`\n🎯 *TP2:* `{formatear_precio(op['tp2'])}`\n🎯 *TP3:* `{formatear_precio(op['tp3'])}`\n\n"
        enviar_telegram(msj_sniper)

    if entradas_sniper_spot:
        msj_spot = "🤖 **BOT ACTIVO ✅**\n\n🎯 *ENTRADAS SNIPER SPOT DETECTADAS:* 🎯\n\n"
        for op in entradas_sniper_spot[:5]:
            msj_spot += f"🪙 *{op['symbol']}* -> *LONG SPOT 🟢* _(R:R {op['rr']})_\n"
            msj_spot += f"🔮 *SuperTrend:* `{op['supertrend']}`\n"
            msj_spot += f"💵 *Precio Entrada:* `{formatear_precio(op['precio'])}`\n"
            msj_spot += f"🛑 *Stop Loss:* `{formatear_precio(op['sl'])}` _(-{op['pct_sl']:.1f}%)_\n"
            msj_spot += f"🎯 *TP1:* `{formatear_precio(op['tp1'])}`\n🎯 *TP2:* `{formatear_precio(op['tp2'])}`\n🎯 *TP3:* `{formatear_precio(op['tp3'])}`\n\n"
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
                            ticker = partes[1]
                            enviar_telegram(f"🤖 **BOT ACTIVO ✅**\n\n⏳ Realizando análisis exhaustivo para `${ticker.upper()}`...")
                            analizar_cripto_individual(ticker)
                        else:
                            enviar_telegram("🤖 **BOT ACTIVO ✅**\n\nℹ️ Indica la moneda. Ejemplo: `/analizar BTC`")
                            
                    elif text.startswith("/trade"):
                        partes = text.split()
                        if len(partes) > 1:
                            ticker = partes[1]
                            enviar_telegram(f"🤖 **BOT ACTIVO ✅**\n\n⏳ Evaluando estrategia Sniper y Rangos para `${ticker.upper()}`...")
                            evaluar_trade_manual(ticker)
                        else:
                            enviar_telegram("🤖 **BOT ACTIVO ✅**\n\nℹ️ Indica la moneda. Ejemplo: `/trade BTC`")

                    elif text.startswith("/comprobar") or text.startswith("/senales"):
                        hilo_comprobar = threading.Thread(target=escanear_senales_sniper_manual, daemon=True)
                        hilo_comprobar.start()
        except Exception as e:
            logging.error(f"Error en bucle de Telegram: {e}")
        time.sleep(1)

# ==========================================
# 8. ESCANEO AUTOMÁTICO GENERAL (CADA 2 HORAS)
# ==========================================
ARCHIVO_BLOQUEO = "ultimo_escaneo.txt"

def analizar_mercado():
    if os.path.exists(ARCHIVO_BLOQUEO):
        tiempo_archivo = os.path.getmtime(ARCHIVO_BLOQUEO)
        if (time.time() - tiempo_archivo) < 1800:
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
        entradas_sniper_spot = []
        entradas_rango = []
        
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(procesar_par_paralelo, par): par for par in pares_filtrados}
            for future in as_completed(futures):
                try:
                    sniper, spot, rango = future.result()
                    if sniper:
                        entradas_sniper.extend(sniper)
                    if spot:
                        entradas_sniper_spot.extend(spot)
                    if rango:
                        entradas_rango.extend(rango)
                except Exception as e:
                    logging.error(f"Error en tarea paralela automática: {e}")

        enviar_resultados_escaneo(entradas_sniper, entradas_sniper_spot, entradas_rango)
        logging.info("✅ Escaneo automático completado.")

    except Exception as e:
        logging.error(f"Error en el escaneo general automático: {e}")

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

    hilo_telegram = threading.Thread(target=escuchar_mensajes_telegram, daemon=True)
    hilo_telegram.start()
    
    try:
        symbol_btc = "BTC/USDT"
        temporalidades = ['1h', '4h', '1d', '1w']
        analisis_btc = {}
        
        for tf in temporalidades:
            res_tf = analizar_par_completo(symbol_btc, tf)
            if res_tf:
                analisis_btc[tf] = res_tf
                
        if '1h' in analisis_btc:
            precio_btc = analisis_btc['1h']['precio']
            msj_inicio = f"🤖 **BOT ACTIVO ✅**\n\n"
            msj_inicio += f"🪙 **Bitcoin (BTC)** -> Precio Actual: `{formatear_precio(precio_btc)}` USDT\n\n"
            msj_inicio += "📊 **Estado en Temporalidades (Estrategia Bot):**\n"
            
            for tf in temporalidades:
                if tf in analisis_btc:
                    data = analisis_btc[tf]
                    tendencia = data['supertrend_estado']
                    rsi_val = data['rsi']
                    adx_val = data['adx']
                    fuerza_adx = data['adx_fuerza']
                    
                    msj_inicio += f"• **{tf.upper()}**: SuperTrend {tendencia} | RSI: `{rsi_val:.1f}` | ADX: `{adx_val:.1f}` ({fuerza_adx})\n"
            
            enviar_telegram(msj_inicio)
        else:
            enviar_telegram("🤖 **BOT ACTIVO ✅**\n\nEl bot se ha iniciado correctamente, pero no se pudo obtener el análisis preliminar de BTC.")
    except Exception as e:
        enviar_telegram(f"🤖 **BOT ACTIVO ✅**\n\nEl bot se ha iniciado correctamente (Error al consultar BTC: {e})")

    logging.info("🚀 Bot actualizado, concurrente y listo.")
    
    analizar_mercado()
    
    while True:
        time.sleep(7200)
        try:
            with open(lock_file, "w") as f:
                f.write(str(os.getpid()))
        except Exception:
            pass
        analizar_mercado()
