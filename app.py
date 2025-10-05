# app.py
import streamlit as st
import pandas as pd
import numpy as np
import io, re, os, sqlite3
import altair as alt
from datetime import date, timedelta

# ============ Pagina ============
st.set_page_config(page_title="Voorraad Dashboard", layout="wide")
st.title("📦 Voorraad Dashboard")
st.caption("Upload basisdata, beheer prijzen (blijvend in de app), bekijk gezondheid en maak besteloverzichten.")

# ============ Helpers ============
PATTERNS = {
    "ean": [r"^\s*ean\s*$", r"\bgtin\b", r"product\s*code", r"art(ikel)?\s*(nr|nummer)?"],
    "title": [r"^\s*titel\s*$", r"^\s*naam\s*$", r"product\s*naam", r"title"],
    "stock": [r"vrije\s*voorraad", r"\bvoorraad\b", r"available", r"stock"],
    "sales_total": [r"verkopen\s*\(\s*totaal\s*\)", r"verkopen.*totaal", r"totaal.*verkopen", r"sales\s*total"],
    "forecast_min_4w": [r"verkoopprognose.*4\s*w", r"forecast.*4", r"prognose.*4\s*w",
                        r"verkoopprognose\s*min\s*\(\s*totaal\s*4\s*w\s*\)"],
}
TARGET_NAMES = {
    "ean": "EAN",
    "title": "Titel",
    "stock": "Vrije voorraad",
    "sales_total": "Verkopen (Totaal)",
    "forecast_min_4w": "Verkoopprognose min (Totaal 4w)",
}
REQ_ORDER = ["EAN","Titel","Vrije voorraad","Verkopen (Totaal)","Verkoopprognose min (Totaal 4w)"]

def auto_map(df: pd.DataFrame):
    m = {}
    for key, pats in PATTERNS.items():
        for c in df.columns:
            if any(re.search(p, str(c).strip().lower(), re.I) for p in pats):
                m[key] = c; break
    return m

def num(x):  # veilig numeriek
    return pd.to_numeric(pd.Series(x).astype(str).str.replace(",", ".", regex=False), errors="coerce").fillna(0)

def to_int(x, default=1):
    try:
        v = pd.to_numeric(str(x).replace(",", "."), errors="coerce")
        return int(v) if pd.notna(v) else default
    except Exception:
        return default

def to_float(x, default=0.0):
    try:
        v = pd.to_numeric(str(x).replace(",", "."), errors="coerce")
        return float(v) if pd.notna(v) else default
    except Exception:
        return default

@st.cache_data(show_spinner=False)
def read_excel_all(file):
    xls = pd.read_excel(file, sheet_name=None, dtype=str)
    out = {}
    for s, df in xls.items():
        df = df.copy()
        df.columns = [str(c).strip() for c in df.columns]
        out[s] = df
    return out

def build_base(df_raw, sel):
    df = pd.DataFrame({
        "EAN": df_raw[sel["ean"]].astype(str).str.strip(),
        "Titel": df_raw[sel["title"]].astype(str),
        "Vrije voorraad": num(df_raw[sel["stock"]]),
        "Verkopen (Totaal)": num(df_raw[sel["sales_total"]]),
        "Verkoopprognose min (Totaal 4w)": num(df_raw[sel["forecast_min_4w"]]),
    })
    return df[REQ_ORDER]

# ============ SQLite opslag prijzen ============
DB_PATH = os.path.join(os.getcwd(), "prices.db")
def conn(): return sqlite3.connect(DB_PATH, check_same_thread=False)

def init_prices():
    c = conn()
    cur = c.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS prices (
            EAN TEXT PRIMARY KEY,
            Referentie TEXT DEFAULT '',
            Verkoopprijs REAL DEFAULT 0,
            Inkoopprijs REAL DEFAULT 0,
            Verzendkosten REAL DEFAULT 0,
            Overige_kosten REAL DEFAULT 0,
            Leverancier TEXT DEFAULT '',
            MOQ INTEGER DEFAULT 1,
            Levertijd_dagen INTEGER DEFAULT 0
        )
    """)
    c.commit(); c.close()

@st.cache_data(show_spinner=False)
def load_prices() -> pd.DataFrame:
    init_prices()
    c = conn()
    df = pd.read_sql_query(
        "SELECT EAN, Referentie, Verkoopprijs, Inkoopprijs, Verzendkosten, "
        "Overige_kosten AS 'Overige kosten', Leverancier, MOQ, "
        "Levertijd_dagen AS 'Levertijd (dagen)' FROM prices", c)
    c.close()
    if df.empty:
        df = pd.DataFrame(columns=["EAN","Referentie","Verkoopprijs","Inkoopprijs","Verzendkosten",
                                   "Overige kosten","Leverancier","MOQ","Levertijd (dagen)"])
    for col in ["Verkoopprijs","Inkoopprijs","Verzendkosten","Overige kosten","MOQ","Levertijd (dagen)"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    df["EAN"] = df["EAN"].astype(str).str.strip()
    df["Referentie"] = df.get("Referentie","").astype(str).str.strip()
    return df

def save_prices(df: pd.DataFrame):
    init_prices()
    expected = ["EAN","Referentie","Verkoopprijs","Inkoopprijs","Verzendkosten","Overige kosten","Leverancier","MOQ","Levertijd (dagen)"]
    for c_ in expected:
        if c_ not in df.columns:
            df[c_] = "" if c_ in ["Leverancier","Referentie"] else 0
    df = df[expected].copy()
    df["EAN"] = df["EAN"].astype(str).str.strip()
    df["Referentie"] = df["Referentie"].astype(str).str.strip()
    for c_ in ["Verkoopprijs","Inkoopprijs","Verzendkosten","Overige kosten","MOQ","Levertijd (dagen)"]:
        df[c_] = pd.to_numeric(df[c_].astype(str).str.replace(",",".",regex=False), errors="coerce").fillna(0)

    c = conn(); cur = c.cursor()
    cur.execute("DELETE FROM prices")
    cur.executemany("""
        INSERT OR REPLACE INTO prices
        (EAN, Referentie, Verkoopprijs, Inkoopprijs, Verzendkosten, Overige_kosten, Leverancier, MOQ, Levertijd_dagen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        (r.EAN, str(r["Referentie"] or ""), float(r["Verkoopprijs"]), float(r["Inkoopprijs"]),
         float(r["Verzendkosten"]), float(r["Overige kosten"]), str(r["Leverancier"] or ""),
         int(r["MOQ"] or 1), int(r["Levertijd (dagen)"] or 0))
        for _, r in df.iterrows() if str(r["EAN"]).strip() != ""
    ])
    c.commit(); c.close()

# ============ Benchmarks (4 categorieën) ============
def status_of(row, incoming_qty, over_pct):
    f = float(row.get("Verkoopprognose min (Totaal 4w)", 0) or 0)
    stock_total = float(row.get("Vrije voorraad", 0) or 0) + float(incoming_qty or 0)
    if stock_total <= 0: return "Out of stock"
    if f <= 0: return "Healthy"
    threshold = (1 + over_pct/100.0) * f
    if stock_total < f: return "At risk"
    if stock_total >= threshold: return "Overstock"
    return "Healthy"

def recommend_qty(row, incoming_qty, moq=1):
    f = float(row.get("Verkoopprognose min (Totaal 4w)", 0) or 0)
    stock_total = float(row.get("Vrije voorraad", 0) or 0) + float(incoming_qty or 0)
    if f <= 0: return 0
    target = 1.1 * f
    need = max(0.0, target - stock_total)
    return int(np.ceil(need / max(1, moq)) * max(1, moq))

# ============ State ============
if "base_df" not in st.session_state: st.session_state.base_df = None
if "prices_df" not in st.session_state:
    try: st.session_state.prices_df = load_prices()
    except Exception: st.session_state.prices_df = pd.DataFrame(columns=["EAN","Referentie","Verkoopprijs","Inkoopprijs","Verzendkosten","Overige kosten","Leverancier","MOQ","Levertijd (dagen)"])
if "incoming_df" not in st.session_state:
    st.session_state.incoming_df = pd.DataFrame(columns=["EAN","Aantal","ETA","Leverancier"])

# ============ Sidebar ============
with st.sidebar:
    st.header("⚙️ Instellingen")
    overstock_pct = st.slider("Overstock-drempel (%)", 5, 50, 20)
    target_days = st.slider("Target days of cover", 7, 60, 28)      # optioneel, niet nodig voor benchmarks
    safety_days = st.slider("Safety buffer (dagen)", 0, 30, 7)      # optioneel

    st.markdown("---")
    st.subheader("Imports (optioneel)")
    base_file = st.file_uploader("Upload basisbestand (.xlsx)", type=["xlsx"], key="base")
    prices_upload = st.file_uploader("Upload prijslijst (xlsx/csv)", type=["xlsx","csv"], key="prices_up")
    incoming_file = st.file_uploader("Upload inkomende voorraad (xlsx/csv)", type=["xlsx","csv"], key="incoming")

# ============ Tabs ============
T1, T2, T3, T4 = st.tabs(["📥 Data & Mapping", "📊 Dashboard", "🧾 Besteloverzicht", "🚚 Inkomend"])

# ------- T1 -------
with T1:
    st.subheader("1) Basisdata uploaden & kolommen koppelen")
    if base_file is None:
        st.info("Upload Excel met minimaal: EAN, Titel, Vrije voorraad, Verkopen (Totaal), Verkoopprognose min (Totaal 4w).")
    else:
        try:
            sheets = read_excel_all(base_file)
        except Exception as e:
            st.error(f"❌ Kon Excel niet lezen: {e}")
            st.stop()
        sheet = st.selectbox("Kies sheet", list(sheets.keys()))
        raw = sheets[sheet]
        st.dataframe(raw.head(10), use_container_width=True)

        auto = auto_map(raw)
        def pick(lbl, key):
            opts = ["— kies —"] + list(raw.columns)
            default = auto.get(key); idx = opts.index(default) if default in opts else 0
            return st.selectbox(lbl, opts, index=idx)

        sel = {
            "ean": pick("Kolom voor EAN", "ean"),
            "title": pick("Kolom voor Titel", "title"),
            "stock": pick("Kolom voor Vrije voorraad", "stock"),
            "sales_total": pick("Kolom voor Verkopen (Totaal)", "sales_total"),
            "forecast_min_4w": pick("Kolom voor Verkoopprognose min (Totaal 4w)", "forecast_min_4w"),
        }
        missing = [TARGET_NAMES[k] for k,v in sel.items() if v == "— kies —"]
        if missing:
            st.warning("Selecteer alle vereiste kolommen: " + ", ".join(missing))
        else:
            if st.button("✅ Vastleggen basisdata", type="primary"):
                st.session_state.base_df = build_base(raw, sel)
                st.success("Basisdata opgeslagen.")

    st.markdown("---")
    st.subheader("2) Prijslijst (blijvend in de app – SQLite)")
    st.caption("Je prijzen worden lokaal in de app opgeslagen in het bestand `prices.db`. Geen uploads of downloads nodig.")

    c1, c2, c3 = st.columns([1,1,1])
    with c1:
        if st.button("🔄 Herladen uit opslag"):
            st.session_state.prices_df = load_prices()
            st.success("Prijzen herladen uit opslag.")
    with c2:
        if st.button("💾 Opslaan in opslag", type="primary"):
            try:
                save_prices(st.session_state.prices_df.copy())
                st.success("Prijzen opgeslagen in de app (SQLite).")
                st.cache_data.clear()
            except Exception as e:
                st.error(f"Opslaan mislukt: {e}")
    with c3:
        if st.button("🆕 Lege prijslijst"):
            st.session_state.prices_df = pd.DataFrame({
                "EAN": [], "Referentie": [], "Verkoopprijs": [], "Inkoopprijs": [],
                "Verzendkosten": [], "Overige kosten": [], "Leverancier": [],
                "MOQ": [], "Levertijd (dagen)": [],
            })

    if prices_upload is not None:
        try:
            if prices_upload.name.lower().endswith(".csv"):
                st.session_state.prices_df = pd.read_csv(prices_upload)
            else:
                st.session_state.prices_df = pd.read_excel(prices_upload)
            st.success("Prijslijst geladen in de editor (nog niet opgeslagen).")
        except Exception as e:
            st.error(f"Kon prijslijst niet lezen: {e}")

    st.session_state.prices_df = st.data_editor(
        st.session_state.prices_df,
        use_container_width=True,
        num_rows="dynamic",
        key="prices_editor",
    )

    st.caption(
        "ℹ️ In Streamlit Cloud blijft `prices.db` meestal behouden tussen herstarts, "
        "maar kan verloren gaan bij redeploy/opschalen. Voor 100% zekerheid kun je later "
        "overschakelen op Google Sheets of een echte database."
    )

    st.markdown("---")
    st.subheader("3) (Optioneel) Inkomende voorraad importeren/bewerken")
    st.caption("Kolommen: EAN, Aantal, ETA (YYYY-MM-DD), Leverancier")
    if incoming_file is not None:
        try:
            if incoming_file.name.lower().endswith(".csv"):
                st.session_state.incoming_df = pd.read_csv(incoming_file)
            else:
                st.session_state.incoming_df = pd.read_excel(incoming_file)
            st.success("Inkomende voorraad geladen in de editor.")
        except Exception as e:
            st.error(f"Kon inkomende voorraad niet lezen: {e}")
    st.session_state.incoming_df = st.data_editor(
        st.session_state.incoming_df,
        use_container_width=True,
        num_rows="dynamic",
        key="incoming_editor",
    )

# ------- Merge utility -------
def merged():
    if st.session_state.base_df is None:
        return None
    base = st.session_state.base_df.copy()
    prices = st.session_state.prices_df.copy()
    incoming = st.session_state.incoming_df.copy()

    for df in [prices, incoming]:
        if df is not None and not df.empty and "EAN" in df.columns:
            df["EAN"] = df["EAN"].astype(str).str.strip()
    base["EAN"] = base["EAN"].astype(str).str.strip()

    # Incoming (toekomst)
    if not incoming.empty and "ETA" in incoming.columns:
        try:
            incoming["ETA"] = pd.to_datetime(incoming["ETA"]).dt.date
        except Exception:
            pass
        future = incoming[incoming["ETA"].isna() | (incoming["ETA"] >= date.today())]
        inc_sum = future.groupby("EAN")["Aantal"].sum(min_count=1).fillna(0)
    else:
        inc_sum = pd.Series(dtype=float)
    base["Incoming"] = base["EAN"].map(inc_sum).fillna(0)

    # Prijzen merge (incl. Referentie)
    cols_prices = [c for c in ["Referentie","Verkoopprijs","Inkoopprijs","Verzendkosten",
                               "Overige kosten","Leverancier","MOQ","Levertijd (dagen)"] if c in prices.columns]
    if cols_prices:
        base = base.merge(prices[["EAN"]+cols_prices], on="EAN", how="left")
    for c in ["Verkoopprijs","Inkoopprijs","Verzendkosten","Overige kosten","MOQ","Levertijd (dagen)"]:
        if c not in base.columns: base[c] = 0
    if "Leverancier" not in base.columns: base["Leverancier"] = ""
    if "Referentie" not in base.columns: base["Referentie"] = ""
    for c in ["Verkoopprijs","Inkoopprijs","Verzendkosten","Overige kosten","MOQ","Levertijd (dagen)"]:
        base[c] = pd.to_numeric(base[c].astype(str).str.replace(",",".",regex=False), errors="coerce").fillna(0)

    base["Voorraadwaarde (verkoop)"] = base["Vrije voorraad"] * base["Verkoopprijs"].fillna(0)
    base["Totale kostprijs per stuk"] = base["Inkoopprijs"].fillna(0) + base["Verzendkosten"].fillna(0) + base["Overige kosten"].fillna(0)
    return base

# ------- T2 -------
with T2:
    st.subheader("Overzicht & gezondheid")
    data = merged()
    if data is None:
        st.info("Nog geen basisdata. Ga naar **📥 Data & Mapping**.")
    else:
        # status + KPI
        data["Status"] = data.apply(lambda r: status_of(r, to_float(r.get("Incoming",0),0), overstock_pct), axis=1)
        c1,c2,c3,c4 = st.columns(4)
        c1.metric("Totale voorraadwaarde (verkoop)", f"€ {data['Voorraadwaarde (verkoop)'].sum():,.2f}")
        c2.metric("Artikelen", len(data))
        c3.metric("Out of stock", int((data["Status"]=="Out of stock").sum()))
        c4.metric("At risk", int((data["Status"]=="At risk").sum()))

        st.write("Filter op status:")
        options = ["Out of stock","At risk","Healthy","Overstock"]
        chosen = st.multiselect(" ", options, default=[], label_visibility="collapsed")

        data["Aanbevolen bestelaantal"] = data.apply(lambda r: recommend_qty(r, to_float(r.get("Incoming",0),0), to_int(r.get("MOQ",1),1)), axis=1)
        view = data[data["Status"].isin(chosen)].copy() if chosen else data.copy()

        st.markdown("**Voorraad gezondheid**")
        order = ["Out of stock","At risk","Healthy","Overstock"]
        counts = data["Status"].value_counts().reindex(order).fillna(0)
        chart_df = pd.DataFrame({"Status": order, "Aantal": [int(counts.get(s,0)) for s in order]})
        y_max = max(1, int(chart_df["Aantal"].max()))
        chart = alt.Chart(chart_df).mark_bar().encode(
            x=alt.X("Status:N", sort=order),
            y=alt.Y("Aantal:Q", scale=alt.Scale(domain=(0, y_max)))
        ).properties(height=280)
        st.altair_chart(chart, use_container_width=True)

        st.markdown("**Producten**")
        cols = ["Status","EAN","Referentie","Titel","Vrije voorraad","Incoming","Verkoopprognose min (Totaal 4w)",
                "Aanbevolen bestelaantal","Leverancier","Verkoopprijs","Inkoopprijs","Voorraadwaarde (verkoop)"]
        for c_ in cols:
            if c_ not in view.columns: view[c_] = ""
        st.dataframe(view[cols].sort_values(["Status","Aanbevolen bestelaantal"], ascending=[True, False]), use_container_width=True)

# ------- T3 -------
with T3:
    st.subheader("Maak besteloverzicht / PO")
    data = merged()
    if data is None:
        st.info("Nog geen basisdata. Ga naar **📥 Data & Mapping**.")
    else:
        data["Aanbevolen bestelaantal"] = data.apply(lambda r: recommend_qty(r, to_float(r.get("Incoming",0),0), to_int(r.get("MOQ",1),1)), axis=1)
        df_order = data[data["Aanbevolen bestelaantal"]>0].copy()
        if df_order.empty:
            st.success("Er zijn momenteel geen aanbevelingen om te bestellen.")
        else:
            st.info(f"Er zijn {len(df_order)} regels met een aanbevolen bestelaantal.")
            df_order["Totaal kosten"] = df_order["Aanbevolen bestelaantal"] * df_order["Totale kostprijs per stuk"].fillna(0)
            st.dataframe(df_order[["Leverancier","EAN","Referentie","Titel","Aanbevolen bestelaantal",
                                   "Totale kostprijs per stuk","Totaal kosten"]], use_container_width=True)

            suppliers = sorted(df_order["Leverancier"].fillna("").unique())
            sup = st.selectbox("Kies leverancier voor export", suppliers)
            df_sup = df_order[df_order["Leverancier"]==sup].copy() if sup else df_order.copy()
            if df_sup.empty:
                st.warning("Geen regels voor deze leverancier.")
            else:
                po_cols = ["EAN","Referentie","Titel","Aanbevolen bestelaantal","Totale kostprijs per stuk","Totaal kosten"]
                buf = io.BytesIO()
                with pd.ExcelWriter(buf, engine="openpyxl") as w:
                    df_sup[po_cols].to_excel(w, index=False, sheet_name="Bestelling")
                st.download_button("📥 Download PO (Excel)", buf.getvalue(),
                                   file_name=f"PO_{sup or 'ALLE'}.xlsx",
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                st.download_button("📄 Download PO (CSV)",
                                   df_sup[po_cols].to_csv(index=False).encode("utf-8"),
                                   file_name=f"PO_{sup or 'ALLE'}.csv", mime="text/csv")

# ------- T4 -------
with T4:
    st.subheader("Inkomende zendingen")
    inc = st.session_state.incoming_df.copy()
    if inc.empty:
        st.info("Nog geen inkomende voorraad toegevoegd.")
    else:
        try: inc["ETA"] = pd.to_datetime(inc["ETA"]).dt.date
        except Exception: pass
        st.dataframe(inc, use_container_width=True)

        st.markdown("**Samenvatting komende 30 dagen**")
        try:
            inc_dt = inc.copy(); inc_dt["ETA"] = pd.to_datetime(inc_dt["ETA"])
            horizon = date.today() + timedelta(days=30)
            soon = inc_dt[inc_dt["ETA"].between(pd.to_datetime(date.today()), pd.to_datetime(horizon))]
            sum_soon = soon.groupby("EAN")["Aantal"].sum().sort_values(ascending=False)
            st.bar_chart(sum_soon)
        except Exception:
            st.write("Kan diagram niet tonen (controleer datumformaat).")
