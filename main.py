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
# 4. MOTOR DE ANÁLISIS MULTI-TEMPORAL (CON ADX)
# ==========================================
def analizar_par_completo(symbol, timeframe):
    try:
        limit_velas = 60 if timeframe == '1w' else 80
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit_velas)
        
        if not ohlcv or len(ohlcv) < 25:
            return None
        
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        precio = df['close'].iloc[-1]

        df['ema10'] = ta.trend.ema_indicator(df['close'], window=10)
        df['ema20'] = ta.trend.ema_indicator(df['close'], window=20)
        df['ema55'] = ta.trend.ema_indicator(df['close'], window=55)
        
        df['rsi'] = ta.momentum.rsi(df['close'], window=14)
        df['mfi'] = ta.volume.money_flow_index(df['high'], df['low'], df['close'], df['volume'], window=14)
        
        # MACD
        macd_ind = ta.trend.MACD(df['close'], window_slow=26, window_fast=12, window_sign=9)
        df['macd'] = macd_ind.macd()
        df['macd_signal'] = macd_ind.macd_signal()
        df['macd_hist'] = macd_ind.macd_diff()

        # StochRSI
        stoch_rsi = ta.momentum.StochRSIIndicator(df['close'], window=14, smooth1=3, smooth2=3)
        df['stoch_rsi_k'] = stoch_rsi.stochrsi_k() * 100
        df['stoch_rsi_d'] = stoch_rsi.stochrsi_d() * 100
        
        # ADX (Average Directional Index)
        adx_ind = ta.trend.ADXIndicator(df['high'], df['low'], df['close'], window=14)
        df['adx'] = adx_ind.adx()

        df['atr'] = ta.volatility.average_true_range(df['high'], df['low'], df['close'], window=14)

        macd_hist = df['macd_hist'].iloc[-1]
        macd_hist_prev = df['macd_hist'].iloc[-2]
        
        stoch_k = df['stoch_rsi_k'].iloc[-1]
        adx_val = df['adx'].iloc[-1] if not df['adx'].empty else 0

        # Zonas claras de MACD
        valle_rojo_claro = (macd_hist < 0) and (macd_hist > macd_hist_prev)
        valle_verde_claro = (macd_hist > 0) and (macd_hist < macd_hist_prev)

        soporte_key, resistencia_key = calcular_soportes_resistencias(df, precio)
        atr = df['atr'].iloc[-1] if not df['atr'].empty else (precio * 0.02)

        return {
            'precio': precio,
            'atr': atr,
            'rsi': df['rsi'].iloc[-1],
            'mfi': df['mfi'].iloc[-1],
            'macd_hist': macd_hist,
            'macd_hist_prev': macd_hist_prev,
            'valle_rojo_claro': valle_rojo_claro,
            'valle_verde_claro': valle_verde_claro,
            'stoch_k': stoch_k,
            'adx': adx_val,
            'ema10': df['ema10'].iloc[-1],
            'ema20': df['ema20'].iloc[-1],
            'ema55': df['ema55'].iloc[-1],
            'soporte': soporte_key,
            'resistencia': resistencia_key,
        }
    except Exception as e:
        return None

# ==========================================
# MÓDULO UNIFICADO DE EVALUACIÓN (CON ADX > 18 Y FILTRO DIARIO)
# ==========================================
def evaluar_todas_las_estrategias(simbolo_limpio, analisis_tf):
    if '1h' not in analisis_tf or '4h' not in analisis_tf or '1d' not in analisis_tf:
        return None

    d1 = analisis_tf['1d']
    h4 = analisis_tf['4h']
    h1 = analisis_tf['1h']
    
    precio_act = h1['precio']
    atr_act = h1['atr']
    
    resultados = []

    # ==========================================
    # 1. SEÑAL LONG: Valle Rojo Claro (1H & 4H) + Stoch < 40 + ADX > 18 + Tendencia Alcista 1D
    # ==========================================
    tendencia_diaria_alcista = d1['ema10'] > d1['ema55']
    condicion_long_macd = h1['valle_rojo_claro'] and h4['valle_rojo_claro']
    condicion_long_stoch = h1['stoch_k'] < 40
    condicion_adx = h1['adx'] > 18

    if tendencia_diaria_alcista and condicion_long_macd and condicion_long_stoch and condicion_adx:
        sl_final = h1['soporte'] - (1.0 * atr_act)
        pct_sl = abs((precio_act - sl_final) / precio_act) * 100
        
        if pct_sl <= 4.0:
            riesgo = precio_act - sl_final
            tp1 = precio_act + (riesgo * 1.5)
            tp2 = precio_act + (riesgo * 2.5)
            tp3 = precio_act + (riesgo * 3.5)

            ratio_actual = (tp1 - precio_act) / riesgo if riesgo > 0 else 0
            
            if riesgo > 0 and ratio_actual >= 1.2: 
                resultados.append({
                    'symbol': simbolo_limpio, 'tipo': 'LONG 🟢', 'estrategia': 'ZONAS + ADX > 18 + DIARIO',
                    'precio': precio_act, 'sl': sl_final, 'pct_sl': pct_sl,
                    'tp1': tp1, 'pct_tp1': abs((tp1 - precio_act)/precio_act)*100,
                    'tp2': tp2, 'pct_tp2': abs((tp2 - precio_act)/precio_act)*100,
                    'tp3': tp3, 'pct_tp3': abs((tp3 - precio_act)/precio_act)*100,
                    'rr': f"1:{ratio_actual:.2f}",
                    'motivos': [
                        f"Tendencia diaria alcista (EMA 10 > EMA 55 en 1D)",
                        f"Fuerza de tendencia adecuada (ADX: {h1['adx']:.1f} > 18)",
                        f"MACD en zona roja clara en 1H y 4H",
                        f"StochRSI en zona baja (< 40)",
                        f"Stop Loss ajustado con ATR (1.0x)"
                    ]
                })

    # ==========================================
    # 2. SEÑAL SHORT: Cresta Verde Clara (1H & 4H) + Stoch > 60 + ADX > 18 + Tendencia Bajista 1D
    # ==========================================
    tendencia_diaria_bajista = d1['ema10'] < d1['ema55']
    condicion_short_macd = h1['valle_verde_claro'] and h4['valle_verde_claro']
    condicion_short_stoch = h1['stoch_k'] > 60

    if tendencia_diaria_bajista and condicion_short_macd and condicion_short_stoch and condicion_adx:
        sl_final = h1['resistencia'] + (1.0 * atr_act)
        pct_sl = abs((sl_final - precio_act) / precio_act) * 100
        
        if pct_sl <= 4.0:
            riesgo = sl_final - precio_act
            tp1 = precio_act - (riesgo * 1.5)
            tp2 = precio_act - (riesgo * 2.5)
            tp3 = precio_act - (riesgo * 3.5)
            
            ratio_actual = (precio_act - tp1) / riesgo if riesgo > 0 else 0

            if riesgo > 0 and ratio_actual >= 1.2: 
                resultados.append({
                    'symbol': simbolo_limpio, 'tipo': 'SHORT 🔴', 'estrategia': 'ZONAS + ADX > 18 + DIARIO',
                    'precio': precio_act, 'sl': sl_final, 'pct_sl': pct_sl,
                    'tp1': tp1, 'pct_tp1': abs((precio_act - tp1)/precio_act)*100,
                    'tp2': tp2, 'pct_tp2': abs((tp2 - precio_act)/precio_act)*100,
                    'tp3': tp3, 'pct_tp3': abs((tp3 - precio_act)/precio_act)*100,
                    'rr': f"1:{ratio_actual:.2f}",
                    'motivos': [
                        f"Tendencia diaria bajista (EMA 10 < EMA 55 en 1D)",
                        f"Fuerza de tendencia adecuada (ADX: {h1['adx']:.1f} > 18)",
                        f"MACD en zona verde clara en 1H y 4H",
                        f"StochRSI en zona alta (> 60)",
                        f"Stop Loss ajustado con ATR (1.0x)"
                    ]
                })

    return resultados

# ==========================================
# 5. FUNCIONES DE ESCANEO / CONSULTA MANUAL
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
        return [item['symbol'] for item in pares_usdt[:350]]
    except Exception as e:
        logging.error(f"Error obteniendo pares top: {e}")
        return []

def analizar_cripto_individual(ticker_raw):
    ticker = ticker_raw.upper().replace("$", "").replace("USDT", "") + "/USDT"
    simbolo_limpio = ticker.split('/')[0]
    
    temporalidades = ['15m', '1h', '4h', '1d', '1w']
    msj = f"🤖 **BOT ACTIVO (ADX & Filtros) ✅**\n\n📊 *ANÁLISIS TÉCNICO: ${simbolo_limpio}*\n\n"
    
    for tf in temporalidades:
        res = analizar_par_completo(ticker, tf)
        if res is not None:
            msj += f"• *Temporalidad {tf.upper()}*:\n"
            msj += f"  - Precio: `{fmt_precio(res['precio'])}`\n"
            msj += f"  - ADX: `{res['adx']:.1f}`\n"
            msj += f"  - MACD Histograma: `{res['macd_hist']:.4f}`\n"
            msj += f"  - StochRSI K: `{res['stoch_k']:.1f}`\n\n"
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

    resultados = evaluar_todas_las_estrategias(simbolo_limpio, analisis_tf)
    
    msj = f"🤖 **BOT ACTIVO ✅**\n\n🎯 *EVALUACIÓN MANUAL: ${simbolo_limpio}*\n\n"

    if resultados:
        for op in resultados:
            msj += f"⚡ *ESTRATEGIA {op['tipo']}: APROBADA* _(R:R {op['rr']})_\n"
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
        msj += "⚪ No se cumplen las condiciones (ADX < 18, tendencia diaria contraria o zonas MACD ausentes).\n\n"

    enviar_telegram(msj)

# ==========================================
# 6. ESCANEO RÁPIDO CONCURRENTE
# ==========================================
def procesar_par_paralelo(par):
    temporalidades = ['15m', '1h', '4h', '1d', '1w']
    analisis_tf = {}
    for tf in temporalidades:
        res = analizar_par_completo(par, tf)
        if res is not None:
            analisis_tf[tf] = res
    
    simbolo_limpio = par.split('/')[0]
    return evaluar_todas_las_estrategias(simbolo_limpio, analisis_tf)

def escanear_senales_sniper_manual():
    enviar_telegram("🤖 **BOT ACTIVO ✅**\n\n🔍 Escaneando el mercado con filtros de Tendencia, ADX > 18 y Zonas MACD...")
    
    pares_filtrados = obtener_pares_top()
    if not pares_filtrados:
        enviar_telegram("🤖 **BOT ACTIVO ✅**\n\n❌ Error al obtener los pares del mercado en este momento.")
        return

    entradas = []

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(procesar_par_paralelo, par): par for par in pares_filtrados}
        for future in as_completed(futures):
            try:
                res = future.result()
                if res:
                    entradas.extend(res)
            except Exception as e:
                logging.error(f"Error procesando hilo de par: {e}")

    enviar_resultados_escaneo(entradas)

def enviar_resultados_escaneo(entradas):
    if not entradas:
        enviar_telegram("🤖 **BOT ACTIVO ✅**\n\n❌ *NO HAY ENTRADAS ACTIVAS*\n\nNinguna criptomoneda cumple simultáneamente con ADX > 18, zonas y tendencia.")
        return

    msj = "🤖 **BOT ACTIVO ✅**\n\n⚡ *ENTRADAS DETECTADAS (FILTROS COMPLETOS):* ⚡\n\n"
    for op in entradas[:5]:
        msj += f"🪙 *{op['symbol']}* -> *{op['tipo']}* _(R:R {op['rr']})_\n"
        msj += f"💵 *Entrada:* `{fmt_precio(op['precio'])}`\n"
        msj += f"🛑 *Stop Loss:* `{fmt_precio(op['sl'])}` _(-{op['pct_sl']:.2f}%)_\n"
        msj += f"🎯 *TP1:* `{fmt_precio(op['tp1'])}` _(+{op['pct_tp1']:.2f}%)_\n"
        msj += f"🎯 *TP2:* `{fmt_precio(op['tp2'])}` _(+{op['pct_tp2']:.2f}%)_\n"
        msj += f"🎯 *TP3:* `{fmt_precio(op['tp3'])}` _(+{op['pct_tp3']:.2f}%)_\n"
        msj += f"📋 *Condiciones Cumplidas:*\n"
        for m in op.get('motivos', []):
            msj += f"  • {m}\n"
        msj += "\n"
    enviar_telegram(msj)

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
                            enviar_telegram(f"🤖 **BOT ACTIVO ✅**\n\n⏳ Analizando `${ticker.upper()}`...")
                            analizar_cripto_individual(ticker)
                        else:
                            enviar_telegram("🤖 **BOT ACTIVO ✅**\n\nℹ️ Indica la moneda. Ejemplo: `/analizar BTC`")
                            
                    elif text.startswith("/trade"):
                        partes = text.split()
                        if len(partes) > 1:
                            ticker = partes[1]
                            enviar_telegram(f"🤖 **BOT ACTIVO ✅**\n\n⏳ Evaluando ADX y tendencia para `${ticker.upper()}`...")
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
            
        entradas = []
        
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(procesar_par_paralelo, par): par for par in pares_filtrados}
            for future in as_completed(futures):
                try:
                    res = future.result()
                    if res:
                        entradas.extend(res)
                except Exception as e:
                    logging.error(f"Error en tarea paralela automática: {e}")

        enviar_resultados_escaneo(entradas)
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
        res_btc = analizar_par_completo(symbol_btc, '1h')
                
        if res_btc:
            precio_btc = res_btc['precio']
            msj_inicio = f"🤖 **BOT ACTIVO (ADX > 18 + Filtros) ✅**\n\n"
            msj_inicio += f"🪙 **Bitcoin (BTC)** -> Precio Actual: `{fmt_precio(precio_btc)}` USDT\n"
            msj_inicio += f"📊 ADX 1H: `{res_btc['adx']:.1f}` | MACD Hist: `{res_btc['macd_hist']:.4f}`\n"
            enviar_telegram(msj_inicio)
        else:
            enviar_telegram("🤖 **BOT ACTIVO ✅**\n\nEl bot se ha iniciado correctamente (Sin datos preliminares de BTC).")
    except Exception as e:
        enviar_telegram(f"🤖 **BOT ACTIVO ✅**\n\nEl bot se ha iniciado correctamente (Error al consultar BTC: {e})")

    logging.info("🚀 Bot actualizado con filtro de ADX > 18 listo.")
    
    analizar_mercado()
    
    while True:
        time.sleep(1800)
        try:
            with open(lock_file, "w") as f:
                f.write(str(os.getpid()))
        except Exception:
            pass
        analizar_mercado()
