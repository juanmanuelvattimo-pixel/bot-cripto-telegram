from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import os
import sys
import threading
import time
import ccxt
import pandas as pd
import requests
import ta

# ==========================================
# CONFIGURACIÓN DE LOGS
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)],
)

# ==========================================
# 0. FUNCIÓN AUXILIAR PARA FORMATO DE PRECIOS DINÁMICOS
# ==========================================


def fmt_precio(val):
  if val is None:
    return '0'
  if abs(val) < 0.01:
    return f'{val:.8f}'.rstrip('0').rstrip('.')
  else:
    return f'{val:.4f}'.rstrip('0').rstrip('.')


# ==========================================
# 1. CONFIGURACIÓN DE TELEGRAM Y FILTRO MULTI-MENSAJE
# ==========================================
TELEGRAM_TOKEN = '8810680096:AAGPSrNFFWpbUHuj0laurGLxuepKIZDexys'
CHAT_ID = '1473411725'

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
      mensaje = mensaje[:3500] + '\n\n⚠️ _(Mensaje recortado por tamaño)_'

    try:
      url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
      data = {'chat_id': CHAT_ID, 'text': mensaje, 'parse_mode': 'Markdown'}
      res = requests.post(url, data=data, timeout=10)

      if res.status_code == 429:
        retry_after = int(
            res.json().get('parameters', {}).get('retry_after', 5)
        )
        logging.warning(
            f'Telegram FloodWait. Pausando por {retry_after} segundos.'
        )
        time.sleep(retry_after)
        requests.post(url, data=data, timeout=10)

      if res.status_code == 200:
        tiempo_ultimo_envio = time.time()
        historial_mensajes_enviados.append(mensaje)
        if len(historial_mensajes_enviados) > 15:
          historial_mensajes_enviados.pop(0)
    except Exception as e:
      logging.error(f'Error enviando mensaje a Telegram: {e}')


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
    if (
        df['high'].iloc[i] > df['high'].iloc[i - 1]
        and df['high'].iloc[i] > df['high'].iloc[i - 2]
        and df['high'].iloc[i] > df['high'].iloc[i + 1]
        and df['high'].iloc[i] > df['high'].iloc[i + 2]
    ):
      pivots_high.append(df['high'].iloc[i])

    if (
        df['low'].iloc[i] < df['low'].iloc[i - 1]
        and df['low'].iloc[i] < df['low'].iloc[i - 2]
        and df['low'].iloc[i] < df['low'].iloc[i + 1]
        and df['low'].iloc[i] < df['low'].iloc[i + 2]
    ):
      pivots_low.append(df['low'].iloc[i])

  por_encima = [p for p in pivots_high if p > precio_actual]
  resistencia = (
      min(por_encima) if por_encima else df['high'].tail(min(30, n)).max()
  )

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

    df = pd.DataFrame(
        ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
    )
    n_velas = len(df)
    precio = df['close'].iloc[-1]

    df['ema10'] = ta.trend.ema_indicator(df['close'], window=10)
    df['ema20'] = ta.trend.ema_indicator(df['close'], window=20)
    df['ema55'] = ta.trend.ema_indicator(df['close'], window=55)

    df['rsi'] = ta.momentum.rsi(df['close'], window=14)
    df['mfi'] = ta.volume.money_flow_index(
        df['high'], df['low'], df['close'], df['volume'], window=14
    )

    indicator_bb = ta.volatility.BollingerBands(
        close=df['close'], window=20, window_dev=2
    )
    df['bb_upper'] = indicator_bb.bollinger_hband()
    df['bb_lower'] = indicator_bb.bollinger_lband()

    adx_ind = ta.trend.ADXIndicator(df['high'], df['low'], df['close'], window=14)
    df['adx'] = adx_ind.adx()
    df['plus_di'] = adx_ind.adx_pos()
    df['minus_di'] = adx_ind.adx_neg()

    df['atr'] = ta.volatility.average_true_range(
        df['high'], df['low'], df['close'], window=14
    )
    multiplicador = 2.0
    hl2 = (df['high'] + df['low']) / 2
    df['up_basic'] = hl2 - (multiplicador * df['atr'])
    df['dn_basic'] = hl2 + (multiplicador * df['atr'])

    df['supertrend_direction'] = 1
    for i in range(1, n_velas):
      if df['close'].iloc[i] > df['dn_basic'].iloc[i - 1]:
        df.loc[df.index[i], 'supertrend_direction'] = 1
      elif df['close'].iloc[i] < df['up_basic'].iloc[i - 1]:
        df.loc[df.index[i], 'supertrend_direction'] = -1
      else:
        df.loc[df.index[i], 'supertrend_direction'] = df[
            'supertrend_direction'
        ].iloc[i - 1]

    # Estocástico normal (se mantiene por compatibilidad)
    low_min = df['low'].rolling(window=14).min()
    high_max = df['high'].rolling(window=14).max()
    df['stoch_k'] = ((df['close'] - low_min) / (high_max - low_min)) * 100
    df['stoch_d'] = df['stoch_k'].rolling(window=3).mean()

    # NUEVO: Cálculo de Stochastic RSI (Stoch RSI)
    stoch_rsi_ind = ta.momentum.StochRSIIndicator(
        df['close'], window=14, smooth1=3, smooth2=3
    )
    df['stoch_rsi_k'] = stoch_rsi_ind.stochrsi_k() * 100
    df['stoch_rsi_d'] = stoch_rsi_ind.stochrsi_d() * 100

    e10, e20, e55 = df['ema10'].iloc[-1], df['ema20'].iloc[-1], df['ema55'].iloc[-1]
    st_dir = df['supertrend_direction'].iloc[-1]
    st_dir_prev = df['supertrend_direction'].iloc[-2]

    stoch_k = df['stoch_k'].iloc[-1]
    stoch_d = df['stoch_d'].iloc[-1]
    stoch_k_prev = df['stoch_k'].iloc[-2]
    stoch_d_prev = df['stoch_d'].iloc[-2]

    stoch_rsi_k = df['stoch_rsi_k'].iloc[-1]
    stoch_rsi_d = df['stoch_rsi_d'].iloc[-1]

    adx = df['adx'].iloc[-1]
    plus_di = df['plus_di'].iloc[-1]
    minus_di = df['minus_di'].iloc[-1]
    rsi = df['rsi'].iloc[-1]
    mfi = df['mfi'].iloc[-1]
    atr = df['atr'].iloc[-1] if not df['atr'].empty else (precio * 0.02)

    supertrend_buy = (st_dir_prev == -1) and (st_dir == 1)
    supertrend_sell = (st_dir_prev == 1) and (st_dir == -1)

    cruce_alcista_estocastico = (stoch_k_prev <= stoch_d_prev) and (
        stoch_k > stoch_d
    )
    cruce_bajista_estocastico = (stoch_k_prev >= stoch_d_prev) and (
        stoch_k < stoch_d
    )

    soporte_key, resistencia_key = calcular_soportes_resistencias(df, precio)

    es_alcista_flexible = (precio > e55) and (st_dir == 1)
    es_bajista_flexible = (precio < e55) and (st_dir == -1)

    adx_direccion = 'ALCISTA 🟢' if plus_di > minus_di else 'BAJISTA 🔴'
    adx_fuerza = 'Fuerte 💪' if adx >= 26 else 'Débil / Rango 😴'

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
        'supertrend_estado': '🟢 ALCISTA' if st_dir == 1 else '🔴 BAJISTA',
        'stoch_estado': '🟢 COMPRA' if stoch_k > stoch_d else '🔴 VENTA',
        'stoch_k': stoch_k,
        'stoch_rsi_k': stoch_rsi_k,
        'stoch_rsi_d': stoch_rsi_d,
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
  if (
      '1h' not in analisis_tf
      or '4h' not in analisis_tf
      or '1d' not in analisis_tf
  ):
    return None

  d1 = analisis_tf['1d']
  h4 = analisis_tf['4h']
  h1 = analisis_tf['1h']

  precio_act = h1['precio']
  atr_act = h1['atr']

  sniper_res = []

  # ADX optimizado y rangos de RSI seguros
  adx_aprobado_long = h1['adx'] >= 12 and h1['rsi'] > 25 and h1['rsi'] < 80
  adx_aprobado_short = h1['adx'] >= 12 and h1['rsi'] > 20 and h1['rsi'] < 75

  # FILTRO 4H (SuperTrend, EMA y RSI sincronizado)
  h4_alcista_real = (
      (h4['supertrend_estado'] == '🟢 ALCISTA')
      and (h4['precio'] > h4['ema20'])
      and (h4['rsi'] < 75)
  )
  h4_bajista_real = (
      (h4['supertrend_estado'] == '🔴 BAJISTA')
      and (h4['precio'] < h4['ema20'])
      and (h4['rsi'] > 25)
  )

  # GATILLOS HÍBRIDOS 1H
  gatillo_1h_long = h1.get('supertrend_buy', False) or (
      (h1['supertrend_estado'] == '🟢 ALCISTA') and h1['cierra_arriba_ema10']
  )
  gatillo_1h_short = h1.get('supertrend_sell', False) or (
      (h1['supertrend_estado'] == '🔴 BAJISTA') and h1['cierra_abajo_ema10']
  )

  # NUEVO: CONDICIÓN STOCH RSI 1H (Sin exigir cruce, niveles inferiores para long < 30 y superiores para short > 70)
  filtro_stoch_rsi_long = h1['stoch_rsi_k'] < 50
  filtro_stoch_rsi_short = h1['stoch_rsi_k'] > 50

  # FILTRO DE FLUJO DE DINERO (MFI)
  filtro_mfi_long = h1['mfi'] > 50
  filtro_mfi_short = h1['mfi'] < 50

  # FILTRO ANTI-PERSECUCIÓN 1H (Reducido a 1.5x ATR)
  distancia_1h_ema = abs(h1['precio'] - h1['ema10'])
  max_extension_1h = h1['atr'] * 1.5
  filtro_1h_no_extendido = distancia_1h_ema <= max_extension_1h

  # FILTRO ANTI-PERSECUCIÓN 4H (Máximo 1.5x ATR respecto a la EMA 10 en 4H)
  distancia_4h_ema = abs(h4['precio'] - h4['ema10'])
  max_extension_4h = h4['atr'] * 1.5
  filtro_4h_no_extendido = distancia_4h_ema <= max_extension_4h

  # Filtro simple de "Sobrecompra/Sobreventa Extrema"
  filtro_rsi_no_extremo_long = h1['rsi'] < 85
  filtro_rsi_no_extremo_short = h1['rsi'] > 15

  # GATILLOS FINALES 10X
  gatillo_long_10x = (
      d1['es_alcista']
      and h4_alcista_real
      and adx_aprobado_long
      and gatillo_1h_long
      and filtro_stoch_rsi_long  # <--- Condición Stoch RSI Long añadida
      and filtro_mfi_long
      and filtro_1h_no_extendido
      and filtro_4h_no_extendido
      and filtro_rsi_no_extremo_long
  )

  if gatillo_long_10x:
    sl_final = h1['soporte'] - (1.0 * atr_act)
    pct_sl = abs((precio_act - sl_final) / precio_act) * 100

    if pct_sl <= 3.0:
      riesgo = precio_act - sl_final
      tp1 = precio_act + (riesgo * 1.0)
      tp2 = precio_act + (riesgo * 1.5)
      tp3 = precio_act + (riesgo * 2.0)

      ratio_actual = (tp1 - precio_act) / riesgo if riesgo > 0 else 0

      if riesgo > 0 and ratio_actual >= 1.2:
        sniper_res.append({
            'symbol': simbolo_limpio,
            'tipo': 'LONG 🟢',
            'precio': precio_act,
            'sl': sl_final,
            'pct_sl': pct_sl,
            'tp1': tp1,
            'pct_tp1': abs((tp1 - precio_act) / precio_act) * 100,
            'tp2': tp2,
            'pct_tp2': abs((tp2 - precio_act) / precio_act) * 100,
            'tp3': tp3,
            'pct_tp3': abs((tp3 - precio_act) / precio_act) * 100,
            'supertrend': h1['supertrend_estado'],
            'rr': f'1:{ratio_actual:.2f}',
            'motivos': [
                'Alineación alcista estructural confirmada',
                'SuperTrend 1H en impulso positivo',
                (
                    'Stoch RSI 1H en niveles inferiores'
                    f' ({h1["stoch_rsi_k"]:.1f})'
                ),
                'MFI confirma flujo de entrada de capital',
            ],
        })

  gatillo_short_10x = (
      d1['es_bajista']
      and h4_bajista_real
      and adx_aprobado_short
      and gatillo_1h_short
      and filtro_stoch_rsi_short  # <--- Condición Stoch RSI Short añadida
      and filtro_mfi_short
      and filtro_1h_no_extendido
      and filtro_4h_no_extendido
      and filtro_rsi_no_extremo_short
  )

  if gatillo_short_10x:
    sl_final = h1['resistencia'] + (1.0 * atr_act)
    pct_sl = abs((sl_final - precio_act) / precio_act) * 100

    if pct_sl <= 3.0:
      riesgo = sl_final - precio_act
      tp1 = precio_act - (riesgo * 1.0)
      tp2 = precio_act - (riesgo * 1.5)
      tp3 = precio_act - (riesgo * 2.0)

      ratio_actual = (precio_act - tp1) / riesgo if riesgo > 0 else 0

      if riesgo > 0 and ratio_actual >= 1.2:
        sniper_res.append({
            'symbol': simbolo_limpio,
            'tipo': 'SHORT 🔴',
            'precio': precio_act,
            'sl': sl_final,
            'pct_sl': pct_sl,
            'tp1': tp1,
            'pct_tp1': abs((precio_act - tp1) / precio_act) * 100,
            'tp2': tp2,
            'pct_tp2': abs((precio_act - tp2) / precio_act) * 100,
            'tp3': tp3,
            'pct_tp3': abs((precio_act - tp3) / precio_act) * 100,
            'supertrend': h1['supertrend_estado'],
            'rr': f'1:{ratio_actual:.2f}',
            'motivos': [
                'Alineación bajista estructural confirmada',
                'SuperTrend 1H en impulso negativo',
                (
                    'Stoch RSI 1H en niveles superiores'
                    f' ({h1["stoch_rsi_k"]:.1f})'
                ),
                'MFI confirma salida de capital',
            ],
        })

  return sniper_res


# ==========================================
# 5. FUNCIONES DE ESCANEO / CONSULTA MANUAL
# ==========================================
def obtener_pares_top():
  try:
    exchange.load_markets()
    tickers = exchange.fetch_tickers()
    estables_ignoradas = [
        'USDC',
        'USDT',
        'BUSD',
        'FDUSD',
        'EUR',
        'DAI',
        'TUSD',
        'USD1',
    ]

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
    logging.error(f'Error obteniendo pares top: {e}')
    return []


def analizar_cripto_individual(ticker_raw):
  ticker = ticker_raw.upper().replace('$', '').replace('USDT', '') + '/USDT'
  simbolo_limpio = ticker.split('/')[0]

  temporalidades = ['15m', '1h', '4h', '1d', '1w']
  msj = f'🤖 **BOT ACTIVO ✅**\n\n📊 *ANÁLISIS TÉCNICO DETALLADO: ${simbolo_limpio}*\n\n'

  for tf in temporalidades:
    res = analizar_par_completo(ticker, tf)
    if res is not None:
      msj += f'• *Temporalidad {tf.upper()}*:\n'
      msj += f"  - Precio: `{fmt_precio(res['precio'])}`\n"
      msj += f"  - SuperTrend: `{res['supertrend_estado']}`\n"
      msj += f"  - RSI: `{res['rsi']:.1f}` | MFI: `{res['mfi']:.1f}`\n"
      msj += f"  - Stoch RSI (K): `{res['stoch_rsi_k']:.1f}`\n"
      msj += f"  - ADX: `{res['adx']:.1f}` ({res['adx_fuerza']})\n"
      msj += (
          f"  - Soporte: `{fmt_precio(res['soporte'])}` | Resistencia:"
          f" `{fmt_precio(res['resistencia'])}`\n\n"
      )
    else:
      msj += f'• *Temporalidad {tf.upper()}*: Sin datos suficientes.\n\n'

  enviar_telegram(msj)


def evaluar_trade_manual(ticker_raw):
  ticker = ticker_raw.upper().replace('$', '').replace('USDT', '') + '/USDT'
  simbolo_limpio = ticker.split('/')[0]

  temporalidades = ['15m', '1h', '4h', '1d', '1w']
  analisis_tf = {}

  for tf in temporalidades:
    res = analizar_par_completo(ticker, tf)
    if res is None:
      enviar_telegram(
          '🤖 **BOT ACTIVO ✅**\n\n❌ No se pudo encontrar o analizar la cripto'
          f' `{ticker_raw}` en BingX.'
      )
      return
    analisis_tf[tf] = res

  sniper = evaluar_todas_las_estrategias(simbolo_limpio, analisis_tf)

  msj = (
      '🤖 **BOT ACTIVO ✅**\n\n🎯 *EVALUACIÓN MANUAL DE TRADE:'
      f' ${simbolo_limpio}*\n\n'
  )

  if sniper:
    for op in sniper:
      msj += (
          '⚡ *ESTRATEGIA SNIPER 10X'
          f" {op['tipo']}: APROBADA* _(R:R {op['rr']})_\n"
      )
      msj += f"🔮 *SuperTrend:* `{op['supertrend']}`\n"
      msj += f"💵 *Entrada:* `{fmt_precio(op['precio'])}`\n"
      msj += f"🛑 *Stop Loss:* `{fmt_precio(op['sl'])}` _(-{op['pct_sl']:.2f}%)_\n"
      msj += f"🎯 *TP1:* `{fmt_precio(op['tp1'])}` _(+{op['pct_tp1']:.2f}%)_\n"
      msj += f"🎯 *TP2:* `{fmt_precio(op['tp2'])}` _(+{op['pct_tp2']:.2f}%)_\n"
      msj += f"🎯 *TP3:* `{fmt_precio(op['tp3'])}` _(+{op['pct_tp3']:.2f}%)_\n"
      msj += '📋 *Condiciones Cumplidas:*\n'
      for m in op.get('motivos', []):
        msj += f'  • {m}\n'
      msj += '\n'
  else:
    msj += (
        '⚪ *SNIPER 10X:* No cumple con las reglas actuales (o Stoch RSI fuera de'
        ' rango, R:R menor a 1.2, o Stop Loss > 3%).\n\n'
    )

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
  sniper = evaluar_todas_las_estrategias(simbolo_limpio, analisis_tf)
  return sniper


def escanear_senales_sniper_manual():
  enviar_telegram(
      '🤖 **BOT ACTIVO ✅**\n\n🔍 Escaneando todo el mercado concurrentemente en'
      ' busca de entradas Sniper...'
  )

  pares_filtrados = obtener_pares_top()
  if not pares_filtrados:
    enviar_telegram(
        '🤖 **BOT ACTIVO ✅**\n\n❌ Error al obtener los pares del mercado en este'
        ' momento.'
    )
    return

  entradas_sniper = []

  with ThreadPoolExecutor(max_workers=8) as executor:
    futures = {
        executor.submit(procesar_par_paralelo, par): par
        for par in pares_filtrados
    }
    for future in as_completed(futures):
      try:
        sniper = future.result()
        if sniper:
          entradas_sniper.extend(sniper)
      except Exception as e:
        logging.error(f'Error procesando hilo de par: {e}')

  enviar_resultados_escaneo(entradas_sniper)


def enviar_resultados_escaneo(entradas_sniper):
  if not entradas_sniper:
    enviar_telegram(
        '🤖 **BOT ACTIVO ✅**\n\n❌ *NO HAY ENTRADAS ACTIVAS*\n\nEn este'
        ' momento ninguna criptomoneda cumple con las condiciones.'
    )
    return

  if entradas_sniper:
    msj_sniper = (
        '🤖 **BOT ACTIVO ✅**\n\n⚡ *ENTRADAS SNIPER 10X DETECTADAS:* ⚡\n\n'
    )
    for op in entradas_sniper[:5]:
      msj_sniper += (
          f"🪙 *{op['symbol']}* -> *{op['tipo']}* _(R:R {op['rr']})_\n"
      )
      msj_sniper += f"🔮 *SuperTrend:* `{op['supertrend']}`\n"
      msj_sniper += f"💵 *Entrada:* `{fmt_precio(op['precio'])}`\n"
      msj_sniper += f"🛑 *Stop Loss:* `{fmt_precio(op['sl'])}` _(-{op['pct_sl']:.2f}%)_\n"
      msj_sniper += f"🎯 *TP1:* `{fmt_precio(op['tp1'])}` _(+{op['pct_tp1']:.2f}%)_\n"
      msj_sniper += f"🎯 *TP2:* `{fmt_precio(op['tp2'])}` _(+{op['pct_tp2']:.2f}%)_\n"
      msj_sniper += f"🎯 *TP3:* `{fmt_precio(op['tp3'])}` _(+{op['pct_tp3']:.2f}%)_\n"
      msj_sniper += '📋 *Condiciones Cumplidas:*\n'
      for m in op.get('motivos', []):
        msj_sniper += f'  • {m}\n'
      msj_sniper += '\n'
    enviar_telegram(msj_sniper)


# ==========================================
# 7. ESCUCHADOR DE TELEGRAM BLINDADO
# ==========================================
def escuchar_mensajes_telegram():
  url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates'
  offset = None

  try:
    init_resp = requests.get(url, params={'timeout': 1}, timeout=5).json()
    if init_resp.get('ok') and init_resp.get('result'):
      offset = init_resp['result'][-1]['update_id'] + 1
  except Exception:
    pass

  while True:
    try:
      params = {'timeout': 15, 'offset': offset}
      resp = requests.get(url, params=params, timeout=20).json()

      if resp.get('ok'):
        for result in resp.get('result', []):
          offset = result['update_id'] + 1
          message = result.get('message', {})
          text = message.get('text', '').strip()

          if text.startswith('/analizar'):
            partes = text.split()
            if len(partes) > 1:
              ticker = partes[1]
              enviar_telegram(
                  '🤖 **BOT ACTIVO ✅**\n\n⏳ Realizando análisis exhaustivo para'
                  f' `${ticker.upper()}`...'
              )
              analizar_cripto_individual(ticker)
            else:
              enviar_telegram(
                  '🤖 **BOT ACTIVO ✅**\n\nℹ️ Indica la moneda. Ejemplo:'
                  ' `/analizar BTC`'
              )

          elif text.startswith('/trade'):
            partes = text.split()
            if len(partes) > 1:
              ticker = partes[1]
              enviar_telegram(
                  '🤖 **BOT ACTIVO ✅**\n\n⏳ Evaluando estrategia Sniper para'
                  f' `${ticker.upper()}`...'
              )
              evaluar_trade_manual(ticker)
            else:
              enviar_telegram(
                  '🤖 **BOT ACTIVO ✅**\n\nℹ️ Indica la moneda. Ejemplo:'
                  ' `/trade BTC`'
              )

          elif text.startswith('/comprobar') or text.startswith('/senales'):
            hilo_comprobar = threading.Thread(
                target=escanear_senales_sniper_manual, daemon=True
            )
            hilo_comprobar.start()
    except Exception as e:
      logging.error(f'Error en bucle de Telegram: {e}')
    time.sleep(1)


# ==========================================
# 8. ESCANEO AUTOMÁTICO GENERAL (CADA 2 HORAS)
# ==========================================
ARCHIVO_BLOQUEO = 'ultimo_escaneo.txt'


def analizar_mercado():
  if os.path.exists(ARCHIVO_BLOQUEO):
    tiempo_archivo = os.path.getmtime(ARCHIVO_BLOQUEO)
    if (time.time() - tiempo_archivo) < 1800:
      return

  try:
    with open(ARCHIVO_BLOQUEO, 'w') as f:
      f.write(str(time.time()))
  except Exception:
    pass

  logging.info('🔎 Escaneo automático de mercado iniciado...')

  try:
    pares_filtrados = obtener_pares_top()
    if not pares_filtrados:
      return

    entradas_sniper = []

    with ThreadPoolExecutor(max_workers=8) as executor:
      futures = {
          executor.submit(procesar_par_paralelo, par): par
          for par in pares_filtrados
      }
      for future in as_completed(futures):
        try:
          sniper = future.result()
          if sniper:
            entradas_sniper.extend(sniper)
        except Exception as e:
          logging.error(f'Error en tarea paralela automática: {e}')

    enviar_resultados_escaneo(entradas_sniper)
    logging.info('✅ Escaneo automático completado.')

  except Exception as e:
    logging.error(f'Error en el escaneo general automático: {e}')


# ==========================================
# 9. BUCLE PRINCIPAL
# ==========================================
if __name__ == '__main__':
  lock_file = 'app.lock'
  if os.path.exists(lock_file):
    if (time.time() - os.path.getmtime(lock_file)) < 10:
      sys.exit(0)

  try:
    with open(lock_file, 'w') as f:
      f.write(str(os.getpid()))
  except Exception:
    pass

  hilo_telegram = threading.Thread(
      target=escuchar_mensajes_telegram, daemon=True
  )
  hilo_telegram.start()

  try:
    symbol_btc = 'BTC/USDT'
    temporalidades = ['15m', '1h', '4h', '1d', '1w']
    analisis_btc = {}

    for tf in temporalidades:
      res_tf = analizar_par_completo(symbol_btc, tf)
      if res_tf:
        analisis_btc[tf] = res_tf

    if '1h' in analisis_btc:
      precio_btc = analisis_btc['1h']['precio']
      msj_inicio = '🤖 **BOT ACTIVO ✅**\n\n'
      msj_inicio += (
          f'🪙 **Bitcoin (BTC)** -> Precio Actual:'
          f' `{fmt_precio(precio_btc)}` USDT\n\n'
      )
      msj_inicio += '📊 **Estado en Temporalidades (Bot Sniper 10X):**\n'

      for tf in ['1h', '4h', '1d', '1w']:
        if tf in analisis_btc:
          data = analisis_btc[tf]
          tendencia = data['supertrend_estado']
          rsi_val = data['rsi']
          stoch_rsi_val = data['stoch_rsi_k']
          adx_val = data['adx']
          fuerza_adx = data['adx_fuerza']

          msj_inicio += (
              f'• **{tf.upper()}**: SuperTrend {tendencia} | RSI:'
              f' `{rsi_val:.1f}` | Stoch RSI: `{stoch_rsi_val:.1f}` | ADX:'
              f' `{adx_val:.1f}` ({fuerza_adx})\n'
          )

      enviar_telegram(msj_inicio)
    else:
      enviar_telegram(
          '🤖 **BOT ACTIVO ✅**\n\nEl bot se ha iniciado correctamente, pero no'
          ' se pudo obtener el análisis preliminar de BTC.'
      )
  except Exception as e:
    enviar_telegram(
        '🤖 **BOT ACTIVO ✅**\n\nEl bot se ha iniciado correctamente (Error al'
        f' consultar BTC: {e})'
    )

  logging.info('🚀 Bot actualizado, concurrente y listo.')

  analizar_mercado()

  while True:
    time.sleep(1800)
    try:
      with open(lock_file, 'w') as f:
        f.write(str(os.getpid()))
    except Exception:
      pass
    analizar_mercado()
