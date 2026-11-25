# app.py
import time, sqlite3, datetime
import requests, pandas as pd, streamlit as st

DB = "trades.db"
con = sqlite3.connect(DB, check_same_thread=False)
con.execute("""CREATE TABLE IF NOT EXISTS trades(
    id INTEGER PRIMARY KEY, datum TEXT, coin TEXT, chain TEXT, adres TEXT,
    inleg REAL, instap REAL, uitstap REAL, risicoscore INTEGER,
    reden TEXT, regels_gevolgd INTEGER, fout TEXT, papier INTEGER)""")

st.set_page_config(page_title="Crypto Discipline Tool", layout="wide")

# ---------- Instellingen ----------
st.sidebar.header("Mijn regels")
budget = st.sidebar.number_input("Totaalbudget (€)", 100.0, step=50.0, value=500.0)
max_pct = st.sidebar.slider("Max % per trade", 1, 10, 3)
stop_pct = st.sidebar.slider("Stop-loss %", 10, 50, 30)
tp_pct = st.sidebar.slider("Inleg terugnemen bij +%", 50, 300, 100)
st.sidebar.info(f"Max positie: €{budget*max_pct/100:.2f}")

tab1, tab2, tab3 = st.tabs(["🔍 Risicoscan", "📝 Logboek", "📊 Dashboard"])

# ---------- Risicoscan ----------
def scan(address):
    r = requests.get(f"[api.dexscreener.com](https://api.dexscreener.com/latest/dex/tokens/{address})", timeout=10)
    pairs = r.json().get("pairs") or []
    if not pairs:
        return None, []
    p = max(pairs, key=lambda x: (x.get("liquidity") or {}).get("usd", 0))
    liq = (p.get("liquidity") or {}).get("usd", 0) or 0
    vol = (p.get("volume") or {}).get("h24", 0) or 0
    mcap = p.get("marketCap") or p.get("fdv") or 0
    tx = (p.get("txns") or {}).get("h24", {})
    buys, sells = tx.get("buys", 0), tx.get("sells", 0)
    age_h = (time.time()*1000 - p.get("pairCreatedAt", time.time()*1000)) / 3.6e6
    change = (p.get("priceChange") or {}).get("h24", 0) or 0

    flags = []  # (punten, tekst)
    if liq < 20_000: flags.append((3, f"Zeer lage liquiditeit (${liq:,.0f})"))
    elif liq < 100_000: flags.append((1, f"Lage liquiditeit (${liq:,.0f})"))
    if mcap and liq/mcap < 0.03: flags.append((2, "Liquiditeit < 3% van marktcap"))
    if age_h < 6: flags.append((3, f"Pair is pas {age_h:.1f} uur oud"))
    elif age_h < 72: flags.append((1, f"Pair is {age_h:.0f} uur oud"))
    if liq and vol/liq > 20: flags.append((2, "Volume extreem t.o.v. liquiditeit (wash trading?)"))
    if buys+sells > 50 and sells/(buys+sells) < 0.15: flags.append((3, "Bijna geen verkopen: mogelijk honeypot"))
    if change > 300: flags.append((2, f"+{change:.0f}% in 24u: je koopt waarschijnlijk de top"))
    if not (p.get("info") or {}).get("socials"): flags.append((1, "Geen socials gekoppeld"))
    p["_meta"] = dict(liq=liq, vol=vol, mcap=mcap, age_h=age_h, change=change)
    return p, flags

with tab1:
    adres = st.text_input("Contractadres")
    if st.button("Scan") and adres:
        try:
            p, flags = scan(adres.strip())
        except Exception as e:
            st.error(f"Fout bij ophalen: {e}"); p = None
        if p:
            m = p["_meta"]; score = sum(f[0] for f in flags)
            st.subheader(f"{p['baseToken']['symbol']} · {p['chainId']} · {p['dexId']}")
            c = st.columns(5)
            c[0].metric("Prijs", f"${p.get('priceUsd')}")
            c[1].metric("Marktcap", f"${m['mcap']:,.0f}")
            c[2].metric("Liquiditeit", f"${m['liq']:,.0f}")
            c[3].metric("Volume 24u", f"${m['vol']:,.0f}")
            c[4].metric("24u", f"{m['change']:.0f}%")
            if score >= 6: st.error(f"Risicoscore {score}: NIET kopen")
            elif score >= 3: st.warning(f"Risicoscore {score}: hoog risico")
            else: st.success(f"Risicoscore {score}: geen grote signalen (niet = veilig!)")
            for pts, t in flags: st.write(f"• ({pts}) {t}")
            st.markdown(f"**Handmatig checken:** [RugCheck](https://rugcheck.xyz/tokens/{adres}) · "
                        f"[Bubblemaps](https://app.bubblemaps.io) · [DexScreener]({p.get('url')})")
            pos = budget*max_pct/100
            st.info(f"Volgens je regels: max €{pos:.2f} inleg, stop-loss bij "
                    f"-{stop_pct}%, inleg terug bij +{tp_pct}%.")
            st.session_state["laatste"] = dict(coin=p['baseToken']['symbol'],
                                               chain=p['chainId'], adres=adres, score=score,
                                               prijs=float(p.get('priceUsd') or 0))
        elif p is None:
            st.warning("Geen data gevonden voor dit adres.")

# ---------- Logboek ----------
with tab2:
    l = st.session_state.get("laatste", {})
    with st.form("trade"):
        c1, c2, c3 = st.columns(3)
        coin = c1.text_input("Coin", l.get("coin", ""))
        inleg = c2.number_input("Inleg €", 0.0, value=budget*max_pct/100)
        papier = c3.checkbox("Paper trade", True)
        instap = c1.number_input("Instapprijs", 0.0, value=l.get("prijs", 0.0), format="%.10f")
        uitstap = c2.number_input("Uitstapprijs (0 = open)", 0.0, format="%.10f")
        regels = c3.checkbox("Regels gevolgd?", True)
        reden = st.text_input("Waarom ingestapt?")
        fout = st.selectbox("Fout (achteraf)", ["geen", "FOMO/te laat", "stop-loss genegeerd",
                            "te grote positie", "te vroeg verkocht", "scam niet herkend", "anders"])
        if st.form_submit_button("Opslaan"):
            if inleg > budget*max_pct/100:
                st.warning("Let op: inleg is groter dan je eigen limiet!")
            con.execute("INSERT INTO trades VALUES(NULL,?,?,?,?,?,?,?,?,?,?,?,?)",
                (datetime.date.today().isoformat(), coin, l.get("chain", ""), l.get("adres", ""),
                 inleg, instap, uitstap, l.get("score", -1), reden, int(regels), fout, int(papier)))
            con.commit(); st.success("Opgeslagen")
    df = pd.read_sql("SELECT * FROM trades ORDER BY id DESC", con)
    st.dataframe(df, use_container_width=True)
    st.download_button("Exporteer CSV (voor AI-analyse)", df.to_csv(index=False), "trades.csv")

# ---------- Dashboard ----------
with tab3:
    df = pd.read_sql("SELECT * FROM trades WHERE uitstap > 0 AND instap > 0", con)
    if df.empty:
        st.info("Nog geen afgesloten trades.")
    else:
        df["rendement_pct"] = (df.uitstap/df.instap - 1)*100
        df["winst_eur"] = df.inleg*df.rendement_pct/100
        c = st.columns(4)
        c[0].metric("Afgesloten trades", len(df))
        c[1].metric("Winrate", f"{(df.rendement_pct>0).mean()*100:.0f}%")
        c[2].metric("Totaal resultaat", f"€{df.winst_eur.sum():.2f}")
        c[3].metric("Regels gevolgd", f"{df.regels_gevolgd.mean()*100:.0f}%")
        st.write("**Resultaat: regels gevolgd vs. niet**")
        st.dataframe(df.groupby("regels_gevolgd").winst_eur.agg(["count", "sum", "mean"]))
        st.write("**Meest gemaakte fouten**")
        st.bar_chart(df[df.fout != "geen"].fout.value_counts())
        st.write("**Resultaat per risicoscore**")
        st.dataframe(df.groupby("risicoscore").rendement_pct.agg(["count", "mean"]))
        st.line_chart(df.winst_eur.cumsum())
