Ik wil een nieuwe app maken waarmee ik de voorraad stand bij kan houden van mijn producten.
Ik heb een keer eerder een voorraad analyse bestand gemaakt via GitHub in combinatie met Streamlit. Dit ging aan de hand van een app.py bestand.
Ik wil dus een app waarin je een excel bestand kunt uploaden.
Aan de hand daarvan komt er een bestand uitrollen die aangeeft of er voldoende voorraad of onvoldoende voorraad aanwezig is. 

Dit is de app.py van de huidige app die ik gebruik om een LVB voorraad analyse te geven:
import streamlit as st
import pandas as pd
import io

st.set_page_config(page_title="LVB Advies Tool", layout="wide")

st.title("📦 LVB Tool met Hermeting Controle")

tab1, tab2, tab3 = st.tabs(["📦 LVB Advies", "📏 Hermeting Controle", "📊 GAP Analyse"])


with tab1:
    wachtwoord = st.text_input("Voer wachtwoord in om verder te gaan:", type="password")
    if wachtwoord != "bhg2k25":
        st.stop()

    st.markdown("### ⏱️ Kies periode voor advies")
    gebruik_14_dagen = st.checkbox("📆 Stuur op basis van 14 dagen (standaard is 28 dagen)")

    if gebruik_14_dagen:
        bol_14_file = st.file_uploader("📥 Upload Bol-export met 14-dagen verkopen (EAN in kolom A, verkopen in kolom I)", type=["xlsx"], key="bol14")
    else:
        bol_14_file = None

    buffer_percentage = st.slider("Instelbare buffer (% van verkopen):", min_value=10, max_value=100, value=30, step=5)

    bol_file = st.file_uploader("📤 Upload Bol-export (.xlsx)", type=["xlsx"])
    # ✅ Enige wijziging onderstaand: ook CSV toestaan
    fulfilment_file = st.file_uploader("🏬 Upload Fulfilment-export (.xlsx of .csv)", type=["xlsx", "csv"])

    if bol_file and fulfilment_file:
        df_bol = pd.read_excel(bol_file)

        # ✅ Nieuw: CSV óf XLSX kunnen inlezen voor fulfilment
        if fulfilment_file.name.lower().endswith(".csv"):
            try:
                # Probeer eerst met ';' (veel gebruikt door Channeldock)
                df_fulfilment = pd.read_csv(fulfilment_file, sep=";")
                # Als er maar 1 kolom is, was het waarschijnlijk een comma-CSV -> nogmaals inlezen
                if df_fulfilment.shape[1] == 1:
                    fulfilment_file.seek(0)
                    df_fulfilment = pd.read_csv(fulfilment_file)
            except Exception as e:
                st.error(f"❌ Kon CSV fulfilmentbestand niet lezen: {e}")
                st.stop()
        else:
            try:
                df_fulfilment = pd.read_excel(fulfilment_file)
            except Exception as e:
                st.error(f"❌ Kon Excel fulfilmentbestand niet lezen: {e}")
                st.stop()

        if gebruik_14_dagen and bol_14_file:
            try:
                df_14_raw = pd.read_excel(bol_14_file, sheet_name="Gisteren & 14 dagen", dtype=str)

                if df_14_raw.shape[1] <= 8:
                    st.error("❌ Het tabblad 'Gisteren & 14 dagen' bevat minder dan 9 kolommen. Zorg dat kolom A (EAN) en kolom I (verkopen over 14 dagen) aanwezig zijn.")
                    st.stop()

                df_14 = df_14_raw.iloc[:, [0, 8]].copy()
                df_14.columns = ["EAN", "Verkopen_14"]
                df_bol["EAN"] = df_bol["EAN"].astype(str)
                df_14["EAN"] = df_14["EAN"].astype(str)
                df_bol = pd.merge(df_bol, df_14, on="EAN", how="left")
                df_bol["Verkopen (Totaal)"] = df_bol["Verkopen_14"].fillna(0).astype(int)

            except Exception as e:
                st.error("❌ Kan het 14-dagen Excel-bestand niet correct verwerken uit het tabblad 'Gisteren & 14 dagen'. Details: " + str(e))
                st.stop()

        df_bol["EAN"] = df_bol["EAN"].astype(str)
        df_fulfilment["EAN"] = df_fulfilment["EAN"].astype(str)
        df_bol["Verkopen (Totaal)"] = pd.to_numeric(df_bol["Verkopen (Totaal)"], errors="coerce").fillna(0).astype(int)
        df_bol["Vrije voorraad"] = pd.to_numeric(df_bol["Vrije voorraad"], errors="coerce").fillna(0)
        df_bol["Verzendtype"] = df_bol.iloc[:, 4].astype(str)

        def match_fulfilment(ean, voorraad_df):
            for _, row in voorraad_df.iterrows():
                ean_list = str(row["EAN"]).split(",")
                if ean in [e.strip() for e in ean_list]:
                    return row["Vrije voorraad"], row["Verwachte voorraad"]
            return 0, 0

        resultaten = []
        for _, row in df_bol.iterrows():
            ean = row["EAN"]
            titel = row.get("Titel", "")
            bol_voorraad = row["Vrije voorraad"]
            verkopen = row["Verkopen (Totaal)"]
            verzendtype = row["Verzendtype"]

            fulfilment_vrij, fulfilment_verwacht = match_fulfilment(ean, df_fulfilment)
            if fulfilment_vrij <= 0 and fulfilment_verwacht <= 0:
                continue

            buffer_grens = verkopen * (buffer_percentage / 100)
            verschil = bol_voorraad - verkopen

            if verschil >= buffer_grens:
                benchmark = "Voldoende"
            elif 0 < verschil < buffer_grens:
                benchmark = "Twijfel"
            else:
                benchmark = "Onvoldoende"

            advies = ""
            aanbevolen = 0
            tekort = max(0, verkopen - bol_voorraad)

            if verzendtype.strip().upper() != "LVB" or benchmark != "Voldoende":
                if benchmark == "Twijfel":
                    if fulfilment_vrij > 0:
                        advies = "Voorraad krap – versturen aanbevolen"
                        aanbevolen = min(fulfilment_vrij, round(tekort * 1.3))
                    elif fulfilment_verwacht > 0:
                        advies = "Nog niet versturen – voorraad verwacht"
                        aanbevolen = 0
                    else:
                        continue
                elif benchmark == "Onvoldoende":
                    if fulfilment_vrij > 0:
                        advies = f"Verstuur minimaal {tekort} stuks"
                        aanbevolen = min(fulfilment_vrij, round(tekort * 1.3))
                    elif fulfilment_verwacht > 0:
                        advies = "Nog niet versturen – voorraad verwacht"
                        aanbevolen = 0
                    else:
                        continue
                elif benchmark == "Voldoende":
                    if verzendtype.strip().upper() != "LVB":
                        if fulfilment_vrij > 0:
                            advies = "Niet op LVB – voorraad beschikbaar – overweeg naar LVB te sturen"
                            aanbevolen = min(fulfilment_vrij, round(verkopen * 1.3))
                        elif fulfilment_verwacht > 0:
                            advies = "Nog niet versturen – voorraad verwacht (niet LVB)"
                            aanbevolen = 0
                        else:
                            continue
                    else:
                        continue

                resultaten.append({
                    "EAN": ean,
                    "Titel": titel,
                    "Benchmarkscore": benchmark,
                    "Verzendtype": verzendtype,
                    "Bol voorraad": bol_voorraad,
                    "Verkopen (Totaal)": verkopen,
                    "Fulfilment vrije voorraad": fulfilment_vrij,
                    "Fulfilment verwachte voorraad": fulfilment_verwacht,
                    "Advies": advies,
                    "Aanbevolen aantal mee te sturen (x1.3 buffer)": aanbevolen
                })

        df_resultaat = pd.DataFrame(resultaten)

        benchmark_order = {"Onvoldoende": 0, "Twijfel": 1, "Voldoende": 2}
        df_resultaat["Benchmarkscore_sort"] = df_resultaat["Benchmarkscore"].map(benchmark_order)
        df_resultaat.sort_values(by=["Benchmarkscore_sort", "Verzendtype"], inplace=True)
        df_resultaat.drop(columns=["Benchmarkscore_sort"], inplace=True)

        def kleur_op_benchmark(row):
            if row["Benchmarkscore"] == "Onvoldoende":
                return ["background-color: #ff3333; color: white"] * len(row)
            elif row["Benchmarkscore"] == "Twijfel":
                return ["background-color: #ffaa00; color: black"] * len(row)
            elif row["Benchmarkscore"] == "Voldoende":
                return ["background-color: #33cc33; color: white"] * len(row)
            else:
                return [""] * len(row)

        st.success("✅ Adviesoverzicht gegenereerd!")
        st.dataframe(df_resultaat.style.apply(kleur_op_benchmark, axis=1), use_container_width=True)

        buffer = io.BytesIO()
        df_resultaat.to_excel(buffer, index=False, engine='openpyxl')
        st.download_button("📥 Download als Excel", data=buffer.getvalue(), file_name="LVB_Advies_Overzicht.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        csv = df_resultaat.to_csv(index=False).encode('utf-8')
        st.download_button("📄 Download als CSV", data=csv, file_name="LVB_Advies_Overzicht.csv", mime="text/csv")


with tab2:
    st.subheader("📏 Hermeting Controle")

    col1, col2 = st.columns(2)
    with col1:
        hermeting_bestand = st.file_uploader("Upload je hermeting sheet (EAN, Naam, Gewenst formaat)", type=["xlsx"], key="hermeting")
    with col2:
        bol_verzendingen = st.file_uploader("Upload Bol verzendexport (EAN, Verzonden formaat)", type=["xlsx"], key="verzending")

    if hermeting_bestand and bol_verzendingen:
        df_hermeting_raw = pd.read_excel(hermeting_bestand, dtype=str)
        df_hermeting = df_hermeting_raw.iloc[:, [0, 1, 2]].copy()
        df_hermeting.columns = ['EAN', 'Productnaam', 'Gewenst formaat']
        df_hermeting['EAN'] = df_hermeting['EAN'].astype(str)

        df_verzonden_raw = pd.read_excel(bol_verzendingen, header=None, dtype=str)
        df_verzonden = pd.DataFrame()
        df_verzonden['EAN'] = df_verzonden_raw.iloc[:, 2]
        df_verzonden['Verzonden formaat'] = df_verzonden_raw.iloc[:, 7]
        df_verzonden.dropna(subset=['EAN'], inplace=True)
        df_verzonden.dropna(subset=['Verzonden formaat'], inplace=True)
        df_verzonden['EAN'] = df_verzonden['EAN'].astype(str)

        df_vergelijk = pd.merge(df_verzonden, df_hermeting, on='EAN', how='left')

        # Verwijder rijen waarbij beide formaten leeg zijn
        df_vergelijk = df_vergelijk.dropna(subset=['Gewenst formaat', 'Verzonden formaat'])

        # Zoek nu echte afwijkingen
        df_afwijkend = df_vergelijk[
            df_vergelijk['Verzonden formaat'].str.lower() != df_vergelijk['Gewenst formaat'].str.lower()
        ]

        df_afwijkend = df_afwijkend.drop_duplicates(subset=['EAN'])

        if not df_afwijkend.empty:
            df_afwijkend['Afwijking'] = "✅ Ja"
            st.success(f"🔎 {len(df_afwijkend)} afwijkende formaten gevonden")
            st.dataframe(df_afwijkend[['EAN', 'Productnaam', 'Gewenst formaat', 'Verzonden formaat', 'Afwijking']], use_container_width=True)

            buffer = io.BytesIO()
            df_afwijkend.to_excel(buffer, index=False, engine='openpyxl')
            st.download_button("📥 Download afwijkingen als Excel", data=buffer.getvalue(), file_name="hermeting_afwijkingen.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

            csv = df_afwijkend.to_csv(index=False).encode('utf-8')
            st.download_button("📄 Download als CSV", data=csv, file_name="hermeting_afwijkingen.csv", mime="text/csv")
        else:
            st.info("✅ Geen afwijkingen gevonden. Alles komt overeen met je verwachte formaten.")

with tab3:
    st.header("📊 GAP Analyse Tool")

    st.markdown("Voer hieronder de links in van je eigen Bol.com listing en drie concurrenten om een vergelijking te maken.")

    eigen_link = st.text_input("🔗 Link naar jouw product")
    concurrent_links = []
    for i in range(1, 4):
        link = st.text_input(f"🔗 Link naar concurrent {i}")
        concurrent_links.append(link)

    if st.button("📈 Vergelijk Listings"):
        if not eigen_link of any(not link for link in concurrent_links):
            st.warning("Vul alle links in voordat je vergelijkt.")
        else:
            st.success("Links succesvol ontvangen! (De vergelijking volgt in de volgende versie.)")
            st.write("Jouw productlink:", eigen_link)
            for idx, link in enumerate(concurrent_links, start=1):
                st.write(f"Concurrent {idx}:", link)

Kun je deze app.py wijzigen zodat het werkzaam is voor mijn nieuwe app?

Het is belangrijk dat de data die gelezen word nieuwe titels krijgt.
Dus ik wil dat de tabel bestaat uit EAN, Titel, Vrije voorraad, Verkopen (Totaal), Verkoopprognose min (Totaal 4w).

Al deze gegevens kun je aflezen in de meegeleverde excel bestand in de bijlage van dit bericht. Het is dus de bedoeling dat ik dit bestand upload en er vervolgens een bestand uit komt rollen met deze gegevens.
