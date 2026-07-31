"""
Konfigurasi pipeline peta cuaca (GFS).

Semua parameter global (region, variabel, path output) terpusat di sini agar
mudah diperluas ke multi-model / multi-level / multi-variabel di fase berikutnya.
"""
from pathlib import Path

# ---------------------------------------------------------------------------
# Domain DATA: Samudra Hindia (India) hingga Pasifik Barat, Cina Selatan hingga
# tengah Australia. Sengaja dibuat LEBIH LUAS dari area tampilan (viewing lock
# di frontend), supaya saat pan/zoom tepi domain data tidak pernah terlihat.
# Rasio domain (~118°bujur : ~66°lintang, proyeksi Mercator ~1.75) dekat dengan
# rasio layar desktop 16:9, jadi saat difit ke layar nyaris mengisi penuh.
# Cakupan inti (yang akan tampak): ~68-174E, -28..28N (India..Pasifik Barat,
# Cina Selatan..tengah Australia). Margin data ~5-6 derajat di tiap sisi.
# ---------------------------------------------------------------------------
REGION = {
    "left_lon": 62.0,     # 62E  (Laut Arab / India barat)
    "right_lon": 180.0,   # 180E (Pasifik Barat, batas antemeridian)
    "top_lat": 33.0,      # 33N  (Cina Selatan + margin)
    "bottom_lat": -33.0,  # 33S  (melewati tengah Australia)
}

# ---------------------------------------------------------------------------
# Model GFS (NOAA/NCEP), resolusi 0.25 derajat.
# Run tersedia 00/06/12/18 UTC, biasanya rilis ~3.5-5 jam setelah jam run.
# ---------------------------------------------------------------------------
GFS = {
    "resolution": "0p25",
    "run_hours": [0, 6, 12, 18],
    # jeda aman (jam) setelah jam-run sebelum data dianggap tersedia
    "availability_lag_hours": 5,
    # endpoint NOMADS "filter" untuk subset variabel + region (file kecil)
    "filter_url": "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl",
}

# ---------------------------------------------------------------------------
# Definisi layer. Fase 1: hanya angin permukaan (10 m).
# Tiap layer mendefinisikan variabel GRIB, level, tipe render, dan rentang skala.
# ---------------------------------------------------------------------------
LAYERS = {
    "wind_surface": {
        "kind": "vector",           # butuh 2 komponen (u, v)
        "u_var": "UGRD",
        "v_var": "VGRD",
        "grib_level": "10_m_above_ground",
        "level_label": "surface",
        # rentang encoding (m/s) untuk mengemas nilai ke 0..255 di PNG
        "unscale": [-40.0, 40.0],
    },
    "rain_surface": {
        "kind": "scalar",           # satu variabel -> heatmap PNG (tanpa partikel)
        "var": "PRATE",             # laju presipitasi permukaan (kg m-2 s-1)
        "grib_level": "surface",
        "level_label": "surface",
        "to_unit": 3600.0,          # kg m-2 s-1 -> mm/jam
        "units": "mm/jam",
        # PRATE punya 2 stepType (instant & avg) di f003+; ambil laju SESAAT.
        "filter_keys": {"stepType": "instant"},
    },
}

# ---------------------------------------------------------------------------
# Retensi data: tiap run disimpan frame dari (run_time - KEEP_PAST_HOURS) sampai
# ujung forecast (+72 jam). Frame lebih tua dari batas itu dihapus otomatis,
# sehingga window bergeser tiap hari (-24 jam belakang ... +72 jam depan).
# ---------------------------------------------------------------------------
KEEP_PAST_HOURS = 24

# ---------------------------------------------------------------------------
# Path output. Data ditulis sebagai file statis (PNG + JSON) yang nanti
# disajikan langsung ke frontend (tanpa database).
# ---------------------------------------------------------------------------
BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_DIR / "data"
RAW_DIR = DATA_DIR / "raw"        # file GRIB2 mentah (sementara)
OUTPUT_DIR = DATA_DIR / "output"  # PNG + JSON siap-frontend

for _d in (DATA_DIR, RAW_DIR, OUTPUT_DIR):
    _d.mkdir(parents=True, exist_ok=True)
