import time
import requests
import ccxt
import pandas as pd
import ta
import threading

# ==========================================
# 1. CONFIGURACIÓN DE TELEGRAM
# ==========================================
TELEGRAM_TOKEN = "8810680096:AAGPSrNFFWpbUHuj0laurGLxuepKIZDexys"
CHAT_ID = "1473411725"

def enviar_telegram(mensaje):
    if not mensaje or not mensaje.strip():
        return
    
    if len(mensaje) > 3500:
        mensaje = mensaje[:3500] + "\n\n⚠️ _(Mensaje recortado por tamaño)_"

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {
            "chat_id": CHAT_ID,
            "text": mensaje,
            "parse_mode": "Markdown"
        }
        requests.post(url, data=data, timeout=10)
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
# 4. MOTOR DE ANÁLISIS MEJORADO Y EXHAUSTIVO
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

        # 1. EMAs principales
        df['ema10'] = ta.trend.ema_indicator(df['close'], window=min(10, n_velas-1))
        df['ema20'] = ta.trend.ema_indicator(df['close'], window=min(20, n_velas-1))
        df['ema55'] = ta.trend.ema_indicator(df['close'], window=min(55, n_velas-1))
        
        # 2. RSI & MFI (Money Flow Index - Flujo de Capital de Cipher B)
        df['rsi'] = ta.momentum.rsi(df['close'], window=min(14, n_velas-1))
        df['mfi'] = ta.volume.money_flow_index(df['high'], df['low'], df['close'], df['volume'], window=min(14, n_velas-1))
        
        # 3. Volumen Institucional (Volumen vs EMA de Volumen)
        df['vol_ema'] = ta.trend.ema_indicator(df['volume'], window=min(20, n_velas-1))
        volumen_alto = df['volume'].iloc[-1] > df['vol_ema'].iloc[-1]
        
        # 4. ADX & Direccionalidad (+DI / -DI)
        adx_ind = ta.trend.ADXIndicator(df['high'], df['low'], df['close'], window=min(14, n_velas-1))
        df['adx'] = adx_ind.adx()
        df['plus_di'] = adx_ind.adx_pos()
        df['minus_di'] = adx_ind.adx_neg()
        
        # 5. Cipher B / WaveTrend
        ap3 = (df['high'] + df['low'] + df['close']) / 3
        esa = ta.trend.ema_indicator(ap3, window=min(10, n_velas-1))
        d = ta.trend.ema_indicator((ap3 - esa).abs(), window=min(10, n_velas-1))
        ci = (ap3 - esa) / (0.015 * d)
        df['wt1'] = ta.trend.ema_indicator(ci, window=min(21, n_velas-1))
        df['wt2'] = ta.trend.sma_indicator(df['wt1'], window=min(4, len(df['wt1'].dropna())-1))
        
        # 6. Oracle Ribbon
        df['oracle_fast'] = ta.trend.ema_indicator(df['close'], window=min(8, n_velas-1))
        df['oracle_slow'] = ta.trend.ema_indicator(df['close'], window=min(13, n_velas-1))
        df['oracle_ribbon'] = df['oracle_fast'] > df['oracle_slow']
        
        # 7. Volatilidad (ATR)
        df['atr'] = ta.volatility.average_true_range(df['high'], df['low'], df['close'], window=min(14, n_velas-1))

        e10, e20, e55 = df['ema10'].iloc[-1], df['ema20'].iloc[-1], df['ema55'].iloc[-1]
        wt1, wt2 = df['wt1'].iloc[-1], df['wt2'].iloc[-1]
        wt1_prev, wt2_prev = df['wt1'].iloc[-2], df['wt2'].iloc[-2]
        adx = df['adx'].iloc[-1]
        plus_di = df['plus_di'].iloc[-1]
        minus_di = df['minus_di'].iloc[-1]
        rsi = df['rsi'].iloc[-1]
        mfi = df['mfi'].iloc[-1]
        atr = df['atr'].iloc[-1] if not df['atr'].empty else (precio * 0.02)
        
        oracle_actual = df['oracle_ribbon'].iloc[-1]
        oracle_previo = df['oracle_ribbon'].iloc[-2] if len(df['oracle_ribbon']) > 1 else oracle_actual
        
        oracle_buy = (not oracle_previo) and oracle_actual
        oracle_sell = oracle_previo and (not oracle_actual)

        soporte_key, resistencia_key = calcular_soportes_resistencias(df, precio)

        # Cálculo Fibonacci
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

        # Evaluación de sistema de puntos (Exigiendo volumen y MFI)
        puntos_alcistas = sum([precio > e55, e10 > e20, wt1 > wt2, oracle_actual, mfi > 50])
        puntos_bajistas = sum([precio <= e55, e10 <= e20, wt1 <= wt2, not oracle_actual, mfi <= 50])

        adx_direccion = "ALCISTA 🟢" if plus_di > minus_di else "BAJISTA 🔴"
        adx_fuerza = "Fuerte 💪" if adx >= 23 else "Débil / Rango 😴"

        return {
            'precio': precio,
            'atr': atr,
            'rsi': rsi,
            'mfi': mfi,
            'adx': adx,
            'adx_direccion': adx_direccion,
            'adx_fuerza': adx_fuerza,
            'volumen_alto': volumen_alto,
            'es_alcista': puntos_alcistas >= 4,
            'es_bajista': puntos_bajistas >= 4,
            'cruce_alcista': (wt1_prev <= wt2_prev) and (wt1 > wt2),
            'cruce_bajista': (wt1_prev >= wt2_prev) and (wt1 < wt2),
            'oracle_buy': oracle_buy,
            'oracle_sell': oracle_sell,
            'oracle_estado': "🟢 COMPRA" if oracle_actual else "🔴 VENTA",
            'cipher_estado': "🟢 COMPRA" if wt1 > wt2 else "🔴 VENTA",
            'soporte': soporte_key,
            'resistencia': resistencia_key,
            'fibo_long': {'tp1': fibo_tp1_long, 'tp2': fibo_tp2_long, 'tp3': fibo_tp3_long},
            'fibo_short': {'tp1': fibo_tp1_short, 'tp2': fibo_tp2_short, 'tp3': fibo_tp3_short}
        }
    except Exception:
        return None

# ==========================================
# 5. CONSULTA INDIVIDUAL ULTRA EXHAUSTIVA (/analizar)
# ==========================================
def analizar_cripto_individual(ticker_raw):
    ticker = ticker_raw.upper().replace("$", "").replace("USDT", "") + "/USDT"
    
    temporalidades = ['1w', '1d', '4h', '1h']
    analisis_tf = {}
    
    for tf in temporalidades:
        res = analizar_par_completo(ticker, tf)
        if res is None:
            enviar_telegram(f"❌ No se pudo encontrar o analizar la cripto `{ticker_raw}` en BingX.")
            return
        analisis_tf[tf] = res

    h1 = analisis_tf['1h']
    precio_act = h1['precio']
    simbolo = ticker.split('/')[0]
    
    msj = f"🔬 *ANÁLISIS COMPLETO SPOT: ${simbolo}*\n"
    msj += f"💵 *Precio Actual:* `{precio_act:.4f}` USDT\n"
    msj += f"🧱 *Soporte Key 1H:* `{h1['soporte']:.4f}`\n"
    msj += f"🎯 *Resistencia Key 1H:* `{h1['resistencia']:.4f}`\n\n"
    msj += "📌 *DESGLOSE MULTI-TEMPORAL:*\n\n"
    
    for tf in temporalidades:
        d = analisis_tf[tf]
        
        if d['es_alcista']:
            tendencia = "🟢 ALCISTA FUERTE"
        elif d['es_bajista']:
            tendencia = "🔴 BAJISTA FUERTE"
        else:
            tendencia = "⚪ NEUTRA / RANGO"
            
        vol_icon = "🔥 Alto" if d['volumen_alto'] else "💤 Normal/Bajo"
        
        msj += f"⏱️ *TEMPORALIDAD {tf.upper()}*\n"
        msj += f"• *Tendencia:* {tendencia}\n"
        msj += f"• *Oracle Ribbon:* `{d['oracle_estado']}`\n"
        msj += f"• *Cipher B (Momentum):* `{d['cipher_estado']}`\n"
        msj += f"• *Flujo Dinero (MFI):* `{d['mfi']:.1f}` _({'🟢 Entrada Capital' if d['mfi'] > 50 else '🔴 Salida Capital'})_\n"
        msj += f"• *Fuerza (ADX):* `{d['adx']:.1f}` -> *{d['adx_direccion']}* _({d['adx_fuerza']})_\n"
        msj += f"• *Volumen:* {vol_icon}\n"
        msj += "-----------------------------------\n"

    enviar_telegram(msj)

# ==========================================
# 6. ESCUCHADOR DE COMANDOS TELEGRAM
# ==========================================
def escuchar_mensajes_telegram():
    offset = None
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    
    while True:
        try:
            params = {"timeout": 10, "offset": offset}
            resp = requests.get(url, params=params, timeout=12).json()
            
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
                            enviar_telegram("ℹ️ Indica la moneda. Ejemplo: `/analizar BTC` o `/analizar SOL`")
        except Exception:
            pass
        time.sleep(2)

# ==========================================
# 7. ESCANEO Y CLASIFICACIÓN GENERAL (SNIPER CON FILTRO R/R Y VOLUMEN)
# ==========================================
def analizar_mercado():
    print("🔎 Escaneando mercado (Sniper 10x)...")
    
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
        pares_filtrados = [item['symbol'] for item in pares_usdt[:150]]
        
        longs_perfectos, longs_diario_semanal = [], []
        shorts_perfectos, shorts_diario_semanal = [], []
        entradas_sniper = []
        
        temporalidades = ['1h', '4h', '1d', '1w']

        for count, par in enumerate(pares_filtrados, 1):
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

               # ==========================================
                # EVALUADOR SNIPER 10X (TPs ADAPTATIVOS POR ATR + ESTRUCTURA)
                # ==========================================
                h1 = analisis_tf['1h']
                precio_act = h1['precio']
                atr_act = h1['atr']
                adx_aprobado = h1['adx'] >= 20

                # LONG 10X SNIPER
                if estados['1d'] == "🟢" and estados['1w'] == "🟢" and estados['4h'] == "🟢" and adx_aprobado and (h1['oracle_buy'] or (h1['cruce_alcista'] and h1['oracle_estado'] == "🟢 COMPRA")):
                    # SL Técnico adaptado
                    sl_tecnico = min(precio_act - (1.2 * atr_act), h1['soporte'] * 0.998)
                    sl_max_10x = precio_act * 0.983
                    sl_final = max(sl_tecnico, sl_max_10x)
                    pct_sl = abs((precio_act - sl_final) / precio_act) * 100 * 10
                    
                    # Distancia de riesgo base
                    distancia_sl = precio_act - sl_final

                    # TPs EQUILIBRADOS:
                    # TP1: Mínimo 1.5x el SL o 1.5x ATR (Equilibrado para no quedar pegado)
                    tp1 = precio_act + max(distancia_sl * 1.5, atr_act * 1.5)
                    # TP2: Nivel estructural de resistencia cercana (o 2.5x ATR si la resistencia está muy lejos)
                    tp2 = min(h1['resistencia'], precio_act + (atr_act * 2.5))
                    if tp2 <= tp1: tp2 = tp1 + (atr_act * 1.0) # Asegurar escalonamiento
                    # TP3: Proyección runner (3.5x ATR)
                    tp3 = precio_act + (atr_act * 3.5)

                    # Validar Ratio Riesgo Beneficio (Mínimo 1:1.5 al TP1)
                    beneficio_tp1 = tp1 - precio_act
                    if distancia_sl > 0 and (beneficio_tp1 / distancia_sl) >= 1.5:
                        entradas_sniper.append({
                            'symbol': simbolo_limpio, 'tipo': 'LONG 🟢',
                            'precio': precio_act, 'sl': sl_final, 'pct_sl': pct_sl,
                            'tp1': tp1, 'pct_tp1': abs((tp1 - precio_act)/precio_act)*100*10,
                            'tp2': tp2, 'pct_tp2': abs((tp2 - precio_act)/precio_act)*100*10,
                            'tp3': tp3, 'pct_tp3': abs((tp3 - precio_act)/precio_act)*100*10,
                            'oracle': h1['oracle_estado'],
                            'rr': f"1:{(beneficio_tp1/distancia_sl):.1f}"
                        })

                # SHORT 10X SNIPER
                elif estados['1d'] == "🔴" and estados['1w'] == "🔴" and estados['4h'] == "🔴" and adx_aprobado and (h1['oracle_sell'] or (h1['cruce_bajista'] and h1['oracle_estado'] == "🔴 VENTA")):
                    # SL Técnico adaptado
                    sl_tecnico = max(precio_act + (1.2 * atr_act), h1['resistencia'] * 1.002)
                    sl_max_10x = precio_act * 1.017
                    sl_final = min(sl_tecnico, sl_max_10x)
                    pct_sl = abs((sl_final - precio_act) / precio_act) * 100 * 10
                    
                    # Distancia de riesgo base
                    distancia_sl = sl_final - precio_act

                    # TPs EQUILIBRADOS:
                    tp1 = precio_act - max(distancia_sl * 1.5, atr_act * 1.5)
                    tp2 = max(h1['soporte'], precio_act - (atr_act * 2.5))
                    if tp2 >= tp1: tp2 = tp1 - (atr_act * 1.0) # Asegurar escalonamiento
                    tp3 = precio_act - (atr_act * 3.5)

                    # Validar Ratio Riesgo Beneficio
                    beneficio_tp1 = precio_act - tp1
                    if distancia_sl > 0 and (beneficio_tp1 / distancia_sl) >= 1.5:
                        entradas_sniper.append({
                            'symbol': simbolo_limpio, 'tipo': 'SHORT 🔴',
                            'precio': precio_act, 'sl': sl_final, 'pct_sl': pct_sl,
                            'tp1': tp1, 'pct_tp1': abs((precio_act - tp1)/precio_act)*100*10,
                            'tp2': tp2, 'pct_tp2': abs((precio_act - tp2)/precio_act)*100*10,
                            'tp3': tp3, 'pct_tp3': abs((precio_act - tp3)/precio_act)*100*10,
                            'oracle': h1['oracle_estado'],
                            'rr': f"1:{(beneficio_tp1/distancia_sl):.1f}"
                        })

        def enviar_lista_telegram(titulo, descripcion, lista):
            if not lista:
                return
            mensaje = f"{titulo}\n_{descripcion}_\n\n"
            for i, res in enumerate(lista[:10], 1):
                mensaje += f"*{i}. {res['symbol']}*\n"
                mensaje += f"1H {res['1h']} | 4H {res['4h']} | 1D {res['1d']} | 1S {res['1w']}\n\n"
            
            enviar_telegram(mensaje)
            time.sleep(1.5)

        enviar_lista_telegram("🟢 *TOP PERFECCIÓN ALCISTA*", "EMA + Cipher B + Oracle + MFI (4H/1D/1W)", longs_perfectos)
        enviar_lista_telegram("📈 *TOP TENDENCIA ALCISTA (1D + 1S)*", "Tendencia Mayor Alcista Confirmada", longs_diario_semanal)
        enviar_lista_telegram("🔴 *TOP PERFECCIÓN BAJISTA*", "EMA + Cipher B + Oracle + MFI (4H/1D/1W)", shorts_perfectos)
        enviar_lista_telegram("📉 *TOP TENDENCIA BAJISTA (1D + 1S)*", "Tendencia Mayor Bajista Confirmada", shorts_diario_semanal)

        if entradas_sniper:
            msj_sniper = "⚡ *ENTRADAS SNIPER (FILTRADAS R:R Y VOLUMEN 10X)* ⚡\n\n"
            for op in entradas_sniper[:5]:
                msj_sniper += f"🪙 *{op['symbol']}* -> *{op['tipo']}* _(R:R {op['rr']})_\n"
                msj_sniper += f"🔮 *Oracle:* `{op['oracle']}`\n"
                msj_sniper += f"💵 *Entrada:* `{op['precio']:.4f}`\n"
                msj_sniper += f"🛑 *Stop Loss:* `{op['sl']:.4f}` _(-{op['pct_sl']:.1f}% en 10x)_\n"
                msj_sniper += f"🎯 *TP1 (Fibo):* `{op['tp1']:.4f}` _(+{op['pct_tp1']:.1f}% en 10x)_\n"
                msj_sniper += f"🎯 *TP2 (Retest):* `{op['tp2']:.4f}` _(+{op['pct_tp2']:.1f}% en 10x)_\n"
                msj_sniper += f"🎯 *TP3 (Runner):* `{op['tp3']:.4f}` _(+{op['pct_tp3']:.1f}% en 10x)_\n\n"
            
            msj_sniper += "💡 _¿Quieres consultar el estado Spot de una moneda? Escribe:_ `/analizar BTC`"
            enviar_telegram(msj_sniper)

        print("✅ Escaneo completado.")

    except Exception as e:
        print(f"Error en el escaneo general: {e}")

# ==========================================
# 8. BUCLE PRINCIPAL
# ==========================================
if __name__ == "__main__":
    hilo_telegram = threading.Thread(target=escuchar_mensajes_telegram, daemon=True)
    hilo_telegram.start()
    
    analizar_mercado()
    while True:
        time.sleep(3600)
        analizar_mercado()

       
