import streamlit as st
import pandas as pd
import folium
from streamlit_folium import st_folium
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import json
import io
import math
from datetime import datetime

# --- STILLINGAR ---
SHEET_NAME = "Tene_Vegur"
GPS_FOLDER_ID = "1lSopJYx4FL2iAsuJ7GsHTnPp9exCnFOe"
FASTIR_TIMAR = [9, 12, 16, 21]

st.set_page_config(page_title="Tene á ferð og flugi", page_icon="🚐", layout="wide")

# --- HÁLPARFALL FORRITS: REIKNA FJARLÆGÐ ---
def reikna_fjarlaegd(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

# --- TENGING VIÐ GOOGLE SERVICES ---
def fa_google_creds():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    try:
        if hasattr(st, "secrets") and "gcp_service_account" in st.secrets:
            creds_dict = dict(st.secrets["gcp_service_account"])
            return ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    except Exception:
        pass
    return ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)

# --- SJÁLFVIRK GPSLOGGER SINKUN ---
def athuga_og_uppfaera_gps():
    try:
        creds = fa_google_creds()
        client = gspread.authorize(creds)
        sheet = client.open(SHEET_NAME).sheet1
        drive_service = build('drive', 'v3', credentials=creds)

        dagur_nuna = datetime.now().strftime("%Y%m%d")
        skrar_nafn = f"{dagur_nuna}.geojson"
        
        query = f"'{GPS_FOLDER_ID}' in parents and name = '{skrar_nafn}' and trashed = false"
        results = drive_service.files().list(q=query, fields="files(id, name)").execute()
        files = results.get('files', [])

        if not files:
            return

        file_id = files[0]['id']
        request = drive_service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()

        fh.seek(0)
        gogn = json.loads(fh.read().decode('utf-8'))
        features = gogn.get("features", [])
        if not features:
            return

        sidasti_punktur = features[-1]
        coords = sidasti_punktur["geometry"]["coordinates"]
        ny_lon, ny_lat = coords[0], coords[1]

        gogn_sheet = sheet.get_all_records()
        skra_nyja_linu = False
        asteski_stadur = "Á ferðalagi"

        if len(gogn_sheet) > 0:
            sidasta_rod = gogn_sheet[-1]
            gamla_lat = float(sidasta_rod.get("Lat", 0) or 0)
            gamla_lon = float(sidasta_rod.get("Lon", 0) or 0)
            fjarlaegd = reikna_fjarlaegd(gamla_lat, gamla_lon, ny_lat, ny_lon)

            nu_tími = datetime.now()
            nu_klukkustund = nu_tími.hour
            nu_dags_str = nu_tími.strftime("%d.%m.%Y")

            sidasti_dags_str = sidasta_rod.get("Dagsetning", "")
            sidasti_klukkan_str = sidasta_rod.get("Klukkan", "")
            sidasta_klukkustund = int(sidasti_klukkan_str.split(":")[0]) if ":" in sidasti_klukkan_str else -1

            if fjarlaegd > 0.5:
                skra_nyja_linu = True
            else:
                for timi in FASTIR_TIMAR:
                    if nu_klukkustund >= timi and nu_klukkustund < (timi + 1):
                        if sidasti_dags_str == nu_dags_str and sidasta_klukkustund == timi:
                            break
                        else:
                            skra_nyja_linu = True
                            asteski_stadur = sidasta_rod.get("Staður", "Á ferðalagi")
                            break
        else:
            skra_nyja_linu = True

        if skra_nyja_linu:
            Dags_str = datetime.now().strftime("%d.%m.%Y")
            Klukkan_str = datetime.now().strftime("%H:%M")
            ny_rod = [Dags_str, Klukkan_str, asteski_stadur, "", "", "", "Sjálfvirkt GPS", ny_lat, ny_lon]
            sheet.append_row(ny_rod)
            st.toast("🎉 Nýjum staðsetningarpunkti bætt við í dagbókina!", icon="🚐")
    except Exception:
        pass

# Keyrum GPS-athugunina
athuga_og_uppfaera_gps()

# --- FORRITSVIÐMÓT (STREAMLIT) ---
st.title("🚐 Tene á ferðalaginu")
st.markdown("Rauntímakort og veðurdagbók yfir ferðalagið.")

@st.cache_data(ttl=60)
def saekja_gogn():
    creds = fa_google_creds()
    client = gspread.authorize(creds)
    sheet = client.open(SHEET_NAME).sheet1
    df_raw = pd.DataFrame(sheet.get_all_records())
    
    # Hreinsa 0 og tóma reiti úr töflunni
    df_raw = df_raw.replace([0, "0", 0.0], "")
    return df_raw

df = saekja_gogn()

if not df.empty and "Lat" in df.columns and "Lon" in df.columns:
    df["Lat_num"] = pd.to_numeric(df["Lat"], errors="coerce")
    df["Lon_num"] = pd.to_numeric(df["Lon"], errors="coerce")
    df_kort = df.dropna(subset=["Lat_num", "Lon_num"])

    if not df_kort.empty:
        sidasta_lat = df_kort.iloc[-1]["Lat_num"]
        sidasta_lon = df_kort.iloc[-1]["Lon_num"]
        sidasti_stadur = df_kort.iloc[-1].get("Staður", "Núverandi staðsetning")
        
        m = folium.Map(location=[sidasta_lat, sidasta_lon], zoom_start=8)
        
        # 1. TEIKNA LÍNU Á MILLI HNITA ÚR GOOGLE SHEETS
        hnit_lista = df_kort[["Lat_num", "Lon_num"]].values.tolist()
        folium.PolyLine(hnit_lista, color="blue", weight=4, opacity=0.8).add_to(m)
        
        # 2. RAUÐIR PRJÓNAR FYRIR ELDRI STAÐI
        for idx, row in df_kort.iloc[:-1].iterrows():
            mynd_html = ""
            if "Mynd" in row and row["Mynd"]:
                mynd_html = f"<br><img src='{row['Mynd']}' width='200px' style='border-radius:8px;'><br>"
                
            popup_text = f"""
            <div style='font-family: sans-serif; min-width: 150px;'>
                <b>📍 {row.get('Staður', '')}</b><br>
                📅 {row.get('Dagsetning', '')} kl. {row.get('Klukkan', '')}<br>
                🌡️ Hiti: {row.get('Hiti (°C)', '')}°C<br>
                🌤️ {row.get('Veðurlýsing', '')}
                {mynd_html}
            </div>
            """
            folium.Marker(
                [row["Lat_num"], row["Lon_num"]],
                popup=popup_text,
                tooltip=f"{row.get('Staður', '')} ({row.get('Klukkan', '')})",
                icon=folium.Icon(color="red", icon="info-sign")
            ).add_to(m)
            
        # 3. GRÆNI HÚSBÍLLINN FYRIR NÚVERANDI STAÐSETNINGU
        nuna_row = df_kort.iloc[-1]
        mynd_html = ""
        if "Mynd" in nuna_row and nuna_row["Mynd"]:
            mynd_html = f"<br><img src='{nuna_row['Mynd']}' width='200px' style='border-radius:8px;'><br>"
            
        nuna_popup = f"""
        <div style='font-family: sans-serif; min-width: 160px;'>
            <h4 style='margin:0; color:#2A9D8F;'>🚐 Hér erum við núna :-)</h4>
            <b>📍 {nuna_row.get('Staður', '')}</b><br>
            📅 {nuna_row.get('Dagsetning', '')} kl. {nuna_row.get('Klukkan', '')}<br>
            🌡️ Hiti: {nuna_row.get('Hiti (°C)', '')}°C<br>
            🌤️ {nuna_row.get('Veðurlýsing', '')}
            {mynd_html}
        </div>
        """
        folium.Marker(
            [sidasta_lat, sidasta_lon],
            popup=nuna_popup,
            tooltip=f"🚐 Hér erum við núna :-) ({sidasti_stadur})",
            icon=folium.Icon(color="green", icon="bus", prefix="fa")
        ).add_to(m)
            
        st_folium(m, width=1200, height=520)

st.subheader("📖 Dagbók og veðurskráningar")

# SNÚA TÖFLUNNI VIÐ SVO NÝJASTA SKRÁNINGIN SÉ EFST
if not df.empty:
    # Taka út hjálpardálkana fyrir sýningu
    df_visun = df.drop(columns=["Lat_num", "Lon_num"], errors="ignore")
    st.dataframe(df_visun.iloc[::-1].reset_index(drop=True), use_container_width=True)
else:
    st.dataframe(df, use_container_width=True)
