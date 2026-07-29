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
# 4. MOTOR DE ANÁLISIS MEJORADO
# ==========================================
def analizar_par_completo(symbol, timeframe):
    try:
        if timeframe == '1w':
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe='1w', limit=60)
            if not ohlcv or len(ohlcv) < 5:
                return None
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            
            window_ema = min(55, len(df)-1)
            df['ema_macro'] = ta.trend.ema_indicator(df['close'], window=window_ema)
            
            precio_act = df['close'].iloc[-1]
            ema_act = df['ema_macro'].iloc[-1]
            
            return {
                'es_alcista': precio_act > ema_act,
                'es_bajista': precio_act <= ema_act,
                'precio': precio_act,
                'atr': precio_act * 0.03,
                'oracle_estado': "⚪ N/A"
            }

        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=80)
        if not ohlcv or len(ohlcv) < 30:
            return None
        
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        n_velas = len(df)
        
        df['ema10'] = ta.trend.ema_indicator(df['close'], window=min(10, n_velas-1))
        df['ema20'] = ta.trend.ema_indicator(df['close'], window=min(20, n_velas-1))
        df['ema55'] = ta.trend.ema_indicator(df['close'], window=min(55, n_velas-1))
        
        win_adx = min(14, n_velas-1)
        
        ap3 = (df['high'] + df['low'] + df['close']) / 3
        esa = ta.trend.ema_indicator(ap3, window=min(10, n_velas-1))
        d = ta.trend.ema_indicator((ap3 - esa).abs(), window=min(10, n_velas-1))
        ci = (ap3 - esa) / (0.015 * d)
        df['wt1'] = ta.trend.ema_indicator(ci, window=min(21, n_velas-1))
        df['wt2'] = ta.trend.sma_indicator(df['wt1'], window=min(4, len(df['wt1'].dropna())-1))
        
        df['oracle_fast'] = ta.trend.ema_indicator(df['close'], window=min(8, n_velas-1))
        df['oracle_slow'] = ta.trend.ema_indicator(df['close'], window=min(13, n_velas-1))
        df['oracle_ribbon'] = df['oracle_fast'] > df['oracle_slow']
        
        df['atr'] = ta.volatility.average_true_range(df['high'], df['low'], df['close'], window=win_adx)

        precio = df['close'].iloc[-1]
        e10, e20, e55 = df['ema10'].iloc[-1], df['ema20'].iloc[-1], df['ema55'].iloc[-1]
        wt1 = df['wt1'].iloc[-1] if not df['wt1'].empty else 0
        wt2 = df['wt2'].iloc[-1] if not df['wt2'].empty else 0
        wt1_prev = df['wt1'].iloc[-2] if len(df['wt1']) > 1 else wt1
        wt2_prev = df['wt2'].iloc[-2] if len(df['wt2']) > 1 else wt2
        atr = df['atr'].iloc[-1] if not df['atr'].empty else (precio * 0.02)
        
        oracle_actual = df['oracle_ribbon'].iloc[-1]
        oracle_previo = df['oracle_ribbon'].iloc[-2] if len(df['oracle_ribbon']) > 1 else oracle_actual
        
        oracle_buy = (not oracle_previo) and oracle_actual
        oracle_sell = oracle_previo and (not oracle_actual)

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

        puntos_alcistas = sum([precio > e55, e10 > e20, wt1 > wt2, oracle_actual])
        puntos_bajistas = 4 - puntos_alcistas

        return {
            'precio': precio,
            'atr': atr,
            'es_alcista': puntos_alcistas >= 3,
            'es_bajista': puntos_bajistas >= 3,
            'cruce_alcista': (wt1_prev <= wt2_prev) and (wt1 > wt2),
            'cruce_bajista': (wt1_prev >= wt2_prev) and (wt1 < wt2),
            'oracle_buy': oracle_buy,
            'oracle_sell': oracle_sell,
            'oracle_estado': "🟢 BUY" if oracle_actual else "🔴 SELL",
            'soporte': soporte_key,
            'resistencia': resistencia_key,
            'fibo_long': {'tp1': fibo_tp1_long, 'tp2': fibo_tp2_long, 'tp3': fibo_tp3_long},
            'fibo_short': {'tp1': fibo_tp1_short, 'tp2': fibo_tp2_short, 'tp3': fibo_tp3_short}
        }
    except Exception:
        return None

# ==========================================
# 5. FUNCIONALIDAD DE ANÁLISIS INDIVIDUAL (SPOT CON FILTRO 4H)
# ==========================================
def analizar_cripto_individual(ticker_raw):
    ticker = ticker_raw.upper().replace("$", "").replace("USDT", "") + "/USDT"
    
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
    simbolo = ticker.split('/')[0]
    
    msj = f"🔍 *ANÁLISIS SPOT (1X): ${simbolo}*\n\n"
    msj += f"💵 *Precio Actual:* `{precio_act:.4f}` USDT\n\n"
    
    msj += "📊 *ESTADO POR TEMPORALIDAD:*\n"
    for tf in ['1w', '1d', '4h', '1h']:
        estado = "🟢 Alcista" if analisis_tf[tf]['es_alcista'] else ("🔴 Bajista" if analisis_tf[tf]['es_bajista'] else "⚪ Neutro")
        msj += f"• *{tf.upper()}:* {estado}\n"
    
    msj += "\n🔮 *ORACLE RIBBON:*\n"
    msj += f"• *Diario (1D):* `{d1['oracle_estado']}`\n"
    msj += f"• *4 Horas (4H):* `{h4['oracle_estado']}`\n"
    msj += f"• *1 Hora (1H):* `{h1['oracle_estado']}`\n\n"
    
    msj += f"🧱 *Soporte 1H:* `{h1['soporte']:.4f}`\n"
    msj += f"🎯 *Resistencia 1H:* `{h1['resistencia']:.4f}`\n\n"

    # VALIDACIÓN ESTRICTA DE ENTRADA SPOT (INCLUYENDO 4H)
    es_long_valido = (
        d1['es_alcista'] and 
        w1['es_alcista'] and 
        h4['es_alcista'] and h4['oracle_estado'] == "🟢 BUY" and
        (h1['oracle_buy'] or (h1['cruce_alcista'] and h1['oracle_estado'] == "🟢 BUY") or h1['es_alcista'])
    )

    es_short_valido = (
        d1['es_bajista'] and 
        w1['es_bajista'] and 
        h4['es_bajista'] and h4['oracle_estado'] == "🔴 SELL" and
        (h1['oracle_sell'] or (h1['cruce_bajista'] and h1['oracle_estado'] == "🔴 SELL") or h1['es_bajista'])
    )

    if es_long_valido:
        sl = min(precio_act - (1.5 * atr_act), h1['soporte'] * 0.998)
        pct_sl = abs((precio_act - sl) / precio_act) * 100
        fibo = h1['fibo_long']
        tp1 = max(fibo['tp1'], precio_act * 1.015)
        tp2 = max(fibo['tp2'], precio_act * 1.030)
        tp3 = max(fibo['tp3'], precio_act * 1.050)
        
        msj += "⚡ *ESTRUCTURA DE ENTRADA SPOT (LONG / COMPRA):*\n"
        msj += "✅ *Filtro 4H:* Alcista Aprobado\n"
        msj += f"🛑 *Stop Loss:* `{sl:.4f}` _(-{pct_sl:.2f}% Spot)_\n"
        msj += f"🎯 *TP1 (Fibo):* `{tp1:.4f}` _(+{abs((tp1 - precio_act)/precio_act)*100:.2f}% Spot)_\n"
        msj += f"🎯 *TP2 (Retest):* `{tp2:.4f}` _(+{abs((tp2 - precio_act)/precio_act)*100:.2f}% Spot)_\n"
        msj += f"🎯 *TP3 (Runner):* `{tp3:.4f}` _(+{abs((tp3 - precio_act)/precio_act)*100:.2f}% Spot)_\n"

    elif es_short_valido:
        sl = max(precio_act + (1.5 * atr_act), h1['resistencia'] * 1.002)
        pct_sl = abs((sl - precio_act) / precio_act) * 100
        fibo = h1['fibo_short']
        tp1 = min(fibo['tp1'], precio_act * 0.985)
        tp2 = min(fibo['tp2'], precio_act * 0.970)
        tp3 = min(fibo['tp3'], precio_act * 0.950)
        
        msj += "⚡ *ESTRUCTURA DE ENTRADA SPOT (SHORT / VENTA):*\n"
        msj += "✅ *Filtro 4H:* Bajista Aprobado\n"
        msj += f"🛑 *Stop Loss:* `{sl:.4f}` _(-{pct_sl:.2f}% Spot)_\n"
        msj += f"🎯 *TP1 (Fibo):* `{tp1:.4f}` _(+{abs((precio_act - tp1)/precio_act)*100:.2f}% Spot)_\n"
        msj += f"🎯 *TP2 (Retest):* `{tp2:.4f}` _(+{abs((precio_act - tp2)/precio_act)*100:.2f}% Spot)_\n"
        msj += f"🎯 *TP3 (Runner):* `{tp3:.4f}` _(+{abs((precio_act - tp3)/precio_act)*100:.2f}% Spot)_\n"

    else:
        msj += "🚫 *NO HAY ENTRADA POSIBLE EN SPOT*\n"
        msj += "_Las temporalidades clave (4H, 1D, 1W) no están alineadas en la misma dirección o falta la confirmación del Oracle/Cipher B en 1H._"

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
                            enviar_telegram(f"⏳ Analizando `${ticker.upper()}` en Spot...")
                            analizar_cripto_individual(ticker)
                        else:
                            enviar_telegram("ℹ️ Indica la moneda. Ejemplo: `/analizar BTC` o `/analizar SOL`")
        except Exception:
            pass
        time.sleep(2)

# ==========================================
# 7. ESCANEO Y CLASIFICACIÓN GENERAL (10X)
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

                # EVALUADOR SNIPER CALCULADO A 10X (CON FILTRO DE 4H)
                h1 = analisis_tf['1h']
                h4 = analisis_tf['4h']
                precio_act = h1['precio']
                atr_act = h1['atr']

                # LONG 10X
                if estados['1d'] == "🟢" and estados['1w'] == "🟢" and estados['4h'] == "🟢" and (h1['oracle_buy'] or (h1['cruce_alcista'] and h1['oracle_estado'] == "🟢 BUY")):
                    sl_tecnico = min(precio_act - (1.2 * atr_act), h1['soporte'] * 0.998)
                    sl_max_10x = precio_act * 0.983
                    sl_final = max(sl_tecnico, sl_max_10x)
                    pct_sl = abs((precio_act - sl_final) / precio_act) * 100 * 10
                    
                    fibo = h1['fibo_long']
                    tp1 = max(fibo['tp1'], precio_act * 1.015)
                    tp2 = max(fibo['tp2'], precio_act * 1.030)
                    tp3 = max(fibo['tp3'], precio_act * 1.050)

                    entradas_sniper.append({
                        'symbol': simbolo_limpio, 'tipo': 'LONG 🟢',
                        'precio': precio_act, 'sl': sl_final, 'pct_sl': pct_sl,
                        'tp1': tp1, 'pct_tp1': abs((tp1 - precio_act)/precio_act)*100*10,
                        'tp2': tp2, 'pct_tp2': abs((tp2 - precio_act)/precio_act)*100*10,
                        'tp3': tp3, 'pct_tp3': abs((tp3 - precio_act)/precio_act)*100*10,
                        'oracle': h1['oracle_estado']
                    })

                # SHORT 10X
                elif estados['1d'] == "🔴" and estados['1w'] == "🔴" and estados['4h'] == "🔴" and (h1['oracle_sell'] or (h1['cruce_bajista'] and h1['oracle_estado'] == "🔴 SELL")):
                    sl_tecnico = max(precio_act + (1.2 * atr_act), h1['resistencia'] * 1.002)
                    sl_max_10x = precio_act * 1.017
                    sl_final = min(sl_tecnico, sl_max_10x)
                    pct_sl = abs((sl_final - precio_act) / precio_act) * 100 * 10
                    
                    fibo = h1['fibo_short']
                    tp1 = min(fibo['tp1'], precio_act * 0.985)
                    tp2 = min(fibo['tp2'], precio_act * 0.970)
                    tp3 = min(fibo['tp3'], precio_act * 0.950)

                    entradas_sniper.append({
                        'symbol': simbolo_limpio, 'tipo': 'SHORT 🔴',
                        'precio': precio_act, 'sl': sl_final, 'pct_sl': pct_sl,
                        'tp1': tp1, 'pct_tp1': abs((precio_act - tp1)/precio_act)*100*10,
                        'tp2': tp2, 'pct_tp2': abs((precio_act - tp2)/precio_act)*100*10,
                        'tp3': tp3, 'pct_tp3': abs((precio_act - tp3)/precio_act)*100*10,
                        'oracle': h1['oracle_estado']
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

        enviar_lista_telegram("🟢 *TOP PERFECCIÓN ALCISTA*", "EMA + Cipher B + Oracle (4H/1D/1W)", longs_perfectos)
        enviar_lista_telegram("📈 *TOP TENDENCIA ALCISTA (1D + 1S)*", "Tendencia Mayor Alcista Confirmada", longs_diario_semanal)
        enviar_lista_telegram("🔴 *TOP PERFECCIÓN BAJISTA*", "EMA + Cipher B + Oracle (4H/1D/1W)", shorts_perfectos)
        enviar_lista_telegram("📉 *TOP TENDENCIA BAJISTA (1D + 1S)*", "Tendencia Mayor Bajista Confirmada", shorts_diario_semanal)

        if entradas_sniper:
            msj_sniper = "⚡ *ENTRADAS SNIPER (OPTIMIZADAS 10X)* ⚡\n\n"
            for op in entradas_sniper[:5]:
                msj_sniper += f"🪙 *{op['symbol']}* -> *{op['tipo']}*\n"
                msj_sniper += f"🔮 *Oracle:* `{op['oracle']}`\n"
                msj_sniper += f"💵 *Entrada:* `{op['precio']:.4f}`\n"
                msj_sniper += f"🛑 *Stop Loss:* `{op['sl']:.4f}` _(-{op['pct_sl']:.1f}% en 10x)_\n"
                msj_sniper += f"🎯 *TP1 (Fibo):* `{op['tp1']:.4f}` _(+{op['pct_tp1']:.1f}% en 10x)_\n"
                msj_sniper += f"🎯 *TP2 (Retest):* `{op['tp2']:.4f}` _(+{op['pct_tp2']:.1f}% en 10x)_\n"
                msj_sniper += f"🎯 *TP3 (Runner):* `{op['tp3']:.4f}` _(+{op['pct_tp3']:.1f}% en 10x)_\n\n"
            
            msj_sniper += "💡 _¿Quieres analizar una cripto específica en Spot? Escribe:_ `/analizar BTC`"
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
        time.sleep(7200)
        analizar_mercado()

       
