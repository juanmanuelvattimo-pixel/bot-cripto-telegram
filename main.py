import time
import requests
import ccxt
import pandas as pd
import ta
import threading
import os
import sys

# ==========================================
# 1. CONFIGURACIÓN DE TELEGRAM Y FILTRO MULTI-MENSAJE
# ==========================================
TELEGRAM_TOKEN = "8810680096:AAGPSrNFFWpbUHuj0laurGLxuepKIZDexys"
CHAT_ID = "1473411725"

historial_mensajes_enviados = []
tiempo_ultimo_envio = 0

def enviar_telegram(mensaje):
    global historial_mensajes_enviados, tiempo_ultimo_envio
    if not mensaje or not mensaje.strip():
        return
    
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
        if res.status_code == 200:
            tiempo_ultimo_envio = time.time()
            historial_mensajes_enviados.append(mensaje)
            if len(historial_mensajes_enviados) > 15:
                historial_mensajes_enviados.pop(0)
    except Exception as e:
        print(f"Error enviando mensaje a Telegram: {e}")

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
    
    for i in range(2, len(df) - 2):
        if df['high'].iloc[i] > df['high'].iloc[i-1] and df['high'].iloc[i] > df['high'].iloc[i-2] and \
           df['high'].iloc[i] > df['high'].iloc[i+1] and df['high'].iloc[i] > df['high'].iloc[i+2]:
            pivots_high.append(df['high'].iloc[i])
            
        if df['low'].iloc[i] < df['low'].iloc[i-1] and df['low'].iloc[i] < df['low'].iloc[i-2] and \
           df['low'].iloc[i] < df['low'].iloc[i+1] and df['low'].iloc[i] < df['low'].iloc[i+2]:
            pivots_low.append(df['low'].iloc[i])
            
    por_encima = [p for p in pivots_high if p > precio_actual]
    resistencia = min(por_encima) if por_encima else df['high'].tail(30).max()
    
    por_debajo = [p for p in pivots_low if p < precio_actual]
    soporte = max(por_debajo) if por_debajo else df['low'].tail(30).min()
    
    return soporte, resistencia

# ==========================================
# NUEVO MÓDULO: REBOTE DE RANGO (CORREGIDO)
# ==========================================
def detectar_rebote_rango_avanzado(h1, h4=None):
    """
    Versión adaptada para recibir el diccionario de análisis h1.
    """
    if not h1:
        return None

    # Si el ADX es muy alto, hay tendencia fuerte y no es un rango
    if h1['adx'] > 32:
        return None
        
    precio_entrada = h1['precio']
    rsi = h1['rsi']
    soporte = h1['soporte']
    resistencia = h1['resistencia']
    atr = h1['atr']
    
    # Detección de rebote basada en RSI y cercanía a zona lateral
    if rsi < 48:
        stop_loss = soporte - (1.0 * atr) if soporte < precio_entrada else precio_entrada * 0.985
        tp1 = resistencia if resistencia > precio_entrada else precio_entrada * 1.02
        tp2 = tp1 * 1.01
        tp3 = tp1 * 1.02
        
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
        
    if rsi > 52:
        stop_loss = resistencia + (1.0 * atr) if resistencia > precio_entrada else precio_entrada * 1.015
        tp1 = soporte if soporte < precio_entrada else precio_entrada * 0.98
        tp2 = tp1 * 0.99
        tp3 = tp1 * 0.98
        
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
            'pct_tp2': abs((tp2 - precio_entrada)/precio_entrada)*100*10,
            'tp3': tp3,
            'pct_tp3': abs((tp3 - precio_entrada)/precio_entrada)*100*10,
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

        df['ema10'] = ta.trend.ema_indicator(df['close'], window=min(10, n_velas-1))
        df['ema20'] = ta.trend.ema_indicator(df['close'], window=min(20, n_velas-1))
        df['ema55'] = ta.trend.ema_indicator(df['close'], window=min(55, n_velas-1))
        
        df['rsi'] = ta.momentum.rsi(df['close'], window=min(14, n_velas-1))
        df['mfi'] = ta.volume.money_flow_index(df['high'], df['low'], df['close'], df['volume'], window=min(14, n_velas-1))
        
        adx_ind = ta.trend.ADXIndicator(df['high'], df['low'], df['close'], window=min(14, n_velas-1))
        df['adx'] = adx_ind.adx()
        df['plus_di'] = adx_ind.adx_pos()
        df['minus_di'] = adx_ind.adx_neg()
        
        df['atr'] = ta.volatility.average_true_range(df['high'], df['low'], df['close'], window=min(14, n_velas-1))
        multiplicador = 2.0
        hl2 = (df['high'] + df['low']) / 2
        df['up_basic'] = hl2 - (multiplicador * df['atr'])
        df['dn_basic'] = hl2 + (multiplicador * df['atr'])
        
        df['supertrend_direction'] = 1
        for i in range(1, len(df)):
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
        st_dir_prev = df['supertrend_direction'].iloc[-2] if len(df['supertrend_direction']) > 1 else st_dir
        
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
            'cierra_arriba_ema10': precio > df['ema10'].iloc[-1],
            'cierra_abajo_ema10': precio < df['ema10'].iloc[-1],
            'soporte': soporte_key,
            'resistencia': resistencia_key,
            'fibo_long': {'tp1': fibo_tp1_long, 'tp2': fibo_tp2_long, 'tp3': fibo_tp3_long},
            'fibo_short': {'tp1': fibo_tp1_short, 'tp2': fibo_tp2_short, 'tp3': fibo_tp3_short}
        }
    except Exception:
        return None

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
    except Exception:
        return []

def evaluar_trade_manual(ticker_raw):
    ticker = ticker_raw.upper().replace("$", "").replace("USDT", "") + "/USDT"
    simbolo_limpio = ticker.split('/')[0]
    
    temporalidades = ['1h', '4h', '1d', '1w']
    analisis_tf = {}
    
    for tf in temporalidades:
        res = analizar_par_completo(ticker, tf)
        if res is None:
            enviar_telegram(f"❌ No se pudo encontrar o analizar la cripto `{ticker_raw}` en BingX.")
            return
        analisis_tf[tf] = res

    h1 = analisis_tf['1h']
    h4 = analisis_tf['4h']
    d1 = analisis_tf['1d']
    w1 = analisis_tf['1w']
    
    precio_act = h1['precio']
    atr_act = h1['atr']
    
    adx_aprobado = h1['adx'] >= 26          
    rsi_long_valido = h1['rsi'] < 70
    rsi_short_valido = h1['rsi'] > 30

    # Gatillos optimizados basados en estado activo de SuperTrend
    gatillo_long_10x = (
        d1['es_alcista'] and h4['es_alcista'] and
        adx_aprobado and rsi_long_valido and
        (h1['supertrend_estado'] == "🟢 ALCISTA") and
        h1['cierra_arriba_ema10']
    )

    gatillo_short_10x = (
        d1['es_bajista'] and h4['es_bajista'] and
        adx_aprobado and rsi_short_valido and
        (h1['supertrend_estado'] == "🔴 BAJISTA") and
        h1['cierra_abajo_ema10']
    )

    h4_rsi_valido = h4['rsi'] < 70
    h4_adx_valido = h4['adx'] >= 26
    gatillo_spot = (
        w1['es_alcista'] and d1['es_alcista'] and
        h4_adx_valido and h4_rsi_valido and
        (h4['supertrend_estado'] == "🟢 ALCISTA") and
        h4['cierra_arriba_ema10']
    )

    msj = f"🎯 *EVALUACIÓN MANUAL DE TRADE: ${simbolo_limpio}*\n\n"

    if gatillo_long_10x:
        sl_tecnico = h1['soporte'] - (1.5 * atr_act)
        sl_max_10x = precio_act * 0.965
        sl_final = max(sl_tecnico, sl_max_10x)
        pct_sl = abs((precio_act - sl_final) / precio_act) * 100 * 10
        
        resistencia_objetivo = h1['resistencia'] if h1['resistencia'] > precio_act else (precio_act + (atr_act * 3))
        fibo = h1['fibo_long']
        
        tp1 = min(resistencia_objetivo, max(fibo['tp1'], precio_act + (atr_act * 1.5)))
        tp2 = max(fibo['tp2'], tp1 * 1.015)
        tp3 = max(fibo['tp3'], tp2 * 1.015)

        riesgo = precio_act - sl_final
        beneficio = tp1 - precio_act
        
        if riesgo > 0 and (beneficio / riesgo) >= 1.3:
            msj += f"🟢 *ESTRATEGIA SNIPER 10X LONG: APROBADA* _(R:R 1:{(beneficio/riesgo):.1f})_\n"
            msj += f"🔮 *SuperTrend:* `{h1['supertrend_estado']}`\n"
            msj += f"💵 *Entrada:* `{precio_act:.4f}`\n"
            msj += f"🛑 *Stop Loss:* `{sl_final:.4f}` _(-{pct_sl:.1f}% en 10x)_\n"
            msj += f"🎯 *TP1:* `{tp1:.4f}` _(+{abs((tp1 - precio_act)/precio_act)*100*10:.1f}% en 10x)_\n"
            msj += f"🎯 *TP2:* `{tp2:.4f}` _(+{abs((tp2 - precio_act)/precio_act)*100*10:.1f}% en 10x)_\n"
            msj += f"🎯 *TP3:* `{tp3:.4f}` _(+{abs((tp3 - precio_act)/precio_act)*100*10:.1f}% en 10x)_\n\n"
    else:
        msj += "⚪ *SNIPER 10X LONG:* No cumple con todas las reglas actuales.\n\n"

    if gatillo_short_10x:
        sl_tecnico = h1['resistencia'] + (1.5 * atr_act)
        sl_max_10x = precio_act * 1.035
        sl_final = min(sl_tecnico, sl_max_10x)
        pct_sl = abs((sl_final - precio_act) / precio_act) * 100 * 10
        
        soporte_objetivo = h1['soporte'] if h1['soporte'] < precio_act else (precio_act - (atr_act * 3))
        fibo = h1['fibo_short']
        
        tp1 = max(soporte_objetivo, min(fibo['tp1'], precio_act - (atr_act * 1.5)))
        tp2 = min(fibo['tp2'], tp1 * 0.985)
        tp3 = min(fibo['tp3'], tp2 * 0.985)

        riesgo = sl_final - precio_act
        beneficio = precio_act - tp1

        if riesgo > 0 and (beneficio / riesgo) >= 1.3:
            msj += f"🔴 *ESTRATEGIA SNIPER 10X SHORT: APROBADA* _(R:R 1:{(beneficio/riesgo):.1f})_\n"
            msj += f"🔮 *SuperTrend:* `{h1['supertrend_estado']}`\n"
            msj += f"💵 *Entrada:* `{precio_act:.4f}`\n"
            msj += f"🛑 *Stop Loss:* `{sl_final:.4f}` _(-{pct_sl:.1f}% en 10x)_\n"
            msj += f"🎯 *TP1:* `{tp1:.4f}` _(+{abs((precio_act - tp1)/precio_act)*100*10:.1f}% en 10x)_\n"
            msj += f"🎯 *TP2:* `{tp2:.4f}` _(+{abs((precio_act - tp2)/precio_act)*100*10:.1f}% en 10x)_\n"
            msj += f"🎯 *TP3:* `{tp3:.4f}` _(+{abs((precio_act - tp3)/precio_act)*100*10:.1f}% en 10x)_\n\n"
    else:
        msj += "⚪ *SNIPER 10X SHORT:* No cumple con todas las reglas actuales.\n\n"

    h4_precio = h4['precio']
    h4_atr = h4['atr']
    if gatillo_spot:
        sl_spot = h4['soporte'] - (1.5 * h4_atr)
        pct_sl_spot = abs((h4_precio - sl_spot) / h4_precio) * 100
        
        resistencia_spot = h4['resistencia'] if h4['resistencia'] > h4_precio else (h4_precio + (h4_atr * 4))
        fibo_s = h4['fibo_long']
        
        tp1_s = min(resistencia_spot, max(fibo_s['tp1'], h4_precio + (h4_atr * 2)))
        tp2_s = max(fibo_s['tp2'], tp1_s * 1.02)
        tp3_s = max(fibo_s['tp3'], tp2_s * 1.02)

        riesgo_s = h4_precio - sl_spot
        beneficio_s = tp1_s - h4_precio
        
        if riesgo_s > 0 and (beneficio_s / riesgo_s) >= 1.3:
            msj += f"🎯 *ESTRATEGIA SNIPER SPOT: APROBADA* _(R:R 1:{(beneficio_s/riesgo_s):.1f})_\n"
            msj += f"🔮 *SuperTrend (4H):* `{h4['supertrend_estado']}`\n"
            msj += f"💵 *Precio Entrada:* `{h4_precio:.4f}`\n"
            msj += f"🛑 *Stop Loss:* `{sl_spot:.4f}` _(-{pct_sl_spot:.1f}%)_\n"
            msj += f"🎯 *TP1:* `{tp1_s:.4f}` _(+{abs((tp1_s - h4_precio)/h4_precio)*100:.1f}%)_\n"
            msj += f"🎯 *TP2:* `{tp2_s:.4f}` _(+{abs((tp2_s - h4_precio)/h4_precio)*100:.1f}%)_\n"
            msj += f"🎯 *TP3:* `{tp3_s:.4f}` _(+{abs((tp3_s - h4_precio)/h4_precio)*100:.1f}%)_\n"
    else:
        msj += "⚪ *SNIPER SPOT:* No califica para trade en este momento.\n"

    res_rebote = detectar_rebote_rango_avanzado(h1, h4)
    if res_rebote is not None:
        for r in res_rebote:
            msj += f"\n⚡ *{r['tipo']}*\n"
            msj += f"💵 *Entrada:* `{precio_act:.4f}`\n"
            msj += f"🛑 *Stop Loss:* `{r['sl']:.4f}`\n"
            msj += f"🎯 *TP1:* `{r['tp1']:.4f}`\n"

    enviar_telegram(msj)

# ==========================================
# 6. ESCANEO RÁPIDO BAJO DEMANDA (/comprobar)
# ==========================================
def escanear_senales_sniper_manual():
    enviar_telegram("🔍 Escaneando todo el mercado en busca de entradas Sniper y Rangos activos...")
    
    pares_filtrados = obtener_pares_top()
    if not pares_filtrados:
        enviar_telegram("❌ Error al obtener los pares del mercado en este momento.")
        return

    entradas_sniper = []
    entradas_sniper_spot = []
    entradas_rango = []
    temporalidades = ['1h', '4h', '1d', '1w']

    for par in pares_filtrados:
        analisis_tf = {}
        for tf in temporalidades:
            res = analizar_par_completo(par, tf)
            if res is not None:
                analisis_tf[tf] = res
        
        if '1h' not in analisis_tf or '4h' not in analisis_tf:
            continue
            
        simbolo_limpio = par.split('/')[0]
        h1 = analisis_tf['1h']
        h4 = analisis_tf['4h']
        d1 = analisis_tf.get('1d')
        w1 = analisis_tf.get('1w')
        
        precio_act = h1['precio']
        atr_act = h1['atr']
        
        # --- DETECCIÓN DE RANGO ---
        senales_rango = detectar_rebote_rango_avanzado(h1, h4)
        if senales_rango:
            for sr in senales_rango:
                entradas_rango.append({
                    'symbol': simbolo_limpio,
                    'tipo': sr['tipo'],
                    'precio': precio_act,
                    'sl': sr['sl'],
                    'pct_sl': sr['pct_sl'],
                    'tp1': sr['tp1'],
                    'pct_tp1': sr['pct_tp1'],
                    'tp2': sr['tp2'],
                    'pct_tp2': sr['pct_tp2'],
                    'tp3': sr['tp3'],
                    'pct_tp3': sr['pct_tp3'],
                    'rr': sr['rr']
                })
        
        # Sniper 10X
        if d1 is not None:
            adx_aprobado = h1['adx'] >= 26         
            rsi_long_valido = h1['rsi'] < 70
            rsi_short_valido = h1['rsi'] > 30

            gatillo_long_10x = (
                d1['es_alcista'] and h4['es_alcista'] and
                adx_aprobado and rsi_long_valido and
                (h1['supertrend_estado'] == "🟢 ALCISTA") and
                h1['cierra_arriba_ema10']
            )

            if gatillo_long_10x:
                sl_tecnico = h1['soporte'] - (1.5 * atr_act)
                sl_max_10x = precio_act * 0.965
                sl_final = max(sl_tecnico, sl_max_10x)
                pct_sl = abs((precio_act - sl_final) / precio_act) * 100 * 10
                
                resistencia_objetivo = h1['resistencia'] if h1['resistencia'] > precio_act else (precio_act + (atr_act * 3))
                fibo = h1['fibo_long']
                
                tp1 = min(resistencia_objetivo, max(fibo['tp1'], precio_act + (atr_act * 1.5)))
                tp2 = max(fibo['tp2'], tp1 * 1.015)
                tp3 = max(fibo['tp3'], tp2 * 1.015)

                riesgo = precio_act - sl_final
                beneficio = tp1 - precio_act
                
                if riesgo > 0 and (beneficio / riesgo) >= 1.3:
                    entradas_sniper.append({
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
                h1['cierra_abajo_ema10']
            )

            if gatillo_short_10x:
                sl_tecnico = h1['resistencia'] + (1.5 * atr_act)
                sl_max_10x = precio_act * 1.035
                sl_final = min(sl_tecnico, sl_max_10x)
                pct_sl = abs((sl_final - precio_act) / precio_act) * 100 * 10
                
                soporte_objetivo = h1['soporte'] if h1['soporte'] < precio_act else (precio_act - (atr_act * 3))
                fibo = h1['fibo_short']
                
                tp1 = max(soporte_objetivo, min(fibo['tp1'], precio_act - (atr_act * 1.5)))
                tp2 = min(fibo['tp2'], tp1 * 0.985)
                tp3 = min(fibo['tp3'], tp2 * 0.985)

                riesgo = sl_final - precio_act
                beneficio = precio_act - tp1

                if riesgo > 0 and (beneficio / riesgo) >= 1.3:
                    entradas_sniper.append({
                        'symbol': simbolo_limpio, 'tipo': 'SHORT 🔴',
                        'precio': precio_act, 'sl': sl_final, 'pct_sl': pct_sl,
                        'tp1': tp1, 'pct_tp1': abs((precio_act - tp1)/precio_act)*100*10,
                        'tp2': tp2, 'pct_tp2': abs((tp2 - precio_act)/precio_act)*100*10,
                        'tp3': tp3, 'pct_tp3': abs((tp3 - precio_act)/precio_act)*100*10,
                        'supertrend': h1['supertrend_estado'],
                        'rr': f"1:{(beneficio/riesgo):.1f}"
                    })

        # Sniper Spot
        if w1 is not None and d1 is not None:
            h4_precio = h4['precio']
            h4_atr = h4['atr']
            h4_rsi_valido = h4['rsi'] < 70
            h4_adx_valido = h4['adx'] >= 26

            gatillo_spot = (
                w1['es_alcista'] and d1['es_alcista'] and
                h4_adx_valido and h4_rsi_valido and
                (h4['supertrend_estado'] == "🟢 ALCISTA") and
                h4['cierra_arriba_ema10']
            )

            if gatillo_spot:
                sl_spot = h4['soporte'] - (1.5 * h4_atr)
                pct_sl_spot = abs((h4_precio - sl_spot) / h4_precio) * 100
                
                resistencia_spot = h4['resistencia'] if h4['resistencia'] > h4_precio else (h4_precio + (h4_atr * 4))
                fibo_s = h4['fibo_long']
                
                tp1_s = min(resistencia_spot, max(fibo_s['tp1'], h4_precio + (h4_atr * 2)))
                tp2_s = max(fibo_s['tp2'], tp1_s * 1.02)
                tp3_s = max(fibo_s['tp3'], tp2_s * 1.02)

                riesgo_s = h4_precio - sl_spot
                beneficio_s = tp1_s - h4_precio
                
                if riesgo_s > 0 and (beneficio_s / riesgo_s) >= 1.3:
                    entradas_sniper_spot.append({
                        'symbol': simbolo_limpio,
                        'precio': h4_precio, 'sl': sl_spot, 'pct_sl': pct_sl_spot,
                        'tp1': tp1_s, 'pct_tp1': abs((tp1_s - h4_precio)/h4_precio)*100,
                        'tp2': tp2_s, 'pct_tp2': abs((tp2_s - h4_precio)/h4_precio)*100,
                        'tp3': tp3_s, 'pct_tp3': abs((tp3_s - h4_precio)/h4_precio)*100,
                        'supertrend': h4['supertrend_estado'],
                        'rr': f"1:{(beneficio_s/riesgo_s):.1f}"
                    })

    if not entradas_sniper and not entradas_sniper_spot and not entradas_rango:
        enviar_telegram("❌ *NO HAY ENTRADAS ACTIVAS*\n\nEn este momento ninguna criptomoneda cumple con las condiciones estrictas de tendencia o rangos.")
        return

    if entradas_rango:
        msj_rango = "⚡ *REBOTES EN RANGO DETECTADOS:* ⚡\n\n"
        for op in entradas_rango[:5]:
            msj_rango += f"🪙 *{op['symbol']}* -> *{op['tipo']}* _(R:R {op['rr']})_\n"
            msj_rango += f"💵 *Entrada:* `{op['precio']:.4f}`\n"
            msj_rango += f"🛑 *Stop Loss:* `{op['sl']:.4f}` _(-{op['pct_sl']:.1f}% en 10x)_\n"
            msj_rango += f"🎯 *TP1:* `{op['tp1']:.4f}` _(+{op['pct_tp1']:.1f}% en 10x)_\n\n"
        enviar_telegram(msj_rango)

    if entradas_sniper:
        msj_sniper = "⚡ *ENTRADAS SNIPER 10X DETECTADAS:* ⚡\n\n"
        for op in entradas_sniper[:5]:
            msj_sniper += f"🪙 *{op['symbol']}* -> *{op['tipo']}* _(R:R {op['rr']})_\n"
            msj_sniper += f"🔮 *SuperTrend:* `{op['supertrend']}`\n"
            msj_sniper += f"💵 *Entrada:* `{op['precio']:.4f}`\n"
            msj_sniper += f"🛑 *Stop Loss:* `{op['sl']:.4f}` _(-{op['pct_sl']:.1f}% en 10x)_\n"
            msj_sniper += f"🎯 *TP1:* `{op['tp1']:.4f}` _(+{op['pct_tp1']:.1f}% en 10x)_\n\n"
        enviar_telegram(msj_sniper)

    if entradas_sniper_spot:
        msj_spot = "🎯 *ENTRADAS SNIPER SPOT DETECTADAS:* 🎯\n\n"
        for op in entradas_sniper_spot[:5]:
            msj_spot += f"🪙 *{op['symbol']}* -> *LONG SPOT 🟢* _(R:R {op['rr']})_\n"
            msj_spot += f"🔮 *SuperTrend:* `{op['supertrend']}`\n"
            msj_spot += f"💵 *Precio Entrada:* `{op['precio']:.4f}`\n"
            msj_spot += f"🛑 *Stop Loss:* `{op['sl']:.4f}` _(-{op['pct_sl']:.1f}%)_\n"
            msj_spot += f"🎯 *TP1:* `{op['tp1']:.4f}` _(+{op['pct_tp1']:.1f}%)_\n\n"
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
                            enviar_telegram(f"⏳ Realizando análisis exhaustivo para `${ticker.upper()}`...")
                            analizar_cripto_individual(ticker)
                        else:
                            enviar_telegram("ℹ️ Indica la moneda. Ejemplo: `/analizar BTC`")
                            
                    elif text.startswith("/trade"):
                        partes = text.split()
                        if len(partes) > 1:
                            ticker = partes[1]
                            enviar_telegram(f"⏳ Evaluando estrategia Sniper y Rangos para `${ticker.upper()}`...")
                            evaluar_trade_manual(ticker)
                        else:
                            enviar_telegram("ℹ️ Indica la moneda. Ejemplo: `/trade BTC`")

                    elif text.startswith("/comprobar") or text.startswith("/senales"):
                        hilo_comprobar = threading.Thread(target=escanear_senales_sniper_manual, daemon=True)
                        hilo_comprobar.start()
        except Exception:
            pass
        time.sleep(1)

# ==========================================
# 8. ESCANEO Y CLASIFICACIÓN GENERAL (CADA 2 HORAS)
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

    print("🔎 Escaneando mercado...")
    
    try:
        pares_filtrados = obtener_pares_top()
        if not pares_filtrados:
            return
            
        longs_perfectos, longs_diario_semanal = [], []
        shorts_perfectos, shorts_diario_semanal = [], []
        entradas_sniper = []
        entradas_sniper_spot = []
        entradas_rango = []
        
        temporalidades = ['1h', '4h', '1d', '1w']

        for par in pares_filtrados:
            analisis_tf = {}
            es_valido = True
            
            for tf in temporalidades:
                res = analizar_par_completo(par, tf)
                if res is None:
                    es_valido = False
                    break
                analisis_tf[tf] = res
            
            if es_valido:
                simbolo_limpio = par.split('/')[0]
                
                estados = {
                    tf: "🟢" if analisis_tf[tf]['es_alcista'] else ("🔴" if analisis_tf[tf]['es_bajista'] else "⚪")
                    for tf in temporalidades
                }
                
                datos_par = {
                    'symbol': simbolo_limpio,
                    '1h': estados['1h'], '4h': estados['4h'], 
                    '1d': estados['1d'], '1w': estados['1w']
                }
                
                if all(v == "🟢" for v in estados.values()):
                    longs_perfectos.append(datos_par)
                elif estados['1d'] == "🟢" and estados['1w'] == "🟢":
                    longs_diario_semanal.append(datos_par)

                if all(v == "🔴" for v in estados.values()):
                    shorts_perfectos.append(datos_par)
                elif estados['1d'] == "🔴" and estados['1w'] == "🔴":
                    shorts_diario_semanal.append(datos_par)

                h1 = analisis_tf['1h']
                h4 = analisis_tf['4h']
                d1 = analisis_tf['1d']
                w1 = analisis_tf['1w']
                
                precio_act = h1['precio']
                atr_act = h1['atr']
                
                # --- DETECCIÓN DE RANGO ---
                senales_rango = detectar_rebote_rango_avanzado(h1, h4)
                if senales_rango:
                    for sr in senales_rango:
                        entradas_rango.append({
                            'symbol': simbolo_limpio, 'tipo': sr['tipo'],
                            'precio': precio_act, 'sl': sr['sl'], 'pct_sl': sr['pct_sl'],
                            'tp1': sr['tp1'], 'pct_tp1': sr['pct_tp1'], 'rr': sr['rr']
                        })
                
                adx_aprobado = h1['adx'] >= 26         
                rsi_long_valido = h1['rsi'] < 70
                rsi_short_valido = h1['rsi'] > 30

                gatillo_long_10x = (
                    d1['es_alcista'] and h4['es_alcista'] and
                    adx_aprobado and rsi_long_valido and
                    (h1['supertrend_estado'] == "🟢 ALCISTA") and
                    h1['cierra_arriba_ema10']
                )

                if gatillo_long_10x:
                    sl_tecnico = h1['soporte'] - (1.5 * atr_act)
                    sl_max_10x = precio_act * 0.965
                    sl_final = max(sl_tecnico, sl_max_10x)
                    pct_sl = abs((precio_act - sl_final) / precio_act) * 100 * 10
                    
                    resistencia_objetivo = h1['resistencia'] if h1['resistencia'] > precio_act else (precio_act + (atr_act * 3))
                    fibo = h1['fibo_long']
                    tp1 = min(resistencia_objetivo, max(fibo['tp1'], precio_act + (atr_act * 1.5)))

                    riesgo = precio_act - sl_final
                    beneficio = tp1 - precio_act
                    
                    if riesgo > 0 and (beneficio / riesgo) >= 1.3:
                        entradas_sniper.append({
                            'symbol': simbolo_limpio, 'tipo': 'LONG 🟢',
                            'precio': precio_act, 'sl': sl_final, 'pct_sl': pct_sl,
                            'tp1': tp1, 'supertrend': h1['supertrend_estado'],
                            'rr': f"1:{(beneficio/riesgo):.1f}"
                        })

                gatillo_short_10x = (
                    d1['es_bajista'] and h4['es_bajista'] and
                    adx_aprobado and rsi_short_valido and
                    (h1['supertrend_estado'] == "🔴 BAJISTA") and
                    h1['cierra_abajo_ema10']
                )

                if gatillo_short_10x:
                    sl_tecnico = h1['resistencia'] + (1.5 * atr_act)
                    sl_max_10x = precio_act * 1.035
                    sl_final = min(sl_tecnico, sl_max_10x)
                    pct_sl = abs((sl_final - precio_act) / precio_act) * 100 * 10
                    
                    soporte_objetivo = h1['soporte'] if h1['soporte'] < precio_act else (precio_act - (atr_act * 3))
                    fibo = h1['fibo_short']
                    tp1 = max(soporte_objetivo, min(fibo['tp1'], precio_act - (atr_act * 1.5)))

                    riesgo = sl_final - precio_act
                    beneficio = precio_act - tp1

                    if riesgo > 0 and (beneficio / riesgo) >= 1.3:
                        entradas_sniper.append({
                            'symbol': simbolo_limpio, 'tipo': 'SHORT 🔴',
                            'precio': precio_act, 'sl': sl_final, 'pct_sl': pct_sl,
                            'tp1': tp1, 'supertrend': h1['supertrend_estado'],
                            'rr': f"1:{(beneficio/riesgo):.1f}"
                        })

                h4_precio = h4['precio']
                h4_atr = h4['atr']
                h4_rsi_valido = h4['rsi'] < 70
                h4_adx_valido = h4['adx'] >= 26

                gatillo_spot = (
                    w1['es_alcista'] and d1['es_alcista'] and
                    h4_adx_valido and h4_rsi_valido and
                    (h4['supertrend_estado'] == "🟢 ALCISTA") and
                    h4['cierra_arriba_ema10']
                )

                if gatillo_spot:
                    sl_spot = h4['soporte'] - (1.5 * h4_atr)
                    pct_sl_spot = abs((h4_precio - sl_spot) / h4_precio) * 100
                    
                    resistencia_spot = h4['resistencia'] if h4['resistencia'] > h4_precio else (h4_precio + (h4_atr * 4))
                    fibo_s = h4['fibo_long']
                    tp1_s = min(resistencia_spot, max(fibo_s['tp1'], h4_precio + (h4_atr * 2)))

                    riesgo_s = h4_precio - sl_spot
                    beneficio_s = tp1_s - h4_precio
                    
                    if riesgo_s > 0 and (beneficio_s / riesgo_s) >= 1.3:
                        entradas_sniper_spot.append({
                            'symbol': simbolo_limpio,
                            'precio': h4_precio, 'sl': sl_spot, 'pct_sl': pct_sl_spot,
                            'tp1': tp1_s, 'supertrend': h4['supertrend_estado'],
                            'rr': f"1:{(beneficio_s/riesgo_s):.1f}"
                        })

        if entradas_rango:
            msj_rango = "⚡ *REBOTES EN RANGO DETECTADOS:* ⚡\n\n"
            for op in entradas_rango[:5]:
                msj_rango += f"🪙 *{op['symbol']}* -> *{op['tipo']}* _(R:R {op['rr']})_\n"
                msj_rango += f"💵 *Entrada:* `{op['precio']:.4f}`\n"
                msj_rango += f"🛑 *Stop Loss:* `{op['sl']:.4f}` _(-{op['pct_sl']:.1f}% en 10x)_\n\n"
            enviar_telegram(msj_rango)

        if entradas_sniper:
            msj_sniper = "⚡ *ENTRADAS SNIPER 10X DETECTADAS:* ⚡\n\n"
            for op in entradas_sniper[:5]:
                msj_sniper += f"🪙 *{op['symbol']}* -> *{op['tipo']}* _(R:R {op['rr']})_\n"
                msj_sniper += f"🔮 *SuperTrend:* `{op['supertrend']}`\n"
                msj_sniper += f"💵 *Entrada:* `{op['precio']:.4f}`\n\n"
            enviar_telegram(msj_sniper)

        if entradas_sniper_spot:
            msj_spot = "🎯 *ENTRADAS SNIPER SPOT DETECTADAS:* 🎯\n\n"
            for op in entradas_sniper_spot[:5]:
                msj_spot += f"🪙 *{op['symbol']}* -> *LONG SPOT 🟢* _(R:R {op['rr']})_\n"
                msj_spot += f"🔮 *SuperTrend:* `{op['supertrend']}`\n"
                msj_spot += f"💵 *Precio Entrada:* `{op['precio']:.4f}`\n\n"
            enviar_telegram(msj_spot)

        print("✅ Escaneo completado.")

    except Exception as e:
        print(f"Error en el escaneo general: {e}")

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
    
    print("🚀 Bot actualizado y listo para enviar alertas de Rangos, Sniper 10X y Spot.")
    
    analizar_mercado()
    
    while True:
        time.sleep(7200)
        try:
            with open(lock_file, "w") as f:
                f.write(str(os.getpid()))
        except Exception:
            pass
        analizar_mercado()
