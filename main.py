import time
import requests
import ccxt
import pandas as pd
import ta
from datetime import datetime

# ==========================================
# 1. CONFIGURACIÓN DE TELEGRAM
# ==========================================
TELEGRAM_TOKEN = "TU_TELEGRAM_TOKEN_AQUI"
CHAT_ID = "TU_CHAT_ID_AQUI"

def enviar_telegram(mensaje):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {
            "chat_id": CHAT_ID,
            "text": mensaje,
            "parse_mode": "Markdown"
        }
        requests.post(url, data=data, timeout=5)
    except Exception as e:
        print(f"Error enviando mensaje a Telegram: {e}")

# ==========================================
# 2. INICIALIZAR EXCHANGE CON TIMEOUT
# ==========================================
exchange = ccxt.bingx({
    'enableRateLimit': True,
    'timeout': 5000,
})

# ==========================================
# 3. MOTOR DE ANÁLISIS OPTIMIZADO
# ==========================================
def analizar_par_completo(symbol, timeframe):
    try:
        # ----------------------------------------------------
        # CASO ESPECIAL: SEMANAL (1W) -> SOLO 1 EMA MACRO
        # ----------------------------------------------------
        if timeframe == '1w':
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe='1w', limit=60)
            if not ohlcv or len(ohlcv) < 5:
                return None
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            
            # EMA Macro (55 o la máxima disponible para monedas nuevas)
            window_ema = min(55, len(df)-1)
            df['ema_macro'] = ta.trend.ema_indicator(df['close'], window=window_ema)
            
            precio_act = df['close'].iloc[-1]
            ema_act = df['ema_macro'].iloc[-1]
            
            return {
                'es_alcista': precio_act > ema_act,
                'es_bajista': precio_act < ema_act,
                'precio': precio_act,
                'atr': precio_act * 0.03
            }

        # ----------------------------------------------------
        # CASO ESTÁNDAR: 1H, 4H, 1D -> COMPLETO (EMAs 10/20/55 + CIPHER B + ORACLE + ADX + MOM)
        # ----------------------------------------------------
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=80)
        if not ohlcv or len(ohlcv) < 20:
            return None
        
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        n_velas = len(df)
        
        # 1. EMAs 10, 20 y 55
        df['ema10'] = ta.trend.ema_indicator(df['close'], window=min(10, n_velas-1))
        df['ema20'] = ta.trend.ema_indicator(df['close'], window=min(20, n_velas-1))
        df['ema55'] = ta.trend.ema_indicator(df['close'], window=min(55, n_velas-1))
        
        # 2. ADX
        win_adx = min(14, n_velas-1)
        adx_ind = ta.trend.ADXIndicator(df['high'], df['low'], df['close'], window=win_adx)
        df['adx'] = adx_ind.adx()
        
        # 3. Momentum (ROC)
        df['momentum'] = ta.momentum.roc(df['close'], window=min(10, n_velas-1))
        
        # 4. Cipher B (WaveTrend WT1 y WT2)
        ap3 = (df['high'] + df['low'] + df['close']) / 3
        esa = ta.trend.ema_indicator(ap3, window=min(10, n_velas-1))
        d = ta.trend.ema_indicator((ap3 - esa).abs(), window=min(10, n_velas-1))
        ci = (ap3 - esa) / (0.015 * d)
        df['wt1'] = ta.trend.ema_indicator(ci, window=min(21, n_velas-1))
        df['wt2'] = ta.trend.sma_indicator(df['wt1'], window=min(4, len(df['wt1'].dropna())-1))
        
        # 5. Oracle (Ribbon 8/13)
        df['oracle_fast'] = ta.trend.ema_indicator(df['close'], window=min(8, n_velas-1))
        df['oracle_slow'] = ta.trend.ema_indicator(df['close'], window=min(13, n_velas-1))
        df['oracle_ribbon'] = df['oracle_fast'] > df['oracle_slow']
        
        # 6. ATR
        df['atr'] = ta.volatility.average_true_range(df['high'], df['low'], df['close'], window=win_adx)

        # Extraer valores finales
        precio = df['close'].iloc[-1]
        e10, e20, e55 = df['ema10'].iloc[-1], df['ema20'].iloc[-1], df['ema55'].iloc[-1]
        wt1 = df['wt1'].iloc[-1] if not df['wt1'].empty else 0
        wt2 = df['wt2'].iloc[-1] if not df['wt2'].empty else 0
        wt1_prev = df['wt1'].iloc[-2] if len(df['wt1']) > 1 else wt1
        wt2_prev = df['wt2'].iloc[-2] if len(df['wt2']) > 1 else wt2
        adx = df['adx'].iloc[-1] if not df['adx'].empty else 0
        mom = df['momentum'].iloc[-1] if not df['momentum'].empty else 0
        atr = df['atr'].iloc[-1] if not df['atr'].empty else (precio * 0.02)
        
        oracle_actual = df['oracle_ribbon'].iloc[-1]
        oracle_previo = df['oracle_ribbon'].iloc[-2] if len(df['oracle_ribbon']) > 1 else oracle_actual
        
        oracle_buy = (not oracle_previo) and oracle_actual
        oracle_sell = oracle_previo and (not oracle_actual)

        # Evaluación de señales
        ema_alcista = (precio > e55) and (e10 > e20)
        cipher_alcista = (wt1 > wt2)
        cruce_reciente_alcista = (wt1_prev <= wt2_prev) and (wt1 > wt2)
        
        ema_bajista = (precio < e55) and (e10 < e20)
        cipher_bajista = (wt1 < wt2)
        cruce_reciente_bajista = (wt1_prev >= wt2_prev) and (wt1 < wt2)

        es_alcista = ema_alcista and cipher_alcista and oracle_actual and (mom >= 0)
        es_bajista = ema_bajista and cipher_bajista and (not oracle_actual) and (mom <= 0)

        en_zona_pullback = (abs(precio - e20) / precio) < 0.015

        return {
            'precio': precio,
            'atr': atr,
            'es_alcista': es_alcista,
            'es_bajista': es_bajista,
            'cruce_alcista': cruce_reciente_alcista,
            'cruce_bajista': cruce_reciente_bajista,
            'oracle_buy': oracle_buy,
            'oracle_sell': oracle_sell,
            'oracle_estado': "🟢 BUY" if oracle_actual else "🔴 SELL",
            'pullback': en_zona_pullback,
            'adx': adx
        }
    except Exception:
        return None

# ==========================================
# 4. ESCANEO Y CLASIFICACIÓN
# ==========================================
def analizar_mercado():
    hora_escaneo = datetime.now().strftime("%H:%M UTC")
    print(f"🔎 Escaneando mercado (EMAs + Cipher B + Oracle + ADX) [{hora_escaneo}]...")
    
    try:
        exchange.load_markets()
        tickers = exchange.fetch_tickers()
        
        pares_usdt = [
            {'symbol': symbol, 'volume': ticker['quoteVolume']}
            for symbol, ticker in tickers.items()
            if symbol.endswith('/USDT') and ticker.get('quoteVolume') is not None
        ]
        
        pares_usdt = sorted(pares_usdt, key=lambda x: x['volume'], reverse=True)
        pares_filtrados = [item['symbol'] for item in pares_usdt[:150]]
        
        longs_perfectos = []
        longs_diario_semanal = []
        shorts_perfectos = []
        shorts_diario_semanal = []
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

                # Evaluador Sniper
                h1 = analisis_tf['1h']
                precio_act = h1['precio']
                atr_act = h1['atr']

                # Sniper Long
                if estados['1d'] == "🟢" and estados['1w'] == "🟢" and (h1['oracle_buy'] or (h1['cruce_alcista'] and h1['oracle_estado'] == "🟢 BUY")):
                    sl = precio_act - (1.5 * atr_act)
                    tp = precio_act + (2.5 * atr_act)
                    entradas_sniper.append({
                        'symbol': simbolo_limpio, 'tipo': 'LONG 🟢',
                        'precio': precio_act, 'sl': sl, 'tp': tp,
                        'oracle': h1['oracle_estado']
                    })
                # Sniper Short
                elif estados['1d'] == "🔴" and estados['1w'] == "🔴" and (h1['oracle_sell'] or (h1['cruce_bajista'] and h1['oracle_estado'] == "🔴 SELL")):
                    sl = precio_act + (1.5 * atr_act)
                    tp = precio_act - (2.5 * atr_act)
                    entradas_sniper.append({
                        'symbol': simbolo_limpio, 'tipo': 'SHORT 🔴',
                        'precio': precio_act, 'sl': sl, 'tp': tp,
                        'oracle': h1['oracle_estado']
                    })

            if count % 30 == 0:
                print(f"⏳ Progreso: {count}/{len(pares_filtrados)} pares procesados...")

        def enviar_lista_telegram(titulo, descripcion, lista):
            if not lista:
                return
            mensaje = f"{titulo}\n_{descripcion} | Hora: {hora_escaneo}_\n\n"
            for i, res in enumerate(lista[:20], 1):
                mensaje += f"*{i}. {res['symbol']}*\n"
                mensaje += f"1H {res['1h']} | 4H {res['4h']} | 1D {res['1d']} | 1S {res['1w']}\n\n"
            enviar_telegram(mensaje)
            time.sleep(1)

        enviar_lista_telegram("🟢 *TOP 20 PERFECCIÓN ALCISTA*", "EMA 10/20/55 + Cipher B + Oracle + ADX", longs_perfectos)
        enviar_lista_telegram("📈 *TOP 20 TENDENCIA ALCISTA (1D + 1S)*", "Tendencia Mayor Alcista Confirmada", longs_diario_semanal)
        enviar_lista_telegram("🔴 *TOP 20 PERFECCIÓN BAJISTA*", "EMA 10/20/55 + Cipher B + Oracle + ADX", shorts_perfectos)
        enviar_lista_telegram("📉 *TOP 20 TENDENCIA BAJISTA (1D + 1S)*", "Tendencia Mayor Bajista Confirmada", shorts_diario_semanal)

        if entradas_sniper:
            msj_sniper = f"🎯 *OPORTUNIDADES SNIPER (CONFIRMACIÓN ORACLE)* 🎯\n_Gatillo en 1H | Hora: {hora_escaneo}_\n\n"
            for op in entradas_sniper[:5]:
                msj_sniper += f"🪙 *{op['symbol']}* -> *{op['tipo']}*\n"
                msj_sniper += f"🔮 *Oracle:* `{op['oracle']}`\n"
                msj_sniper += f"💵 *Entrada:* `{op['precio']:.4f}`\n"
                msj_sniper += f"🛑 *SL:* `{op['sl']:.4f}` | 🎯 *TP:* `{op['tp']:.4f}`\n\n"
            enviar_telegram(msj_sniper)

        print("✅ Escaneo completo finalizado con éxito.")

    except Exception as e:
        print(f"Error en el escaneo general: {e}")

# ==========================================
# 5. BUCLE DE EJECUCIÓN
# ==========================================
if __name__ == "__main__":
    enviar_telegram("🤖 *Bot Sistema Sniper + Oracle Activo*")
    analizar_mercado()
    
    while True:
        print("Esperando 1 hora para el próximo ciclo...")
        time.sleep(3600)
        analizar_mercado()
    
    
