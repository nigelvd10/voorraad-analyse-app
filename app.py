# app.py — Overstock op absolute drempel + All products-chip + forecast afronden omhoog
import streamlit as st
import pandas as pd
import numpy as np
import altair as alt
import sqlite3, os, re
from datetime import date

# =============================
# App setup & styling
# =============================
st.set_page_config(page_title="Voorraad App", layout="wide")

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

# =============================
# Helpers
# =============================
def to_num(x):
    return pd.to_numeric(pd.Series(x).astype(str).str.replace(",",".",regex=False), errors="coerce").fillna(0)

def to_int(x, default=0):
    try:
        v = pd.to_numeric(str(x).replace(",", "."), errors="coerce")
        return int(v) if pd.notna(v) else default
    except Exception:
        return default

# Kolomherkenning incl. Referentie
PATTERNS = {
    "ean": [
        r"^\s*ean\s*$",
        r"\bgtin\b",
        r"^\s*barcode\s*$",
        r"^\s*upc\s*$",
    ],
    "ref": [
        r"^\s*referentie\s*$",
        r"^ref$",
        r"\breference\b",
        r"^\s*sku\s*$",
        r"artikel\s*code",
        r"artikel\s*nr",
        r"artikelnummer",
        r"product\s*ref(erentie)?",
        r"vendor\s*code",
        r"model\s*code",
    ],
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
            cn = str(c).strip().lower()
            if any(re.search(p, cn, re.I) for p in pats):
                m[k]=c; break
    return m

@st.cache_data(show_spinner=False)
def read_excel_all(file):
    x = pd.read_excel(file, sheet_name=None, dtype=str)
    out={}
    for s, df in x.items():
        df=df.copy(); df.columns=[str(c).strip() for c in df.columns]
        out[s]=df
    return out

def build_base(df_raw, sel):
    ref_col = sel.get("ref")
    ref_series = df_raw[ref_col].astype(str) if ref_col else ""

    # Prognose naar boven afronden
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

# =============================
# SQLite opslag (blijvend)
# =============================
DB_PATH = os.path.join(os.getcwd(), "app_data.db")
def db(): return sqlite3.connect(DB_PATH, check_same_thread=False)

def init_db():
    c = db(); cur = c.cursor()
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
    c.commit()
    cur.execute("PRAGMA table_info(suppliers)")
    cols = {row[1] for row in cur.fetchall()}
    if "Productietijd_dagen" not in cols:
        cur.execute("ALTER TABLE suppliers ADD COLUMN Productietijd_dagen INTEGER DEFAULT 0")
    if "Levertijd_zee_dagen" not in cols:
        cur.execute("ALTER TABLE suppliers ADD COLUMN Levertijd_zee_dagen INTEGER DEFAULT 0")
    if "Levertijd_lucht_dagen" not in cols:
        cur.execute("ALTER TABLE suppliers ADD COLUMN Levertijd_lucht_dagen INTEGER DEFAULT 0")
    c.commit(); c.close()

@st.cache_data(show_spinner=False)
def load_prices():
    init_db()
    c=db()
    df=pd.read_sql_query(
        "SELECT EAN, Referentie, Verkoopprijs, Inkoopprijs, Verzendkosten, "
        "Overige_kosten AS 'Overige kosten', Leverancier, MOQ, Levertijd_dagen AS 'Levertijd (dagen)' "
        "FROM prices", c)
    c.close()
    if df.empty:
        df=pd.DataFrame(columns=["EAN","Referentie","Verkoopprijs","Inkoopprijs","Verzendkosten",
                                 "Overige kosten","Leverancier","MOQ","Levertijd (dagen)"])
    for col in ["Verkoopprijs","Inkoopprijs","Verzendkosten","Overige kosten","MOQ","Levertijd (dagen)"]:
        df[col]=pd.to_numeric(df[col], errors="coerce").fillna(0)
    df["EAN"]=df["EAN"].astype(str).str.strip()
    df["Referentie"]=df.get("Referentie","").astype(str).str.strip()
    return df

def save_prices(df):
    init_db()
    need=["EAN","Referentie","Verkoopprijs","Inkoopprijs","Verzendkosten","Overige kosten","Leverancier","MOQ","Levertijd (dagen)"]
    for c in need:
        if c not in df.columns:
            df[c] = "" if c in ["Referentie","Leverancier"] else 0
    df=df[need].copy()
    df["EAN"]=df["EAN"].astype(str).str.strip()
    df["Referentie"]=df["Referentie"].astype(str).str.strip()
    for c in ["Verkoopprijs","Inkoopprijs","Verzendkosten","Overige kosten","MOQ","Levertijd (dagen)"]:
        df[c]=pd.to_numeric(df[c].astype(str).str.replace(",",".",regex=False), errors="coerce").fillna(0)
    c=db(); cur=c.cursor()
    cur.execute("DELETE FROM prices")
    cur.executemany("""
        INSERT OR REPLACE INTO prices
        (EAN, Referentie, Verkoopprijs, Inkoopprijs, Verzendkosten, Overige_kosten, Leverancier, MOQ, Levertijd_dagen)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        (r.EAN, str(r["Referentie"] or ""), float(r["Verkoopprijs"]), float(r["Inkoopprijs"]),
         float(r["Verzendkosten"]), float(r["Overige kosten"]), str(r["Leverancier"] or ""),
         int(r["MOQ"] or 1), int(r["Levertijd (dagen)"] or 0))
        for _, r in df.iterrows() if str(r["EAN"]).strip()!=""
    ])
    c.commit(); c.close()
    st.cache_data.clear()

@st.cache_data(show_spinner=False)
def load_suppliers():
    init_db()
    c=db()
    df=pd.read_sql_query(
        "SELECT Naam, Locatie, Productietijd_dagen AS 'Productietijd (dagen)', "
        "Levertijd_zee_dagen AS 'Levertijd zee (dagen)', "
        "Levertijd_lucht_dagen AS 'Levertijd lucht (dagen)' FROM suppliers", c)
    c.close()
    if df.empty:
        df=pd.DataFrame(columns=["Naam","Locatie","Productietijd (dagen)","Levertijd zee (dagen)","Levertijd lucht (dagen)"])
    for col in ["Productietijd (dagen)","Levertijd zee (dagen)","Levertijd lucht (dagen)"]:
        df[col]=pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    return df

def save_suppliers(df):
    init_db()
    need=["Naam","Locatie","Productietijd (dagen)","Levertijd zee (dagen)","Levertijd lucht (dagen)"]
    for c in need:
        if c not in df.columns: df[c] = "" if c != "Productietijd (dagen)" else 0
    df=df[need].copy()
    df["Naam"]=df["Naam"].astype(str).str.strip()
    df["Locatie"]=df["Locatie"].astype(str).str.strip()
    for col in ["Productietijd (dagen)","Levertijd zee (dagen)","Levertijd lucht (dagen)"]:
        df[col]=pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    c=db(); cur=c.cursor()
    cur.execute("DELETE FROM suppliers")
    cur.executemany(
        "INSERT OR REPLACE INTO suppliers (Naam, Locatie, Productietijd_dagen, Levertijd_zee_dagen, Levertijd_lucht_dagen) VALUES (?, ?, ?, ?, ?)",
        [(r.Naam, r.Locatie, int(r["Productietijd (dagen)"]), int(r["Levertijd zee (dagen)"]), int(r["Levertijd lucht (dagen)"]))
         for _, r in df.iterrows() if r.Naam]
    )
    c.commit(); c.close()
    st.cache_data.clear()

def delete_supplier(name: str):
    c=db(); cur=c.cursor()
    cur.execute("DELETE FROM suppliers WHERE Naam=?", (name,))
    c.commit(); c.close()
    st.cache_data.clear()

@st.cache_data(show_spinner=False)
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
    cur.execute("INSERT INTO incoming (EAN, Referentie, Aantal, ETA, Leverancier, Opmerking) VALUES (?, ?, ?, ?, ?, ?)",
                (str(ean).strip(), str(ref or ""), int(qty or 0), str(eta) if eta else "", str(leverancier or ""), str(note or "")))
    c.commit(); c.close()
    st.cache_data.clear()

def delete_incoming_row(row_id: int):
    c=db(); cur=c.cursor()
    cur.execute("DELETE FROM incoming WHERE id=?", (int(row_id),))
    c.commit(); c.close()
    st.cache_data.clear()

# =============================
# Basisdata upload
# =============================
if "base_df" not in st.session_state:
    st.session_state.base_df = None

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
                default = auto.get(key)
                idx = (opts.index(default) if default in opts else 0)
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
                if sel["ref"] == "— (geen) —":
                    sel["ref"] = None
                st.session_state.base_df = build_base(raw, sel)
                st.success("Basisdata opgeslagen.")
        except Exception as e:
            st.error(f"Kon Excel niet lezen: {e}")

# =============================
# Merge helper
# =============================
def merged_inventory():
    base = st.session_state.base_df
    if base is None: return None
    base = base.copy()
    prices = load_prices().copy()
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
        base = base.merge(
            prices[["EAN"] + cols],
            on="EAN",
            how="left",
            suffixes=("_u", "_p")
        )
        if "Referentie_u" in base.columns or "Referentie_p" in base.columns:
            base["Referentie"] = base.get("Referentie_u","").replace("", np.nan).fillna(base.get("Referentie_p",""))
            base.drop(columns=[c for c in ["Referentie_u","Referentie_p"] if c in base.columns],
                     inplace=True, errors="ignore")
    else:
        for c in cols:
            if c not in base.columns: base[c] = 0 if c not in ["Leverancier","Referentie"] else ""

    for c in ["Verkoopprijs","Inkoopprijs","Verzendkosten","Overige kosten","MOQ","Levertijd (dagen)"]:
        base[c] = pd.to_numeric(base[c].astype(str).str.replace(",",".",regex=False), errors="coerce").fillna(0)

    if "Leverancier" not in base.columns: base["Leverancier"]=""
    if "Referentie" not in base.columns: base["Referentie"]=""

    base["Voorraadwaarde (verkoop)"] = base["Vrije voorraad"] * base["Verkoopprijs"].fillna(0)
    base["Totale kostprijs per stuk"] = base["Inkoopprijs"].fillna(0) + base["Verzendkosten"].fillna(0) + base["Overige kosten"].fillna(0)
    return base

# =============================
# Benchmarks + recommend
# =============================
def classify(row, over_units: int):
    """Label op basis van absolute drempel (stuks)."""
    f = float(row.get("Verkoopprognose min (Totaal 4w)",0) or 0)
    stock_total = float(row.get("Vrije voorraad",0) or 0) + float(row.get("Incoming",0) or 0)
    if stock_total <= 0:
        return "Out of stock"
    if f <= 0:
        return "Healthy"
    if stock_total < f:
        return "At risk"
    if stock_total >= f + over_units:
        return "Overstock"
    return "Healthy"

def recommend(row):
    f = float(row.get("Verkoopprognose min (Totaal 4w)",0) or 0)
    stock_total = float(row.get("Vrije voorraad",0) or 0) + float(row.get("Incoming",0) or 0)
    if f <= 0: return 0
    target = 1.1*f
    need = max(0.0, target - stock_total)
    moq = to_int(row.get("MOQ",1),1)
    return int(np.ceil(need/max(1,moq))*max(1,moq))

# =============================
# Sidebar navigatie (emoji)
# =============================
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

# =============================
# Pages
# =============================
if choice == "Home":
    st.header("Home")
    if st.session_state.base_df is None:
        st.info("Nog geen basisdata geladen. Upload hieronder.")
        upload_base_ui()
    inv = merged_inventory()
    if inv is None: st.stop()

    # Absolute drempel in stuks
    over_units = st.number_input("Overstock-drempel (stuks)", min_value=0, value=30, step=1)

    inv["Status"] = inv.apply(lambda r: classify(r, over_units), axis=1)

    c1,c2,c3,c4 = st.columns(4)
    c1.metric("Totale voorraadwaarde (verkoop)", f"€ {inv['Voorraadwaarde (verkoop)'].sum():,.2f}")
    c2.metric("Artikelen", len(inv))
    c3.metric("Out of stock", int((inv["Status"]=="Out of stock").sum()))
    c4.metric("At risk", int((inv["Status"]=="At risk").sum()))

    # Staafdiagram met vaste kleuren
    st.markdown("**Voorraad gezondheid**")
    order = ["Out of stock","At risk","Healthy","Overstock"]
    counts = inv["Status"].value_counts().reindex(order).fillna(0)
    chart_df = pd.DataFrame({"Status":order,"Aantal":[int(counts.get(s,0)) for s in order]})
    y_max = max(1, int(chart_df["Aantal"].max()))
    color_scale = alt.Scale(domain=order, range=["#E74C3C", "#F39C12", "#27AE60", "#34495E"])
    chart = (
        alt.Chart(chart_df)
        .mark_bar()
        .encode(
            x=alt.X("Status:N", sort=order, title="Status"),
            y=alt.Y("Aantal:Q", scale=alt.Scale(domain=(0, y_max)), title="Aantal"),
            color=alt.Color("Status:N", scale=color_scale, legend=None),
        )
        .properties(height=280)
    )
    st.altair_chart(chart, use_container_width=True)

    # Klikbare chips voor details — nu met "All products"
    st.markdown("**Klik op een categorie voor details**")
    chip_order = ["All products"] + order
    chip_counts = {
        "All products": len(inv),
        "Out of stock": int(counts.get("Out of stock",0)),
        "At risk": int(counts.get("At risk",0)),
        "Healthy": int(counts.get("Healthy",0)),
        "Overstock": int(counts.get("Overstock",0)),
    }
    cols = st.columns(len(chip_order))
    selected = st.session_state.get("selected_status", None)
    for i, s in enumerate(chip_order):
        with cols[i]:
            cnt = chip_counts.get(s, 0)
            if st.button(f"{s} ({cnt})", key=f"chip_{s}", use_container_width=True):
                selected = s
                st.session_state["selected_status"] = s

    st.markdown("---")
    if selected:
        if selected == "All products":
            st.subheader("Details: All products")
            df_sel = inv.copy()
        else:
            st.subheader(f"Details: {selected}")
            df_sel = inv[inv["Status"]==selected].copy()

        if df_sel.empty:
            st.info("Geen producten in deze categorie.")
        else:
            df_sel["Aanbevolen bestelaantal"] = df_sel.apply(recommend, axis=1)
            st.dataframe(
                df_sel[["Status","EAN","Referentie","Titel","Vrije voorraad","Incoming",
                        "Verkoopprognose min (Totaal 4w)","Aanbevolen bestelaantal","Leverancier"]],
                use_container_width=True
            )
    else:
        st.subheader("Toplijst (aanbevolen bestelaantal)")
        inv["Aanbevolen bestelaantal"] = inv.apply(recommend, axis=1)
        st.dataframe(
            inv[["Status","EAN","Referentie","Titel","Vrije voorraad","Incoming",
                 "Verkoopprognose min (Totaal 4w)","Aanbevolen bestelaantal","Leverancier"]]
            .sort_values(["Aanbevolen bestelaantal"], ascending=False),
            use_container_width=True
        )

elif choice == "Inventory":
    st.header("Inventory")
    if st.session_state.base_df is None:
        st.info("Upload eerst je basisbestand.")
        upload_base_ui()
    inv = merged_inventory()
    if inv is not None:
        st.subheader("Prijzen & parameters (blijvend opslaan)")
        prices = load_prices()
        prices = st.data_editor(prices, key="prices_editor_table", num_rows="dynamic", use_container_width=True)
        col1,col2 = st.columns([1,1])
        with col1:
            if st.button("💾 Opslaan prijzen", type="primary"):
                save_prices(prices); st.success("Prijzen opgeslagen.")
        with col2:
            if st.button("🔄 Herladen prijzen"):
                st.cache_data.clear(); st.experimental_rerun()

        st.markdown("---")
        st.subheader("Overzicht producten")
        show_cols = ["EAN","Referentie","Titel","Vrije voorraad","Verkoopprognose min (Totaal 4w)",
                     "Verkoopprijs","Inkoopprijs","Verzendkosten","Overige kosten","Leverancier","MOQ","Levertijd (dagen)"]
        for c in show_cols:
            if c not in inv.columns: inv[c]=""
        st.dataframe(inv[show_cols], use_container_width=True)

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
        submitted = st.form_submit_button("Toevoegen")
        if submitted:
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
                save_suppliers(sup); st.success("Leverancier opgeslagen."); st.cache_data.clear()

    st.subheader("Leverancierslijst (bewerken/verwijderen)")
    sup_edit = st.data_editor(sup, num_rows="dynamic", use_container_width=True, key="sup_editor")
    c1, c2 = st.columns([1,1])
    with c1:
        if st.button("💾 Opslaan wijzigingen", type="primary"):
            save_suppliers(sup_edit); st.success("Leveranciers opgeslagen."); st.cache_data.clear()
    with c2:
        del_name = st.selectbox("🗑️ Verwijderen: kies leverancier", ["—"] + sup_edit["Naam"].astype(str).tolist())
        if st.button("Verwijder geselecteerde"):
            if del_name and del_name != "—":
                delete_supplier(del_name); st.success(f"'{del_name}' verwijderd."); st.cache_data.clear()

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
        ok = st.form_submit_button("Toevoegen")
        if ok:
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
                delete_incoming_row(int(del_id)); st.success("Verwijderd."); st.cache_data.clear()
            else:
                st.warning("Onbekend ID.")
