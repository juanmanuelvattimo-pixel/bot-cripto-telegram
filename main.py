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
# 4. MOTOR DE ANÁLISIS TÉCNICO (15M, 1H, 4H, 1D)
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
        df['rsi'] = ta.momentum.rsi(df['close'], window=14)
        
        # --- STOCHRSI ---
        rsi_min = df['rsi'].rolling(window=14).min()
        rsi_max = df['rsi'].rolling(window=14).max()
        denominador = (rsi_max - rsi_min).replace(0, 0.0001)
        
        df['stochrsi_k'] = ((df['rsi'] - rsi_min) / denominador) * 100
        df['stochrsi_d'] = df['stochrsi_k'].rolling(window=3).mean()

        # --- MACD ---
        macd_ind = ta.trend.MACD(df['close'], window_slow=26, window_fast=12, window_sign=9)
        df['macdhist'] = macd_ind.macd_diff()
        
        # --- ADX ---
        adx_ind = ta.trend.ADXIndicator(df['high'], df['low'], df['close'], window=14)
        df['adx'] = adx_ind.adx()

        # --- SUPERTREND ---
        df['atr'] = ta.volatility.average_true_range(df['high'], df['low'], df['close'], window=14)
        hl2 = (df['high'] + df['low']) / 2
        df['up_basic'] = hl2 - (2.0 * df['atr'])
        df['dn_basic'] = hl2 + (2.0 * df['atr'])
        
        df['supertrend_direction'] = 1
        for i in range(1, n_velas):
            if df['close'].iloc[i] > df['dn_basic'].iloc[i-1]:
                df.loc[df.index[i], 'supertrend_direction'] = 1
            elif df['close'].iloc[i] < df['up_basic'].iloc[i-1]:
                df.loc[df.index[i], 'supertrend_direction'] = -1
            else:
                df.loc[df.index[i], 'supertrend_direction'] = df['supertrend_direction'].iloc[i-1]

        st_dir = df['supertrend_direction'].iloc[-1]
        
        stoch_k = df['stochrsi_k'].iloc[-1]
        stoch_d = df['stochrsi_d'].iloc[-1]

        soporte_key, resistencia_key = calcular_soportes_resistencias(df, precio)
        atr = df['atr'].iloc[-1] if not df['atr'].empty else (precio * 0.02)

        return {
            'precio': precio,
            'atr': atr,
            'st_dir': st_dir,
            'supertrend_estado': "🟢 ALCISTA" if st_dir == 1 else "🔴 BAJISTA",
            'stoch_k': stoch_k,
            'stoch_d': stoch_d,
            'macd_hist': df['macdhist'].iloc[-1],
            'macd_hist_prev': df['macdhist'].iloc[-2],
            'adx': df['adx'].iloc[-1],
            'ema10': df['ema10'].iloc[-1],
            'cierra_arriba_ema10': precio > df['ema10'].iloc[-1],
            'cierra_abajo_ema10': precio < df['ema10'].iloc[-1],
            'soporte': soporte_key,
            'resistencia': resistencia_key,
        }
    except Exception as e:
        return None

# ==========================================
# 5. MOTOR DE ESTRATEGIA (ADX sin pendiente, Sin Cruce Stoch, R:R >= 1.2)
# ==========================================
def evaluar_todas_las_estrategias(simbolo_limpio, analisis_tf):
    if '15m' not in analisis_tf or '1h' not in analisis_tf or '4h' not in analisis_tf or '1d' not in analisis_tf:
        return None

    d1 = analisis_tf['1d']
    h4 = analisis_tf['4h']
    h1 = analisis_tf['1h']
    m15 = analisis_tf['15m']
    
    precio_act = h1['precio']
    atr_act = h1['atr']
    
    sniper_res = []

    # 1. FILTRO DIARIO (1D): Tendencia macro obligatoria
    tendencia_1d_alcista = (d1['st_dir'] == 1)
    tendencia_1d_bajista = (d1['st_dir'] == -1)

    # 2. ESCUDO 4H: SuperTrend + Histograma MACD
    tendencia_4h_alcista = (h4['st_dir'] == 1) and (h4['macd_hist'] > 0)
    tendencia_4h_bajista = (h4['st_dir'] == -1) and (h4['macd_hist'] < 0)

    # 3. FILTRO DE ADX EN 1H (Solo exige ADX >= 20, sin importar pendiente)
    adx_valido_1h = (h1['adx'] >= 12)

    # 4. FILTRO DE EXTENSIÓN (Distancia a la EMA 10 en 1H <= 1.5 ATR)
    distancia_ema10 = abs(precio_act - h1['ema10'])
    max_extension = atr_act * 1.5
    filtro_no_extendido = distancia_ema10 <= max_extension

    # 5. CONTROL 15 MINUTOS (15M): El precio va a favor en micro
    filtro_15m_long = m15['cierra_arriba_ema10']
    filtro_15m_short = m15['cierra_abajo_ema10']

    # --- GATILLO LONG ---
    es_valle_rojo_contrayendose = (h1['macd_hist'] < 0) and (h1['macd_hist'] > h1['macd_hist_prev'])
    es_valle_verde_creciendo = (h1['macd_hist'] > 0) and (h1['macd_hist'] > h1['macd_hist_prev'])
    macd_long_valido = es_valle_rojo_contrayendose or es_valle_verde_creciendo

    gatillo_long = (
        tendencia_1d_alcista and
        tendencia_4h_alcista and 
        adx_valido_1h and
        filtro_no_extendido and
        h1['stoch_k'] < 25 and   # Solo evalúa que esté en zona de piso (< 25)
        macd_long_valido and
        filtro_15m_long
    )

    if gatillo_long:
        sl_final = h1['soporte'] - (1.0 * atr_act)
        pct_sl = abs((precio_act - sl_final) / precio_act) * 100
        riesgo = precio_act - sl_final
        
        if riesgo > 0:
            tp1 = precio_act + (riesgo * 1.5)
            tp2 = precio_act + (riesgo * 2.5)
            tp3 = precio_act + (riesgo * 3.5)
            beneficio = tp1 - precio_act
            ratio_actual = beneficio / riesgo
            
            if ratio_actual >= 1.2:
                fase_macd_txt = "Valle rojo contrayéndose hacia cero (Giro temprano)" if es_valle_rojo_contrayendose else "Valle verde en expansión (Impulso fuerte)"
                sniper_res.append({
                    'symbol': simbolo_limpio, 'tipo': 'LONG 🟢 (ADX sin pendiente)',
                    'precio': precio_act, 'sl': sl_final, 'pct_sl': pct_sl,
                    'tp1': tp1, 'pct_tp1': abs((tp1 - precio_act)/precio_act)*100,
                    'tp2': tp2, 'pct_tp2': abs((tp2 - precio_act)/precio_act)*100,
                    'tp3': tp3, 'pct_tp3': abs((tp3 - precio_act)/precio_act)*100,
                    'supertrend': h1['supertrend_estado'],
                    'rr': f"1:{ratio_actual:.2f}",
                    'motivos': [
                        "Tendencia DIARIA (1D) alcista confirmada",
                        "Tendencia 4H alcista y MACD 4H positivos",
                        f"ADX 1H con fuerza suficiente ({h1['adx']:.1f} >= 20)",
                        f"Precio cerca de EMA 10 (Distancia: {distancia_ema10:.4f} <= 1.5 ATR)",
                        f"StochRSI en zona de PISO ({h1['stoch_k']:.1f} < 25)",
                        f"MACD 1H: {fase_macd_txt}",
                        "15M acompañando con cierre sobre EMA 10",
                        f"Ratio Riesgo/Beneficio aceptado (1:{ratio_actual:.2f} >= 1.2)"
                    ]
                })

    # --- GATILLO SHORT ---
    es_colina_verde_contrayendose = (h1['macd_hist'] > 0) and (h1['macd_hist'] < h1['macd_hist_prev'])
    es_valle_rojo_profundizando = (h1['macd_hist'] < 0) and (h1['macd_hist'] < h1['macd_hist_prev'])
    macd_short_valido = es_colina_verde_contrayendose or es_valle_rojo_profundizando

    gatillo_short = (
        tendencia_1d_bajista and
        tendencia_4h_bajista and 
        adx_valido_1h and
        filtro_no_extendido and
        h1['stoch_k'] > 75 and   # Solo evalúa que esté en zona de cielo (> 75)
        macd_short_valido and
        filtro_15m_short
    )

    if gatillo_short:
        sl_final = h1['resistencia'] + (1.0 * atr_act)
        pct_sl = abs((sl_final - precio_act) / precio_act) * 100
        riesgo = sl_final - precio_act
        
        if riesgo > 0:
            tp1 = precio_act - (riesgo * 1.5)
            tp2 = precio_act - (riesgo * 2.5)
            tp3 = precio_act - (riesgo * 3.5)
            beneficio = precio_act - tp1
            ratio_actual = beneficio / riesgo

            if ratio_actual >= 1.2:
                fase_macd_txt = "Colina verde contrayéndose hacia cero (Giro temprano)" if es_colina_verde_contrayendose else "Valle rojo en expansión (Impulso bajista fuerte)"
                sniper_res.append({
                    'symbol': simbolo_limpio, 'tipo': 'SHORT 🔴 (ADX sin pendiente)',
                    'precio': precio_act, 'sl': sl_final, 'pct_sl': pct_sl,
                    'tp1': tp1, 'pct_tp1': abs((precio_act - tp1)/precio_act)*100,
                    'tp2': tp2, 'pct_tp2': abs((tp2 - precio_act)/precio_act)*100,
                    'tp3': tp3, 'pct_tp3': abs((tp3 - precio_act)/precio_act)*100,
                    'supertrend': h1['supertrend_estado'],
                    'rr': f"1:{ratio_actual:.2f}",
                    'motivos': [
                        "Tendencia DIARIA (1D) bajista confirmada",
                        "Tendencia 4H bajista y MACD 4H negativos",
                        f"ADX 1H con fuerza suficiente ({h1['adx']:.1f} >= 20)",
                        f"Precio cerca de EMA 10 (Distancia: {distancia_ema10:.4f} <= 1.5 ATR)",
                        f"StochRSI en zona de CIELO ({h1['stoch_k']:.1f} > 75)",
                        f"MACD 1H: {fase_macd_txt}",
                        "15M acompañando con cierre bajo EMA 10",
                        f"Ratio Riesgo/Beneficio aceptado (1:{ratio_actual:.2f} >= 1.2)"
                    ]
                })

    return sniper_res

# ==========================================
# 6. FUNCIONES DE ESCANEO / CONSULTA MANUAL
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

def evaluar_trade_manual(ticker_raw):
    ticker = ticker_raw.upper().replace("$", "").replace("USDT", "") + "/USDT"
    simbolo_limpio = ticker.split('/')[0]
    
    temporalidades = ['15m', '1h', '4h', '1d']
    analisis_tf = {}
    
    for tf in temporalidades:
        res = analizar_par_completo(ticker, tf)
        if res is None:
            enviar_telegram(f"🤖 **BOT ACTIVO ✅**\n\n❌ No se pudo encontrar o analizar `{ticker_raw}` en BingX.")
            return
        analisis_tf[tf] = res

    sniper = evaluar_todas_las_estrategias(simbolo_limpio, analisis_tf)
    msj = f"🤖 **BOT ACTIVO (ADX sin pendiente) ✅**\n\n🎯 *EVALUACIÓN MANUAL: ${simbolo_limpio}*\n\n"

    if sniper:
        for op in sniper:
            msj += f"⚡ *ESTRATEGIA {op['tipo']}: APROBADA* _(R:R {op['rr']})_\n"
            msj += f"🔮 *SuperTrend 1H:* `{op['supertrend']}`\n"
            msj += f"💵 *Entrada:* `{fmt_precio(op['precio'])}`\n"
            msj += f"🛑 *Stop Loss:* `{fmt_precio(op['sl'])}` _(-{op['pct_sl']:.2f}%)_\n"
            msj += f"🎯 *TP1:* `{fmt_precio(op['tp1'])}` _(+{op['pct_tp1']:.2f}%)_\n"
            msj += f"📋 *Condiciones Cumplidas:*\n"
            for m in op.get('motivos', []):
                msj += f"  • {m}\n"
            msj += "\n"
    else:
        msj += "⚪ No cumple con los filtros actuales.\n\n"

    enviar_telegram(msj)

def procesar_par_paralelo(par):
    temporalidades = ['15m', '1h', '4h', '1d']
    analisis_tf = {}
    for tf in temporalidades:
        res = analizar_par_completo(par, tf)
        if res is not None:
            analisis_tf[tf] = res
    
    simbolo_limpio = par.split('/')[0]
    return evaluar_todas_las_estrategias(simbolo_limpio, analisis_tf)

def escanear_senales_sniper_manual():
    enviar_telegram("🤖 **BOT ACTIVO (ADX sin pendiente) ✅**\n\n🔍 Escaneando mercado...")
    
    pares_filtrados = obtener_pares_top()
    if not pares_filtrados:
        enviar_telegram("🤖 **BOT ACTIVO ✅**\n\n❌ Error al obtener los pares del mercado.")
        return

    entradas_sniper = []

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(procesar_par_paralelo, par): par for par in pares_filtrados}
        for future in as_completed(futures):
            try:
                sniper = future.result()
                if sniper:
                    entradas_sniper.extend(sniper)
            except Exception as e:
                logging.error(f"Error procesando hilo de par: {e}")

    enviar_resultados_escaneo(entradas_sniper)

def enviar_resultados_escaneo(entradas_sniper):
    if not entradas_sniper:
        enviar_telegram("🤖 **BOT ACTIVO ✅**\n\n❌ *NO HAY OPORTUNIDADES ACTIVAS*\n\nNingún par cumple con los filtros actuales.")
        return

    msj_sniper = "🤖 **BOT ACTIVO (ADX sin pendiente) ✅**\n\n⚡ *OPORTUNIDADES DETECTADAS:* ⚡\n\n"
    for op in entradas_sniper[:5]:
        msj_sniper += f"🪙 *{op['symbol']}* -> *{op['tipo']}* _(R:R {op['rr']})_\n"
        msj_sniper += f"💵 *Entrada:* `{fmt_precio(op['precio'])}`\n"
        msj_sniper += f"🛑 *SL:* `{fmt_precio(op['sl'])}` | 🎯 *TP1:* `{fmt_precio(op['tp1'])}`\n"
        msj_sniper += f"📋 *Motivos:*\n"
        for m in op.get('motivos', []):
            msj_sniper += f"  • {m}\n"
        msj_sniper += "\n"
    enviar_telegram(msj_sniper)

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
                            
                    if text.startswith("/trade"):
                        partes = text.split()
                        if len(partes) > 1:
                            ticker = partes[1]
                            enviar_telegram(f"🤖 **BOT ACTIVO ✅**\n\n⏳ Evaluando trade para `${ticker.upper()}`...")
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
# 8. ESCANEO AUTOMÁTICO GENERAL
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
        
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(procesar_par_paralelo, par): par for par in pares_filtrados}
            for future in as_completed(futures):
                try:
                    sniper = future.result()
                    if sniper:
                        entradas_sniper.extend(sniper)
                except Exception as e:
                    logging.error(f"Error en tarea paralela automática: {e}")

        enviar_resultados_escaneo(entradas_sniper)
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
    
    enviar_telegram("🤖 **BOT ACTIVO (ADX sin pendiente) ✅**\n\nEl bot se ha iniciado sin la condición de pendiente en el ADX (solo exige ADX >= 20).")
    logging.info("🚀 Bot actualizado y listo.")
    
    analizar_mercado()
    
    while True:
        time.sleep(1800)
        try:
            with open(lock_file, "w") as f:
                f.write(str(os.getpid()))
        except Exception:
            pass
        analizar_mercado()
