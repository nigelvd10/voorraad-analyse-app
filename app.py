# app.py — één bewerkbare Inventory-tabel met robuuste autosave
import streamlit as st
import pandas as pd
import numpy as np
import altair as alt
import sqlite3, os, re, hashlib
from datetime import date

st.set_page_config(page_title="Voorraad App", layout="wide")

# ============ Styling ============ #
SIDEBAR_CSS = """
<style>
section[data-testid="stSidebar"] {background:#201915;color:#fff;}
.sidebar-title {font-size:28px;font-weight:700;margin:8px 0 16px 4px;color:#fff;}
.nav-btn {display:flex;align-items:center;gap:12px;padding:12px 14px;border-radius:14px;margin:6px 6px;color:#eee;text-decoration:none;font-size:18px;}
.nav-btn:hover {background:rgba(255,255,255,.06);}
.nav-active {background:#3a2f27;color:#fff;}
</style>
"""
st.markdown(SIDEBAR_CSS, unsafe_allow_html=True)

# ============ Utils ============ #
def to_num(x):
    return pd.to_numeric(pd.Series(x).astype(str).str.replace(",",".",regex=False), errors="coerce").fillna(0)

def to_int(x, default=0):
    try:
        v = pd.to_numeric(str(x).replace(",", "."), errors="coerce")
        return int(v) if pd.notna(v) else default
    except Exception:
        return default

def df_hash(df: pd.DataFrame, cols=None) -> str:
    if df is None or len(df)==0:
        return "empty"
    d = df if cols is None else df[cols]
    return hashlib.md5(d.to_csv(index=False).encode()).hexdigest()

def ensure_df(obj, expected_cols=None) -> pd.DataFrame:
    if isinstance(obj, pd.DataFrame):
        df = obj.copy()
    elif isinstance(obj, dict):
        try: df = pd.DataFrame(obj)
        except Exception: df = pd.DataFrame()
    elif isinstance(obj, list):
        try: df = pd.DataFrame(obj)
        except Exception: df = pd.DataFrame()
    else:
        df = pd.DataFrame()
    if expected_cols:
        for c in expected_cols:
            if c not in df.columns:
                df[c] = "" if c in ["EAN","Referentie","Leverancier","Titel"] else 0
        df = df[expected_cols]
    return df

# ============ Kolom-detectie ============ #
PATTERNS = {
    "ean": [r"^\s*ean\s*$", r"\bgtin\b", r"^\s*barcode\s*$", r"^\s*upc\s*$"],
    "ref": [r"^\s*referentie\s*$", r"^ref$", r"\breference\b", r"^\s*sku\s*$",
            r"artikel\s*code", r"artikel\s*nr", r"artikelnummer", r"product\s*ref", r"vendor\s*code"],
    "title": [r"^\s*titel\s*$", r"^\s*naam\s*$", r"product\s*naam", r"title"],
    "stock": [r"vrije\s*voorraad", r"\bvoorraad\b", r"available", r"stock"],
    "sales_total": [r"verkopen\s*\(\s*totaal\s*\)", r"verkopen.*totaal", r"totaal.*verkopen", r"sales\s*total"],
    "forecast_min_4w": [r"verkoopprognose.*4\s*w", r"forecast.*4", r"prognose.*4\s*w",
                        r"verkoopprognose\s*min\s*\(\s*totaal\s*4\s*w\s*\)"],
}
REQ_ORDER = ["EAN","Referentie","Titel","Vrije voorraad","Verkopen (Totaal)","Verkoopprognose min (Totaal 4w)"]

def auto_map(df):
    m={}
    for k, pats in PATTERNS.items():
        for c in df.columns:
            if any(re.search(p, str(c).strip().lower(), re.I) for p in pats):
                m[k]=c; break
    return m

def read_excel_all(file):
    x = pd.read_excel(file, sheet_name=None, dtype=str)
    return {s: d.rename(columns=lambda c: str(c).strip()) for s,d in x.items()}

def build_base(df_raw, sel):
    ref_col = sel.get("ref")
    ref_series = df_raw[ref_col].astype(str) if ref_col else ""
    forecast_raw = to_num(df_raw[sel["forecast_min_4w"]])
    forecast_ceiled = np.ceil(forecast_raw).astype(int)
    df = pd.DataFrame({
        "EAN": df_raw[sel["ean"]].astype(str).str.strip(),
        "Referentie": ref_series,
        "Titel": df_raw[sel["title"]].astype(str),
        "Vrije voorraad": to_num(df_raw[sel["stock"]]),
        "Verkopen (Totaal)": to_num(df_raw[sel["sales_total"]]),
        "Verkoopprognose min (Totaal 4w)": forecast_ceiled,
    })
    return df[REQ_ORDER]

# ============ Database ============ #
DB_PATH = os.path.join(os.getcwd(), "app_data.db")
def db(): return sqlite3.connect(DB_PATH, check_same_thread=False)

def init_db():
    c=db(); cur=c.cursor()
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
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS suppliers (
        Naam TEXT PRIMARY KEY,
        Locatie TEXT DEFAULT '',
        Productietijd_dagen INTEGER DEFAULT 0,
        Levertijd_zee_dagen INTEGER DEFAULT 0,
        Levertijd_lucht_dagen INTEGER DEFAULT 0
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS incoming (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        EAN TEXT,
        Referentie TEXT DEFAULT '',
        Aantal INTEGER DEFAULT 0,
        ETA TEXT,
        Leverancier TEXT DEFAULT '',
        Opmerking TEXT DEFAULT ''
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS base_data (
        EAN TEXT PRIMARY KEY,
        Referentie TEXT,
        Titel TEXT,
        Vrije_voorraad REAL,
        Verkopen_Totaal REAL,
        Verkoopprognose_min_Totaal4w INTEGER
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )""")
    c.commit(); c.close()

def invalidate_caches():
    # Na een save caches legen zodat volgende "echte" refresh vers uit DB leest
    try:
        st.cache_data.clear()
    except Exception:
        pass

# ---- prijzen ---- #
PRICE_COLS = ["EAN","Referentie","Verkoopprijs","Inkoopprijs","Verzendkosten","Overige kosten","Leverancier","MOQ","Levertijd (dagen)"]

@st.cache_data(ttl=0.1)
def load_prices():
    init_db()
    c=db()
    df=pd.read_sql_query(
        "SELECT EAN, Referentie, Verkoopprijs, Inkoopprijs, Verzendkosten, Overige_kosten AS 'Overige kosten', "
        "Leverancier, MOQ, Levertijd_dagen AS 'Levertijd (dagen)' FROM prices", c)
    c.close()
    if df.empty: df=pd.DataFrame(columns=PRICE_COLS)
    for col in ["Verkoopprijs","Inkoopprijs","Verzendkosten","Overige kosten","MOQ","Levertijd (dagen)"]:
        df[col]=pd.to_numeric(df[col], errors="coerce").fillna(0)
    df["EAN"]=df["EAN"].astype(str).str.strip()
    df["Referentie"]=df.get("Referentie","").astype(str).str.strip()
    df["Leverancier"]=df.get("Leverancier","").astype(str)
    return df

def save_prices_full(df_full: pd.DataFrame):
    init_db()
    df_full = ensure_df(df_full, PRICE_COLS)
    df_full["EAN"]=df_full["EAN"].astype(str).str.strip()
    df_full["Referentie"]=df_full["Referentie"].astype(str).str.strip()
    df_full["Leverancier"]=df_full["Leverancier"].astype(str).str.strip()
    for ccol in ["Verkoopprijs","Inkoopprijs","Verzendkosten","Overige kosten","MOQ","Levertijd (dagen)"]:
        df_full[ccol]=pd.to_numeric(df_full[ccol].astype(str).str.replace(",",".",regex=False), errors="coerce").fillna(0)
    c=db(); cur=c.cursor()
    cur.execute("DELETE FROM prices")
    cur.executemany("""
        INSERT OR REPLACE INTO prices
        (EAN, Referentie, Verkoopprijs, Inkoopprijs, Verzendkosten, Overige_kosten, Leverancier, MOQ, Levertijd_dagen)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, [
        (r.EAN, str(r["Referentie"] or ""), float(r["Verkoopprijs"]), float(r["Inkoopprijs"]),
         float(r["Verzendkosten"]), float(r["Overige kosten"]), str(r["Leverancier"] or ""),
         int(r["MOQ"] or 1), int(r["Levertijd (dagen)"] or 0))
        for _, r in df_full.iterrows() if str(r["EAN"]).strip()!=""
    ])
    c.commit(); c.close()
    invalidate_caches()

# ---- suppliers ---- #
@st.cache_data(ttl=0.1)
def load_suppliers():
    init_db()
    c=db()
    df=pd.read_sql_query(
        "SELECT Naam, Locatie, Productietijd_dagen AS 'Productietijd (dagen)', "
        "Levertijd_zee_dagen AS 'Levertijd zee (dagen)', Levertijd_lucht_dagen AS 'Levertijd lucht (dagen)' "
        "FROM suppliers", c)
    c.close()
    if df.empty:
        df=pd.DataFrame(columns=["Naam","Locatie","Productietijd (dagen)","Levertijd zee (dagen)","Levertijd lucht (dagen)"])
    for col in ["Productietijd (dagen)","Levertijd zee (dagen)","Levertijd lucht (dagen)"]:
        df[col]=pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    df["Naam"]=df.get("Naam","").astype(str)
    return df

def save_suppliers(df):
    init_db()
    need=["Naam","Locatie","Productietijd (dagen)","Levertijd zee (dagen)","Levertijd lucht (dagen)"]
    df = ensure_df(df, need)
    df["Naam"]=df["Naam"].astype(str).str.strip()
    df["Locatie"]=df["Locatie"].astype(str).str.strip()
    for col in ["Productietijd (dagen)","Levertijd zee (dagen)","Levertijd lucht (dagen)"]:
        df[col]=pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    c=db(); cur=c.cursor()
    cur.execute("DELETE FROM suppliers")
    cur.executemany(
        "INSERT OR REPLACE INTO suppliers (Naam, Locatie, Productietijd_dagen, Levertijd_zee_dagen, Levertijd_lucht_dagen) VALUES (?,?,?,?,?)",
        [(r.Naam, r.Locatie, int(r["Productietijd (dagen)"]), int(r["Levertijd zee (dagen)"]), int(r["Levertijd lucht (dagen)"]))
         for _, r in df.iterrows() if r.Naam]
    )
    c.commit(); c.close()
    invalidate_caches()

def delete_supplier(name: str):
    c=db(); cur=c.cursor()
    cur.execute("DELETE FROM suppliers WHERE Naam=?", (name,))
    c.commit(); c.close()
    invalidate_caches()

# ---- incoming ---- #
@st.cache_data(ttl=0.1)
def load_incoming():
    init_db()
    c=db()
    df=pd.read_sql_query("SELECT id, EAN, Referentie, Aantal, ETA, Leverancier, Opmerking FROM incoming", c)
    c.close()
    if df.empty:
        df=pd.DataFrame(columns=["id","EAN","Referentie","Aantal","ETA","Leverancier","Opmerking"])
    return df

def add_incoming_row(ean, ref, qty, eta, leverancier, note):
    init_db()
    c=db(); cur=c.cursor()
    cur.execute("INSERT INTO incoming (EAN, Referentie, Aantal, ETA, Leverancier, Opmerking) VALUES (?,?,?,?,?,?)",
                (str(ean).strip(), str(ref or ""), int(qty or 0), str(eta) if eta else "", str(leverancier or ""), str(note or "")))
    c.commit(); c.close()
    invalidate_caches()

def delete_incoming_row(row_id: int):
    c=db(); cur=c.cursor()
    cur.execute("DELETE FROM incoming WHERE id=?", (int(row_id),))
    c.commit(); c.close()
    invalidate_caches()

# ---- base_data ---- #
@st.cache_data(ttl=0.1)
def load_base_df():
    init_db()
    c=db()
    df=pd.read_sql_query("""
        SELECT EAN, Referentie, Titel,
               Vrije_voorraad AS 'Vrije voorraad',
               Verkopen_Totaal AS 'Verkopen (Totaal)',
               Verkoopprognose_min_Totaal4w AS 'Verkoopprognose min (Totaal 4w)'
        FROM base_data
    """, c)
    c.close()
    return None if df.empty else df

def save_base_df(df):
    init_db()
    df = ensure_df(df, REQ_ORDER)
    c=db(); cur=c.cursor()
    cur.execute("DELETE FROM base_data")
    cur.executemany("""
        INSERT OR REPLACE INTO base_data
        (EAN, Referentie, Titel, Vrije_voorraad, Verkopen_Totaal, Verkoopprognose_min_Totaal4w)
        VALUES (?,?,?,?,?,?)
    """, [
        (r.EAN, str(r.Referentie or ""), str(r.Titel or ""),
         float(r["Vrije voorraad"] or 0),
         float(r["Verkopen (Totaal)"] or 0),
         int(r["Verkoopprognose min (Totaal 4w)"] or 0))
        for _, r in df.iterrows() if str(r.EAN).strip()!=""
    ])
    c.commit(); c.close()
    invalidate_caches()

# ============ Basisdata upload UI ============ #
if "base_df" not in st.session_state:
    st.session_state.base_df = load_base_df()

def upload_base_ui():
    st.markdown("#### Upload basisbestand (.xlsx)")
    up = st.file_uploader("Kies Excel", type=["xlsx"], key="basefile")
    if up:
        try:
            sheets = read_excel_all(up)
            sheet = st.selectbox("Kies sheet", list(sheets.keys()))
            raw = sheets[sheet]
            st.dataframe(raw.head(8), use_container_width=True)
            auto = auto_map(raw)
            def pick(lbl, key, optional=False):
                opts = (["— (geen) —"] if optional else []) + list(raw.columns)
                default = auto.get(key); idx = (opts.index(default) if default in opts else 0)
                return st.selectbox(lbl, opts, index=idx)
            sel = {
                "ean": pick("Kolom voor EAN *","ean"),
                "ref": pick("Kolom voor Referentie (aanbevolen)","ref", optional=True),
                "title": pick("Kolom voor Titel *","title"),
                "stock": pick("Kolom voor Vrije voorraad *","stock"),
                "sales_total": pick("Kolom voor Verkopen (Totaal) *","sales_total"),
                "forecast_min_4w": pick("Kolom voor Verkoopprognose min (Totaal 4w) *","forecast_min_4w"),
            }
            ok = all(sel[k] not in ["— (geen) —"] for k in ["ean","title","stock","sales_total","forecast_min_4w"])
            if st.button("✅ Vastleggen", type="primary", disabled=not ok):
                if sel["ref"] == "— (geen) —": sel["ref"] = None
                base = build_base(raw, sel)
                save_base_df(base)
                st.session_state.base_df = base
                st.session_state._base_hash = df_hash(base, REQ_ORDER)
                st.success("Basisdata opgeslagen en herbruikbaar bij refresh.")
        except Exception as e:
            st.error(f"Kon Excel niet lezen: {e}")

# ============ Merge helper ============ #
def merged_inventory(prices_df=None):
    base = st.session_state.base_df
    if base is None: return None
    base = base.copy()
    prices = prices_df if prices_df is not None else load_prices().copy()
    incoming = load_incoming().copy()

    base["EAN"] = base["EAN"].astype(str).str.strip()

    if not incoming.empty:
        try: incoming["ETA"] = pd.to_datetime(incoming["ETA"]).dt.date
        except Exception: pass
        future = incoming[incoming["ETA"].isna() | (incoming["ETA"] >= date.today())]
        inc_sum = future.groupby("EAN")["Aantal"].sum(min_count=1).fillna(0)
    else:
        inc_sum = pd.Series(dtype=float)
    base["Incoming"] = base["EAN"].map(inc_sum).fillna(0)

    cols = ["Referentie","Verkoopprijs","Inkoopprijs","Verzendkosten","Overige kosten","Leverancier","MOQ","Levertijd (dagen)"]
    if not prices.empty:
        base = base.merge(prices[["EAN"]+cols], on="EAN", how="left", suffixes=("_u","_p"))
        if "Referentie_u" in base.columns or "Referentie_p" in base.columns:
            base["Referentie"] = base.get("Referentie_u","").replace("", np.nan).fillna(base.get("Referentie_p",""))
            base.drop(columns=[c for c in ["Referentie_u","Referentie_p"] if c in base.columns], inplace=True, errors="ignore")
    else:
        for ccol in cols:
            base[ccol] = "" if ccol in ["Leverancier","Referentie"] else 0

    for ccol in ["Verkoopprijs","Inkoopprijs","Verzendkosten","Overige kosten","MOQ","Levertijd (dagen)"]:
        base[ccol] = pd.to_numeric(base[ccol].astype(str).str.replace(",",".",regex=False), errors="coerce").fillna(0)

    base["Voorraadwaarde (verkoop)"] = base["Vrije voorraad"] * base["Verkoopprijs"].fillna(0)
    base["Totale kostprijs per stuk"] = base["Inkoopprijs"].fillna(0) + base["Verzendkosten"].fillna(0) + base["Overige kosten"].fillna(0)
    return base

# ============ Sidebar ============ #
with st.sidebar:
    st.markdown('<div class="sidebar-title">Menu</div>', unsafe_allow_html=True)
    pages = ["Home", "Inventory", "Suppliers", "Incoming"]
    icons = {"Home":"🏠","Inventory":"📦","Suppliers":"👥","Incoming":"⬇️"}
    choice = st.session_state.get("_page","Home")
    for p in pages:
        label = f"{icons[p]}  {p}"
        if st.button(label, key=f'nav_{p}', use_container_width=True):
            choice = p
    st.session_state["_page"] = choice

# ===== Session-state buffers =====
if "prices_df" not in st.session_state:
    st.session_state.prices_df = load_prices()
if "_prices_hash" not in st.session_state:
    st.session_state._prices_hash = df_hash(st.session_state.prices_df, PRICE_COLS)
if "_base_hash" not in st.session_state:
    st.session_state._base_hash = df_hash(st.session_state.base_df, REQ_ORDER) if st.session_state.base_df is not None else "empty"
if "last_inventory_df" not in st.session_state:
    # Eerste render: merge uit DB en vastzetten als “waarheid” voor de huidige sessie
    st.session_state.last_inventory_df = merged_inventory(prices_df=st.session_state.prices_df)

# ============ Pages ============ #
if choice == "Home":
    st.header("Home")
    if st.session_state.base_df is None:
        st.info("Nog geen basisdata geladen. Upload hieronder (wordt automatisch opgeslagen).")
        upload_base_ui()
    inv = merged_inventory(prices_df=st.session_state.prices_df)
    if inv is None: st.stop()

    over_units = int(st.secrets.get("over_units_default", 30))
    inv["Status"] = inv.apply(lambda r: (
        "Out of stock" if (float(r.get("Vrije voorraad",0))+float(r.get("Incoming",0)))<=0 else
        ("At risk" if (float(r.get("Vrije voorraad",0))+float(r.get("Incoming",0)))<
         float(r.get("Verkoopprognose min (Totaal 4w)",0)) else
         ("Overstock" if (float(r.get("Vrije voorraad",0))+float(r.get("Incoming",0)))>=
          float(r.get("Verkoopprognose min (Totaal 4w)",0))+over_units else "Healthy"))
    ), axis=1)

    c1,c2,c3,c4 = st.columns(4)
    c1.metric("Totale voorraadwaarde (verkoop)", f"€ {inv['Voorraadwaarde (verkoop)'].sum():,.2f}")
    c2.metric("Artikelen", len(inv))
    c3.metric("Out of stock", int((inv["Status"]=="Out of stock").sum()))
    c4.metric("At risk", int((inv["Status"]=="At risk").sum()))

    st.markdown("**Voorraad gezondheid**")
    order = ["Out of stock","At risk","Healthy","Overstock"]
    counts = inv["Status"].value_counts().reindex(order).fillna(0)
    chart_df = pd.DataFrame({"Status":order,"Aantal":[int(counts.get(s,0)) for s in order]})
    color_scale = alt.Scale(domain=order, range=["#E74C3C", "#F39C12", "#27AE60", "#34495E"])
    chart = (alt.Chart(chart_df).mark_bar().encode(
        x=alt.X("Status:N", sort=order, title="Status"),
        y=alt.Y("Aantal:Q", title="Aantal"),
        color=alt.Color("Status:N", scale=color_scale, legend=None),
    ).properties(height=280))
    st.altair_chart(chart, use_container_width=True)

elif choice == "Inventory":
    st.header("Inventory")

    if st.session_state.base_df is None:
        st.info("Upload eerst je basisbestand (wordt automatisch opgeslagen).")
        upload_base_ui()

    # -------- ÉÉN BEWERKBARE TABEL + AUTOSAVE (buffered) -------- #
    st.subheader("Overzicht producten (bewerkbaar • autosave)")

    # 1) Gebruik ALTIJD de buffer (last_inventory_df) voor weergave
    inv_buffer = st.session_state.last_inventory_df.copy()

    INV_COLS = [
        "EAN","Referentie","Titel","Vrije voorraad","Verkoopprognose min (Totaal 4w)",
        "Verkoopprijs","Inkoopprijs","Verzendkosten","Overige kosten","Leverancier","MOQ","Levertijd (dagen)"
    ]
    for c in INV_COLS:
        if c not in inv_buffer.columns:
            inv_buffer[c] = "" if c in ["Referentie","Titel","Leverancier"] else 0
    inv_view = inv_buffer[INV_COLS].copy()

    options = [""] + sorted(set(load_suppliers().get("Naam", pd.Series(dtype=str)).dropna().astype(str).tolist()))
    col_cfg = {
        "Leverancier": st.column_config.SelectboxColumn("Leverancier", options=options, required=False),
        "EAN": st.column_config.TextColumn("EAN"),
        "Titel": st.column_config.TextColumn("Titel"),
        "Referentie": st.column_config.TextColumn("Referentie"),
        "Vrije voorraad": st.column_config.NumberColumn(step=1, min_value=0),
        "Verkoopprognose min (Totaal 4w)": st.column_config.NumberColumn(step=1, min_value=0),
        "Verkoopprijs": st.column_config.NumberColumn(format="%.2f", min_value=0.0, step=0.01),
        "Inkoopprijs": st.column_config.NumberColumn(format="%.2f", min_value=0.0, step=0.01),
        "Verzendkosten": st.column_config.NumberColumn(format="%.2f", min_value=0.0, step=0.01),
        "Overige kosten": st.column_config.NumberColumn(format="%.2f", min_value=0.0, step=0.01),
        "MOQ": st.column_config.NumberColumn(step=1, min_value=0),
        "Levertijd (dagen)": st.column_config.NumberColumn(step=1, min_value=0),
    }

    edited = st.data_editor(
        inv_view,
        key="one_inventory_table",
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        column_config=col_cfg,
    )

    # 2) Normaliseren
    edited = ensure_df(edited, INV_COLS)
    for c in ["Verkoopprijs","Inkoopprijs","Verzendkosten","Overige kosten"]:
        edited[c] = pd.to_numeric(edited[c].astype(str).str.replace(",",".",regex=False), errors="coerce").fillna(0.0)
    for c in ["Vrije voorraad","Verkoopprognose min (Totaal 4w)","MOQ","Levertijd (dagen)"]:
        edited[c] = pd.to_numeric(edited[c], errors="coerce").fillna(0).astype(int)
    edited["EAN"] = edited["EAN"].astype(str).str.strip()
    edited["Referentie"] = edited["Referentie"].astype(str).str.strip()
    edited["Titel"] = edited["Titel"].astype(str).str.strip()
    edited["Leverancier"] = edited["Leverancier"].astype(str).str.strip()

    # 3) Schrijf EERST naar buffers in session_state (zodat rerun dezelfde data laat zien)
    st.session_state.last_inventory_df = edited.copy()

    # 4) Maak base/prices datasets uit de editor-gegevens
    base_out = edited[["EAN","Referentie","Titel","Vrije voorraad","Verkoopprognose min (Totaal 4w)"]].copy()
    # behoud Verkopen (Totaal) vanuit bestaande base_df
    if st.session_state.base_df is not None and "Verkopen (Totaal)" in st.session_state.base_df.columns:
        sales_map = st.session_state.base_df.set_index("EAN")["Verkopen (Totaal)"]
        base_out["Verkopen (Totaal)"] = base_out["EAN"].map(sales_map).fillna(0)
        base_out = base_out[["EAN","Referentie","Titel","Vrije voorraad","Verkopen (Totaal)","Verkoopprognose min (Totaal 4w)"]]
    else:
        base_out["Verkopen (Totaal)"] = 0
        base_out = base_out[REQ_ORDER]

    prices_out = edited[["EAN","Referentie","Verkoopprijs","Inkoopprijs","Verzendkosten","Overige kosten","Leverancier","MOQ","Levertijd (dagen)"]].copy()

    base_out = base_out[base_out["EAN"].str.len()>0]
    prices_out = prices_out[prices_out["EAN"].str.len()>0]

    new_base_hash = df_hash(base_out, REQ_ORDER)
    new_prices_hash = df_hash(prices_out, PRICE_COLS)

    # 5) Dan pas DB-save (en daarna cache invalidatie). Bij succes updaten we ook de hashes + in-memory kopieën.
    changed = False
    if new_base_hash != st.session_state._base_hash:
        save_base_df(base_out)
        st.session_state.base_df = base_out.copy()
        st.session_state._base_hash = new_base_hash
        changed = True
    if new_prices_hash != st.session_state._prices_hash:
        save_prices_full(prices_out)
        st.session_state.prices_df = prices_out.copy()
        st.session_state._prices_hash = new_prices_hash
        changed = True

    if changed:
        st.toast("Wijzigingen automatisch opgeslagen ✅", icon="✅")

elif choice == "Suppliers":
    st.header("Suppliers")
    sup = load_suppliers()
    with st.form("new_supplier"):
        st.subheader("Nieuwe leverancier toevoegen")
        naam = st.text_input("Naam *")
        locatie = st.text_input("Locatie")
        prod = st.number_input("Gem. productietijd (dagen)", min_value=0, value=0, step=1)
        sea = st.number_input("Levertijd (zee, dagen)", min_value=0, value=0, step=1)
        air = st.number_input("Levertijd (lucht, dagen)", min_value=0, value=0, step=1)
        if st.form_submit_button("Toevoegen"):
            if not naam.strip():
                st.warning("Naam is verplicht.")
            else:
                exists = (sup["Naam"].str.lower()==naam.strip().lower()).any()
                new_row = {"Naam":naam.strip(),"Locatie":locatie,
                           "Productietijd (dagen)":int(prod),
                           "Levertijd zee (dagen)":int(sea),
                           "Levertijd lucht (dagen)":int(air)}
                if exists:
                    sup.loc[sup["Naam"].str.lower()==naam.strip().lower(), :] = new_row
                else:
                    sup = pd.concat([sup, pd.DataFrame([new_row])], ignore_index=True)
                save_suppliers(sup); st.success("Leverancier opgeslagen.")
    st.subheader("Leverancierslijst (automatisch opslaan)")
    ret_sup = st.data_editor(sup, num_rows="dynamic", use_container_width=True, key="sup_editor")
    save_suppliers(ret_sup)

elif choice == "Incoming":
    st.header("Incoming")
    st.subheader("Handmatig zending toevoegen")
    with st.form("incoming_add"):
        ean = st.text_input("EAN *")
        ref = st.text_input("Referentie")
        qty = st.number_input("Aantal *", min_value=0, value=0, step=1)
        eta = st.date_input("ETA (verwachte datum)", value=None)
        leverancier = st.text_input("Leverancier")
        note = st.text_area("Opmerking")
        if st.form_submit_button("Toevoegen"):
            if not ean.strip() or qty<=0:
                st.warning("Vul minimaal EAN en Aantal (>0) in.")
            else:
                add_incoming_row(ean, ref, qty, eta.isoformat() if eta else "", leverancier, note)
                st.success("Zending toegevoegd.")
    st.subheader("Overzicht inkomende zendingen")
    inc = load_incoming()
    if inc.empty:
        st.info("Nog geen inkomende zendingen.")
    else:
        inc_disp = inc.copy()
        try: inc_disp["ETA"] = pd.to_datetime(inc_disp["ETA"]).dt.date
        except Exception: pass
        st.dataframe(inc_disp, use_container_width=True)
        st.markdown("Rij verwijderen")
        del_id = st.number_input("ID (zie kolom 'id')", min_value=0, step=1, value=0)
        if st.button("🗑️ Verwijder ID"):
            if del_id>0 and (inc["id"]==del_id).any():
                delete_incoming_row(int(del_id)); st.success("Verwijderd.")
            else:
                st.warning("Onbekend ID.")
