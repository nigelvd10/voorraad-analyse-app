
# app.py
import streamlit as st
import pandas as pd
import io
import re

# ---------- Pagina ----------
st.set_page_config(page_title="Voorraad Rapport Tool", layout="wide")
st.title("📦 Voorraad Rapport Tool")
st.caption("Upload één Excelbestand en genereer een rapport met EAN, Titel, Vrije voorraad, Verkopen (Totaal), Verkoopprognose min (Totaal 4w).")

# ---------- Tabs ----------
tab_upload, tab_result, tab_help = st.tabs(["📥 Upload & Mapping", "📊 Resultaat", "ℹ️ Uitleg"])

# ---------- Regex-heuristiek voor automatische kolomherkenning ----------
PATTERNS = {
    "ean": [
        r"^\s*ean\s*$", r"\bgtin\b", r"\bupc\b", r"art(ikel)?\s*(nr|nummer)?\b", r"product\s*code"
    ],
    "title": [
        r"^\s*titel\s*$", r"^product\s*naam$", r"^\s*naam\s*$", r"\btitle\b"
    ],
    "stock": [
        r"vrije\s*voorraad", r"\bvoorraad\b", r"beschikb(aar|.)\s*voorraad", r"\bstock\b", r"available"
    ],
    "sales_total": [
        r"verkopen\s*\(\s*totaal\s*\)", r"verkopen.*totaal", r"totaal.*verkopen", r"sales\s*total"
    ],
    "forecast_min_4w": [
        r"verkoopprognose.*4\s*w", r"prognose.*4\s*w", r"forecast.*4", r"verkoopprognose\s*min\s*\(\s*totaal\s*4\s*w\s*\)"
    ],
}

TARGET_NAMES = {
    "ean": "EAN",
    "title": "Titel",
    "stock": "Vrije voorraad",
    "sales_total": "Verkopen (Totaal)",
    "forecast_min_4w": "Verkoopprognose min (Totaal 4w)",
}

REQUIRED_ORDER = [
    "EAN",
    "Titel",
    "Vrije voorraad",
    "Verkopen (Totaal)",
    "Verkoopprognose min (Totaal 4w)",
]

# ---------- Helpers ----------
def _auto_map_columns(df: pd.DataFrame) -> dict:
    mapping = {}
    cols = [str(c).strip() for c in df.columns]
    for key, patterns in PATTERNS.items():
        found = None
        for c in cols:
            c_norm = c.lower()
            if any(re.search(p, c_norm, flags=re.IGNORECASE) for p in patterns):
                found = c
                break
        if found:
            mapping[key] = found
    return mapping

def _coerce_numeric(series: pd.Series) -> pd.Series:
    # zet komma's om naar punt en parseer numeriek
    s = series.astype(str).str.replace(",", ".", regex=False)
    return pd.to_numeric(s, errors="coerce").fillna(0)

@st.cache_data(show_spinner=False)
def _read_all_sheets(file) -> dict:
    xls = pd.read_excel(file, sheet_name=None, dtype=str)
    cleaned = {}
    for name, df in xls.items():
        df = df.copy()
        df.columns = [str(c).strip() for c in df.columns]
        cleaned[name] = df
    return cleaned

def _build_result(df_raw: pd.DataFrame, sel: dict) -> pd.DataFrame:
    df = pd.DataFrame({
        "EAN": df_raw[sel["ean"]].astype(str).str.strip(),
        "Titel": df_raw[sel["title"]].astype(str).fillna(""),
        "Vrije voorraad": _coerce_numeric(df_raw[sel["stock"]]),
        "Verkopen (Totaal)": _coerce_numeric(df_raw[sel["sales_total"]]),
        "Verkoopprognose min (Totaal 4w)": _coerce_numeric(df_raw[sel["forecast_min_4w"]]),
    })
    return df[REQUIRED_ORDER]

# ---------- Tab 1: Upload & Mapping ----------
with tab_upload:
    st.subheader("1) Upload je Excel")
    up = st.file_uploader("Kies een .xlsx bestand", type=["xlsx"], key="upload_xlsx")

    if up is not None:
        try:
            sheets = _read_all_sheets(up)
        except Exception as e:
            st.error(f"❌ Kon het Excelbestand niet lezen: {e}")
            st.stop()

        sheet_name = st.selectbox("2) Kies het tabblad (sheet) met de data", list(sheets.keys()))
        df_raw = sheets[sheet_name]

        st.markdown("**Voorbeeld uit het gekozen sheet (eerste 10 rijen):**")
        st.dataframe(df_raw.head(10), use_container_width=True)

        st.divider()
        st.subheader("3) Koppel kolommen")

        auto_map = _auto_map_columns(df_raw)

        def _select(label, options, suggested):
            opts = ["— kies —"] + options
            idx = opts.index(suggested) if suggested in options else 0
            return st.selectbox(label, opts, index=idx)

        colnames = list(df_raw.columns)
        sel_ean = _select("Kolom voor **EAN**", colnames, auto_map.get("ean", None))
        sel_title = _select("Kolom voor **Titel**", colnames, auto_map.get("title", None))
        sel_stock = _select("Kolom voor **Vrije voorraad**", colnames, auto_map.get("stock", None))
        sel_sales = _select("Kolom voor **Verkopen (Totaal)**", colnames, auto_map.get("sales_total", None))
        sel_fore = _select("Kolom voor **Verkoopprognose min (Totaal 4w)**", colnames, auto_map.get("forecast_min_4w", None))

        missing = [name for name, sel in [
            (TARGET_NAMES["ean"], sel_ean),
            (TARGET_NAMES["title"], sel_title),
            (TARGET_NAMES["stock"], sel_stock),
            (TARGET_NAMES["sales_total"], sel_sales),
            (TARGET_NAMES["forecast_min_4w"], sel_fore),
        ] if sel == "— kies —"]

        if missing:
            st.warning("Selecteer alle vereiste kolommen: " + ", ".join(missing))
        else:
            if st.button("✅ Genereer rapport", type="primary"):
                sel = {
                    "ean": sel_ean,
                    "title": sel_title,
                    "stock": sel_stock,
                    "sales_total": sel_sales,
                    "forecast_min_4w": sel_fore,
                }
                try:
                    result_df = _build_result(df_raw, sel)
                except Exception as e:
                    st.error(f"❌ Er ging iets mis bij het opbouwen van het rapport: {e}")
                else:
                    st.success("Rapport opgebouwd. Ga naar het tabblad **📊 Resultaat**.")
                    st.session_state["result_df"] = result_df

# ---------- Tab 2: Resultaat ----------
with tab_result:
    st.subheader("Gegenereerd rapport")
    if "result_df" not in st.session_state:
        st.info("Nog geen resultaat. Ga naar **📥 Upload & Mapping** om een rapport te genereren.")
    else:
        result = st.session_state["result_df"]
        st.dataframe(result, use_container_width=True)

        # Snelle metrics
        c1, c2, c3 = st.columns(3)
        c1.metric("Unieke EANs", result["EAN"].nunique())
        c2.metric("Totaal Vrije voorraad", float(result["Vrije voorraad"].sum()))
        c3.metric("Totaal Verkopen (Totaal)", float(result["Verkopen (Totaal)"].sum()))

        # Downloads
        excel_buf = io.BytesIO()
        with pd.ExcelWriter(excel_buf, engine="openpyxl") as writer:
            result.to_excel(writer, index=False, sheet_name="Voorraad_Rapport")

        st.download_button(
            "📥 Download als Excel",
            data=excel_buf.getvalue(),
            file_name="voorraad_rapport.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        csv_bytes = result.to_csv(index=False).encode("utf-8")
        st.download_button(
            "📄 Download als CSV",
            data=csv_bytes,
            file_name="voorraad_rapport.csv",
            mime="text/csv",
        )

# ---------- Tab 3: Uitleg ----------
with tab_help:
    st.markdown(
        """
**Hoe werkt het?**
1. Upload je Excel (.xlsx).
2. Kies het juiste tabblad (sheet).
3. Controleer of de kolommen goed zijn gekoppeld. De app probeert dit automatisch te herkennen.
4. Klik **Genereer rapport**.  
5. Bekijk en download het resultaat op het tabblad **📊 Resultaat**.

**Uitvoer-kolommen (vast formaat):**
- `EAN` (tekst)
- `Titel` (tekst)
- `Vrije voorraad` (getal)
- `Verkopen (Totaal)` (getal)
- `Verkoopprognose min (Totaal 4w)` (getal)

De app ondersteunt zowel komma's als punten in getallen en zet lege/ongeldige waardes om naar 0.
"""
    )
