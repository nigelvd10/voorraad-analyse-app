import streamlit as st
import pandas as pd
import numpy as np
import io
import re
from datetime import date, timedelta

# =============================
# Pagina-setup & CSS (simple theming)
# =============================
st.set_page_config(page_title="Voorraad Dashboard", layout="wide")

st.markdown(
    """
    <style>
    .chip {padding:6px 10px;border-radius:999px;border:1px solid #e6e6e6;margin-right:6px;background:#fff;font-size:12px}
    .chip.red{background:#ffe6e6;border-color:#ffb3b3}
    .chip.amber{background:#fff3e0;border-color:#ffd199}
    .chip.orange{background:#ffe8d6;border-color:#ffc38a}
    .chip.green{background:#e6ffe9;border-color:#b3ffbf}
    .chip.gray{background:#f2f2f2;border-color:#e0e0e0}
    .card{background:#fff;border:1px solid #eee;border-radius:16px;padding:16px;margin-bottom:12px;box-shadow:0 1px 2px rgba(0,0,0,.03)}
    .metric-big{font-size:28px;font-weight:700}
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("📦 Voorraad Dashboard")
st.caption("Upload je basisbestand en beheer benchmarks, voorraadwaarde, inkomende voorraad en besteloverzichten.")

# =============================
# Helpers
# =============================
PATTERNS = {
    "ean": [r"^\s*ean\s*$", r"\bgtin\b", r"product\s*code", r"art(ikel)?\s*(nr|nummer)?"],
    "title": [r"^\s*titel\s*$", r"^\s*naam\s*$", r"product\s*naam", r"title"],
    "stock": [r"vrije\s*voorraad", r"\bvoorraad\b", r"available", r"stock"],
    "sales_total": [r"verkopen\s*\(\s*totaal\s*\)", r"verkopen.*totaal", r"totaal.*verkopen", r"sales\s*total"],
    "forecast_min_4w": [r"verkoopprognose.*4\s*w", r"forecast.*4", r"prognose.*4\s*w", r"verkoopprognose\s*min\s*\(\s*totaal\s*4\s*w\s*\)"],
}

TARGET_NAMES = {
    "ean": "EAN",
    "title": "Titel",
    "stock": "Vrije voorraad",
    "sales_total": "Verkopen (Totaal)",
    "forecast_min_4w": "Verkoopprognose min (Totaal 4w)",
}

REQUIRED_ORDER = [
    "EAN","Titel","Vrije voorraad","Verkopen (Totaal)","Verkoopprognose min (Totaal 4w)"
]

@st.cache_data(show_spinner=False)
def read_excel_all(file):
    xls = pd.read_excel(file, sheet_name=None, dtype=str)
    cleaned = {}
    for s, df in xls.items():
        df = df.copy()
        df.columns = [str(c).strip() for c in df.columns]
        cleaned[s] = df
    return cleaned


def auto_map_columns(df: pd.DataFrame):
    mapping = {}
    for key, patterns in PATTERNS.items():
        for c in df.columns:
            c_norm = str(c).strip().lower()
            if any(re.search(p, c_norm, flags=re.I) for p in patterns):
                mapping[key] = c
                break
    return mapping


def coerce_num(x):
    return pd.to_numeric(pd.Series(x).astype(str).str.replace(",", ".", regex=False), errors="coerce").fillna(0)


def to_int_safe(x, default=1):
    try:
        v = pd.to_numeric(str(x).replace(',', '.'), errors='coerce')
        return int(v) if pd.notna(v) else default
    except Exception:
        return default


def to_float_safe(x, default=0.0):
    try:
        v = pd.to_numeric(str(x).replace(',', '.'), errors='coerce')
        return float(v) if pd.notna(v) else default
    except Exception:
        return default


def build_base(df_raw, sel):
    df = pd.DataFrame({
        "EAN": df_raw[sel["ean"]].astype(str).str.strip(),
        "Titel": df_raw[sel["title"]].astype(str),
        "Vrije voorraad": coerce_num(df_raw[sel["stock"]]),
        "Verkopen (Totaal)": coerce_num(df_raw[sel["sales_total"]]),
        "Verkoopprognose min (Totaal 4w)": coerce_num(df_raw[sel["forecast_min_4w"]]),
    })
    return df[REQUIRED_ORDER]


def classify_status(row, target_days, safety_days, incoming_qty):
    daily_rate = (row["Verkoopprognose min (Totaal 4w)"] / 28.0) if row["Verkoopprognose min (Totaal 4w)"] > 0 else 0
    stock = float(row["Vrije voorraad"]) + float(incoming_qty)
    if stock <= 0:
        return "Out of stock"
    if daily_rate == 0:
        return "Healthy"  # geen verbruik bekend -> niet alarmeren
    cover_days = stock / daily_rate
    # drempels
    if cover_days < safety_days:
        return "Overdue"  # onder safety -> acuut
    elif cover_days < target_days:
        return "At risk"
    elif cover_days > target_days * 2.0:
        return "Overstock"
    else:
        return "Healthy"


def recommend_qty(row, target_days, safety_days, incoming_qty, moq=1):
    daily_rate = (row["Verkoopprognose min (Totaal 4w)"] / 28.0) if row["Verkoopprognose min (Totaal 4w)"] > 0 else 0
    if daily_rate == 0:
        return 0
    target_stock = daily_rate * (target_days + safety_days)
    current = float(row["Vrije voorraad"]) + float(incoming_qty)
    need = max(0.0, target_stock - current)
    # rond op MOQ
    need_rounded = int(np.ceil(need / max(1, moq)) * max(1, moq))
    return need_rounded

# =============================
# STATE-init
# =============================
if "base_df" not in st.session_state:
    st.session_state.base_df = None
if "prices_df" not in st.session_state:
    st.session_state.prices_df = pd.DataFrame(columns=["EAN","Verkoopprijs","Inkoopprijs","Verzendkosten","Overige kosten","Leverancier","MOQ","Levertijd (dagen)"])
if "incoming_df" not in st.session_state:
    st.session_state.incoming_df = pd.DataFrame(columns=["EAN","Aantal","ETA","Leverancier"])  # ETA = expected arrival date

# =============================
# Sidebar – instellingen
# =============================
with st.sidebar:
    st.header("⚙️ Instellingen")
    target_days = st.slider("Target days of cover", 7, 60, 28, help="Doel aantal dagen voorraad dat je wil afdekken")
    safety_days = st.slider("Safety buffer (dagen)", 0, 30, 7, help="Extra veiligheidsbuffer in dagen")
    st.markdown("—")
    st.subheader("Imports")
    base_file = st.file_uploader("Upload basisbestand (.xlsx)", type=["xlsx"], key="base")
    prices_file = st.file_uploader("(Optioneel) Prijslijst (.xlsx/.csv)", type=["xlsx","csv"], key="prices")
    incoming_file = st.file_uploader("(Optioneel) Inkomende voorraad (.xlsx/.csv)", type=["xlsx","csv"], key="incoming")

# =============================
# Tabbladen UI
# =============================
T1, T2, T3, T4 = st.tabs(["📥 Data & Mapping", "📊 Dashboard", "🧾 Besteloverzicht", "🚚 Inkomend"])

# ---------- T1: Data & Mapping ----------
with T1:
    st.subheader("1) Basisdata uploaden & kolommen koppelen")
    if base_file is None:
        st.info("Upload je Excel met minimaal: EAN, Titel, Vrije voorraad, Verkopen (Totaal), Verkoopprognose min (Totaal 4w)")
    else:
        try:
            sheets = read_excel_all(base_file)
        except Exception as e:
            st.error(f"❌ Kon Excel niet lezen: {e}")
            st.stop()
        sheet = st.selectbox("Kies sheet", list(sheets.keys()))
        raw = sheets[sheet]
        st.dataframe(raw.head(10), use_container_width=True)
        auto = auto_map_columns(raw)
        st.markdown("**Koppel kolommen**")
        def pick(lbl, key):
            opts = ["— kies —"] + list(raw.columns)
            default = auto.get(key)
            idx = opts.index(default) if default in opts else 0
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
    st.subheader("2) (Optioneel) Prijslijst toevoegen of bewerken")
    st.caption("Kolommen: EAN, Verkoopprijs, Inkoopprijs, Verzendkosten, Overige kosten, Leverancier, MOQ, Levertijd (dagen)")
    if prices_file is not None:
        try:
            if prices_file.name.lower().endswith(".csv"):
                st.session_state.prices_df = pd.read_csv(prices_file)
            else:
                st.session_state.prices_df = pd.read_excel(prices_file)
        except Exception as e:
            st.error(f"Kon prijslijst niet lezen: {e}")
    st.session_state.prices_df = st.data_editor(
        st.session_state.prices_df,
        use_container_width=True,
        num_rows="dynamic",
        key="prices_editor",
    )
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        st.session_state.prices_df.to_excel(w, index=False)
    st.download_button("💾 Download prijslijst", buf.getvalue(), "prijslijst.xlsx")

    st.markdown("---")
    st.subheader("3) (Optioneel) Inkomende voorraad importeren")
    st.caption("Kolommen: EAN, Aantal, ETA (YYYY-MM-DD), Leverancier")
    if incoming_file is not None:
        try:
            if incoming_file.name.lower().endswith(".csv"):
                st.session_state.incoming_df = pd.read_csv(incoming_file)
            else:
                st.session_state.incoming_df = pd.read_excel(incoming_file)
        except Exception as e:
            st.error(f"Kon inkomende voorraad niet lezen: {e}")
    st.session_state.incoming_df = st.data_editor(
        st.session_state.incoming_df,
        use_container_width=True,
        num_rows="dynamic",
        key="incoming_editor",
    )

# Utility om alles te mergen

def merged_frame():
    if st.session_state.base_df is None:
        return None
    base = st.session_state.base_df.copy()
    prices = st.session_state.prices_df.copy()
    incoming = st.session_state.incoming_df.copy()
    # normaliseer types
    for df in [prices, incoming]:
        if df is None or df.empty:
            continue
        if "EAN" in df:
            df["EAN"] = df["EAN"].astype(str).str.strip()
    base["EAN"] = base["EAN"].astype(str).str.strip()
    # inkomende aantallen per EAN (toekomstige)
    if not incoming.empty and "ETA" in incoming.columns:
        try:
            incoming["ETA"] = pd.to_datetime(incoming["ETA"]).dt.date
        except Exception:
            pass
        incoming_future = incoming[incoming["ETA"].isna() | (incoming["ETA"] >= date.today())]
        inc_sum = incoming_future.groupby("EAN")["Aantal"].sum(min_count=1).fillna(0)
    else:
        inc_sum = pd.Series(dtype=float)
    base["Incoming"] = base["EAN"].map(inc_sum).fillna(0)

    # merge prijzen
    cols_prices = [c for c in ["Verkoopprijs","Inkoopprijs","Verzendkosten","Overige kosten","Leverancier","MOQ","Levertijd (dagen)"] if c in prices.columns]
    if cols_prices:
        base = base.merge(prices[["EAN"]+cols_prices], on="EAN", how="left")
    # defaulten
    for c in ["Verkoopprijs","Inkoopprijs","Verzendkosten","Overige kosten","MOQ","Levertijd (dagen)"]:
        if c not in base.columns:
            base[c] = 0
    if "Leverancier" not in base.columns:
        base["Leverancier"] = ""
    # type-coercion (robust tegen lege/tekst waardes)
    for c in ["Verkoopprijs","Inkoopprijs","Verzendkosten","Overige kosten","MOQ","Levertijd (dagen)"]:
        base[c] = pd.to_numeric(base[c].astype(str).str.replace(',', '.', regex=False), errors='coerce').fillna(0)
    # berekeningen
    base["Voorraadwaarde (verkoop)"] = base["Vrije voorraad"] * base["Verkoopprijs"].fillna(0)
    base["Totale kostprijs per stuk"] = base["Inkoopprijs"].fillna(0) + base["Verzendkosten"].fillna(0) + base["Overige kosten"].fillna(0)
    return base

# ---------- T2: Dashboard ----------
with T2:
    st.subheader("Overzicht & gezondheid")
    data = merged_frame()
    if data is None:
        st.info("Nog geen basisdata. Ga naar **📥 Data & Mapping**.")
    else:
        # status classificatie
        statuses = []
        for _, r in data.iterrows():
            moq = to_int_safe(r.get("MOQ", 1), 1)
            incoming_qty = to_float_safe(r.get("Incoming", 0), 0)
            statuses.append(classify_status(r, target_days, safety_days, incoming_qty))
        data["Status"] = statuses

        # KPI's
        c1,c2,c3,c4 = st.columns(4)
        c1.markdown("<div class='card'><div>Totale voorraadwaarde (verkoop)</div><div class='metric-big'>€ {:,.2f}</div></div>".format(data["Voorraadwaarde (verkoop)"].sum()), unsafe_allow_html=True)
        c2.markdown("<div class='card'><div>Artikelen</div><div class='metric-big'>{}</div></div>".format(len(data)), unsafe_allow_html=True)
        c3.markdown("<div class='card'><div>Out of stock</div><div class='metric-big'>{}</div></div>".format((data["Status"]=="Out of stock").sum()), unsafe_allow_html=True)
        c4.markdown("<div class='card'><div>Te bestellen (aanbevolen qty > 0)</div><div class='metric-big'>{}</div></div>".format(0), unsafe_allow_html=True)

        # chips
        st.write("Filter op status:")
        chosen = st.multiselect(" ", ["Out of stock","Overdue","At risk","Reorder","Overstock","Healthy"], default=[], label_visibility="collapsed")

        # bereken recommendaties
        recs = []
        for _, r in data.iterrows():
            moq = to_int_safe(r.get("MOQ", 1), 1)
            incoming_qty = to_float_safe(r.get("Incoming", 0), 0)
            qty = recommend_qty(r, target_days, safety_days, incoming_qty, moq)
            recs.append(qty)
        data["Aanbevolen bestelaantal"] = recs
        data.loc[data["Status"].isin(["Overdue","At risk"]) & (data["Aanbevolen bestelaantal"]>0), "Status"] = data.loc[data["Status"].isin(["Overdue","At risk"]) & (data["Aanbevolen bestelaantal"]>0), "Status"].replace({"At risk":"Reorder"})

        # update KPI bestelbaar
        to_order_count = (data["Aanbevolen bestelaantal"]>0).sum()
        st.session_state["_to_order_count"] = int(to_order_count)

        if chosen:
            view = data[data["Status"].isin(chosen)].copy()
        else:
            view = data.copy()

        # Gezondheidsdiagram
        st.markdown("**Voorraad gezondheid**")
        health_counts = data["Status"].value_counts().reindex(["Out of stock","Overdue","At risk","Reorder","Overstock","Healthy"]).fillna(0)
        st.bar_chart(health_counts)

        st.markdown("**Producten**")
        display_cols = [
            "Status","EAN","Titel","Vrije voorraad","Incoming","Verkoopprognose min (Totaal 4w)",
            "Aanbevolen bestelaantal","Leverancier","Verkoopprijs","Inkoopprijs","Voorraadwaarde (verkoop)"
        ]
        missing_cols = [c for c in display_cols if c not in view.columns]
        for c in missing_cols:
            view[c] = ""
        st.dataframe(view[display_cols].sort_values(["Status","Aanbevolen bestelaantal"], ascending=[True,False]), use_container_width=True)

# ---------- T3: Besteloverzicht (PO) ----------
with T3:
    st.subheader("Maak besteloverzicht / PO")
    data = merged_frame()
    if data is None:
        st.info("Nog geen basisdata. Ga naar **📥 Data & Mapping**.")
    else:
        data["Aanbevolen bestelaantal"] = data.apply(lambda r: recommend_qty(r, target_days, safety_days, to_float_safe(r.get("Incoming",0),0), to_int_safe(r.get("MOQ",1),1)), axis=1)
        df_order = data[data["Aanbevolen bestelaantal"]>0].copy()
        if df_order.empty:
            st.success("Er zijn momenteel geen aanbevelingen om te bestellen.")
        else:
            st.info(f"Er zijn {len(df_order)} regels met een aanbevolen bestelaantal.")
            df_order["Totaal kosten"] = df_order["Aanbevolen bestelaantal"] * df_order["Totale kostprijs per stuk"].fillna(0)
            st.dataframe(df_order[["Leverancier","EAN","Titel","Aanbevolen bestelaantal","Totale kostprijs per stuk","Totaal kosten"]], use_container_width=True)

            # Exporteer per leverancier
            suppliers = sorted(df_order["Leverancier"].fillna("").unique())
            sup = st.selectbox("Kies leverancier voor export", suppliers)
            df_sup = df_order[df_order["Leverancier"]==sup].copy() if sup else df_order.copy()
            if df_sup.empty:
                st.warning("Geen regels voor deze leverancier.")
            else:
                po_cols = ["EAN","Titel","Aanbevolen bestelaantal","Totale kostprijs per stuk","Totaal kosten"]
                excel_buf = io.BytesIO()
                with pd.ExcelWriter(excel_buf, engine="openpyxl") as w:
                    df_sup[po_cols].to_excel(w, index=False, sheet_name="Bestelling")
                st.download_button(
                    "📥 Download PO (Excel)",
                    data=excel_buf.getvalue(),
                    file_name=f"PO_{sup or 'ALLE'}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
                st.download_button(
                    "📄 Download PO (CSV)",
                    data=df_sup[po_cols].to_csv(index=False).encode("utf-8"),
                    file_name=f"PO_{sup or 'ALLE'}.csv",
                    mime="text/csv",
                )

# ---------- T4: Inkomende voorraad ----------
with T4:
    st.subheader("Inkomende zendingen")
    inc = st.session_state.incoming_df.copy()
    if inc.empty:
        st.info("Nog geen inkomende voorraad toegevoegd.")
    else:
        try:
            inc["ETA"] = pd.to_datetime(inc["ETA"]).dt.date
        except Exception:
            pass
        st.dataframe(inc, use_container_width=True)
        # Samenvatting komende 30 dagen
        st.markdown("**Samenvatting komende 30 dagen**")
        try:
            inc_dt = inc.copy()
            inc_dt["ETA"] = pd.to_datetime(inc_dt["ETA"])
            horizon = date.today() + timedelta(days=30)
            soon = inc_dt[inc_dt["ETA"].between(pd.to_datetime(date.today()), pd.to_datetime(horizon))]
            sum_soon = soon.groupby("EAN")["Aantal"].sum().sort_values(ascending=False)
            st.bar_chart(sum_soon)
        except Exception:
            st.write("Kan diagram niet tonen (controleer datumformaat).")
