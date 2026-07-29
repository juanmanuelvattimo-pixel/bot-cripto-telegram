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
# 2. INICIALIZAR EXCHANGE
# ==========================================
exchange = ccxt.bingx({
    'enableRateLimit': True,
    'timeout': 5000,
})

# ==========================================
# 3. MOTOR COMPLETO: EMAs + CIPHER B + ADX + MOMENTUM + ORACLE + ATR
# ==========================================
def analizar_par_completo(symbol, timeframe):
    try:
        limite_velas = 30 if timeframe == '1w' else 80
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limite_velas)
        if not ohlcv or len(ohlcv) < 55:
            return None
        
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        # --- 1. EMAs 10, 20, 55 ---
        df['ema10'] = ta.trend.ema_indicator(df['close'], window=10)
        df['ema20'] = ta.trend.ema_indicator(df['close'], window=20)
        df['ema55'] = ta.trend.ema_indicator(df['close'], window=min(55, len(df)-1))
        
        # --- 2. ADX (Fuerza) ---
        adx_ind = ta.trend.ADXIndicator(df['high'], df['low'], df['close'], window=14)
        df['adx'] = adx_ind.adx()
        
        # --- 3. MOMENTUM ---
        df['momentum'] = ta.momentum.roc(df['close'], window=10)
        
        # --- 4. CIPHER B (WaveTrend WT1 y WT2) ---
        ap3 = (df['high'] + df['low'] + df['close']) / 3
        esa = ta.trend.ema_indicator(ap3, window=10)
        d = ta.trend.ema_indicator((ap3 - esa).abs(), window=10)
        ci = (ap3 - esa) / (0.015 * d)
        df['wt1'] = ta.trend.ema_indicator(ci, window=21)
        df['wt2'] = ta.trend.sma_indicator(df['wt1'], window=4)
        
        # --- 5. ORACLE INDICATOR (Cinta de Tendencia + Señal de Compra/Venta) ---
        # Basado en la convergencia de medias rápidas/lentas (Oracle Matrix Ribbon)
        df['oracle_fast'] = ta.trend.ema_indicator(df['close'], window=8)
        df['oracle_slow'] = ta.trend.ema_indicator(df['close'], window=13)
        df['oracle_ribbon'] = df['oracle_fast'] > df['oracle_slow']
        
        # --- 6. ATR (Para SL y TP) ---
        df['atr'] = ta.volatility.average_true_range(df['high'], df['low'], df['close'], window=14)

        # Tomar los últimos valores
        precio = df['close'].iloc[-1]
        e10, e20, e55 = df['ema10'].iloc[-1], df['ema20'].iloc[-1], df['ema55'].iloc[-1]
        wt1, wt2 = df['wt1'].iloc[-1], df['wt2'].iloc[-1]
        wt1_prev, wt2_prev = df['wt1'].iloc[-2], df['wt2'].iloc[-2]
        adx = df['adx'].iloc[-1]
        mom = df['momentum'].iloc[-1]
        atr = df['atr'].iloc[-1]
        
        # Estado del Oracle
        oracle_actual = df['oracle_ribbon'].iloc[-1]
        oracle_previo = df['oracle_ribbon'].iloc[-2]
        
        # Señal directa de entrada/salida del Oracle
        oracle_buy = (not oracle_previo) and oracle_actual   # Entrada Compra Oracle
        oracle_sell = oracle_previo and (not oracle_actual)  # Salida / Entrada Venta Oracle

        # --- EVALUACIÓN GENERAL DE TENDENCIA ---
        ema_alcista = (precio > e55) and (e10 > e20)
        cipher_alcista = (wt1 > wt2)
        cruce_reciente_alcista = (wt1_prev <= wt2_prev) and (wt1 > wt2)
        
        ema_bajista = (precio < e55) and (e10 < e20)
        cipher_bajista = (wt1 < wt2)
        cruce_reciente_bajista = (wt1_prev >= wt2_prev) and (wt1 < wt2)

        # Condición Ponderada Alcista (Incluye Oracle en Verde)
        es_alcista = ema_alcista and cipher_alcista and oracle_actual and (mom > 0)
        # Condición Ponderada Bajista (Incluye Oracle en Rojo)
        es_bajista = ema_bajista and cipher_bajista and (not oracle_actual) and (mom < 0)

        # Retroceso a zona de valor (cerca de EMA 20 o 55)
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
# 4. RASTREO Y CLASIFICACIÓN
# ==========================================
def analizar_mercado():
    hora_escaneo = datetime.now().strftime("%H:%M UTC")
    print(f"🔎 Escaneando mercado (EMAs + Cipher B + Oracle + ADX + Momentum) [{hora_escaneo}]...")
    
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
                
                # Clasificación de Listas Principales
                if all(v == "🟢" for v in estados.values()):
                    longs_perfectos.append(datos_par)
                elif estados['1d'] == "🟢" and estados['1w'] == "🟢":
                    longs_diario_semanal.append(datos_par)

                if all(v == "🔴" for v in estados.values()):
                    shorts_perfectos.append(datos_par)
                elif estados['1d'] == "🔴" and estados['1w'] == "🔴":
                    shorts_diario_semanal.append(datos_par)

                # --- CONDICIÓN SNIPER CON CONFIRMACIÓN DE ORACLE ---
                h1 = analisis_tf['1h']
                precio_act = h1['precio']
                atr_act = h1['atr']

                # Sniper LONG (Gatillo por Cruce Cipher B o Entrada confirmada por Oracle)
                if estados['1d'] == "🟢" and estados['1w'] == "🟢" and (h1['oracle_buy'] or (h1['cruce_alcista'] and h1['oracle_estado'] == "🟢 BUY")):
                    sl = precio_act - (1.5 * atr_act)
                    tp = precio_act + (2.5 * atr_act)
                    entradas_sniper.append({
                        'symbol': simbolo_limpio, 'tipo': 'LONG 🟢',
                        'precio': precio_act, 'sl': sl, 'tp': tp,
                        'oracle': h1['oracle_estado']
                    })
                # Sniper SHORT (Gatillo por Venta/Salida de Oracle o Cruce Bajista Cipher B)
                elif estados['1d'] == "🔴" and estados['1w'] == "🔴" and (h1['oracle_sell'] or (h1['cruce_bajista'] and h1['oracle_estado'] == "🔴 SELL")):
                    sl = precio_act + (1.5 * atr_act)
                    tp = precio_act - (2.5 * atr_act)
                    entradas_sniper.append({
                        'symbol': simbolo_limpio, 'tipo': 'SHORT 🔴',
                        'precio': precio_act, 'sl': sl, 'tp': tp,
                        'oracle': h1['oracle_estado']
                    })

        def enviar_lista_telegram(titulo, descripcion, lista):
            if not lista:
                return
            mensaje = f"{titulo}\n_{descripcion} | Hora: {hora_escaneo}_\n\n"
            for i, res in enumerate(lista[:20], 1):
                mensaje += f"*{i}. {res['symbol']}*\n"
                mensaje += f"1H {res['1h']} | 4H {res['4h']} | 1D {res['1d']} | 1S {res['1w']}\n\n"
            enviar_telegram(mensaje)
            time.sleep(1)

        # Enviar Reportes
        enviar_lista_telegram("🟢 *TOP 20 PERFECCIÓN ALCISTA*", "EMA 10/20/55 + Cipher B + Oracle + ADX", longs_perfectos)
        enviar_lista_telegram("📈 *TOP 20 TENDENCIA ALCISTA (1D + 1S)*", "Tendencia Mayor Alcista Confirmada", longs_diario_semanal)
        enviar_lista_telegram("🔴 *TOP 20 PERFECCIÓN BAJISTA*", "EMA 10/20/55 + Cipher B + Oracle + ADX", shorts_perfectos)
        enviar_lista_telegram("📉 *TOP 20 TENDENCIA BAJISTA (1D + 1S)*", "Tendencia Mayor Bajista Confirmada", shorts_diario_semanal)

        # Enviar Bloque Sniper con Confirmación del Oracle
        if entradas_sniper:
            msj_sniper = f"🎯 *OPORTUNIDADES SNIPER (CONFIRMACIÓN ORACLE)* 🎯\n_Gatillo en 1H | Hora: {hora_escaneo}_\n\n"
            for op in entradas_sniper[:5]:
                msj_sniper += f"🪙 *{op['symbol']}* -> *{op['tipo']}*\n"
                msj_sniper += f"🔮 *Oracle:* `{op['oracle']}`\n"
                msj_sniper += f"💵 *Entrada:* `{op['precio']:.4f}`\n"
                msj_sniper += f"🛑 *SL:* `{op['sl']:.4f}` | 🎯 *TP:* `{op['tp']:.4f}`\n\n"
            enviar_telegram(msj_sniper)

        print("✅ Escaneo completo con Oracle enviado exitosamente.")

    except Exception as e:
        print(f"Error en el escaneo general: {e}")

# ==========================================
# 5. BUCLE DE EJECUCIÓN 24/7
# ==========================================
if __name__ == "__main__":
    enviar_telegram("🤖 *Bot Sistema Sniper + Oracle Activo*")
    analizar_mercado()
    
    while True:
        print("Esperando 1 hora para el próximo ciclo...")
        time.sleep(3600)
        analizar_mercado()
