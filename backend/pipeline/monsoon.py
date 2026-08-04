"""
Status MONSUN + medan aliran angin musiman rata-rata, dari angin 10 m GFS.

Monsun = pembalikan angin musiman. Untuk kawasan Indonesia, penanda utamanya
adalah arah angin lapisan bawah:
  - Monsun BARAT (angin baratan, u>0): musim hujan (sekitar Nov-Mar).
  - Monsun TIMUR (angin timuran, u<0): musim kemarau (sekitar Mei-Sep).
Klasifikasi diambil dari rata-rata angin di kotak Indonesia sepanjang jendela
forecast (monsun stabil dalam 3 hari). Panah aliran = rata-rata angin per kotak
(medan kasar) yang ikut slider.

INDIKASI dari model global GFS. Nol download tambahan (pakai u,v yang sudah ada).
Array masukan berorientasi baris-0 = UTARA (sama seperti process.py). u/v m/s.
"""
from __future__ import annotations

import numpy as np

MS_TO_KT = 1.943844
U_THRESHOLD = 0.5    # ambang |u| minimum arah (m/s) SETELAH lolos uji keajekan
STEADY_MIN = 0.4     # keajekan arah minimum (0=acak, 1=tetap) agar dianggap monsun mantap
                     # (bukan pancaroba). Pancaroba SON/MAM cirinya arah angin tak konsisten.

# Kotak wilayah (lon0, lon1, lat0, lat1) untuk klasifikasi.
BOX_INDONESIA = (105.0, 140.0, -9.0, 2.0)
BOX_ASIA = (85.0, 112.0, 10.0, 20.0)         # Teluk Benggala / Indochina
BOX_AUSTRALIA = (120.0, 140.0, -18.0, -10.0)  # Australia utara
# Seruakan dingin: angin UTARAAN (v<0) rata-rata di bagian UTARA Laut Cina Selatan.
# Kriteria onset Lau & Chan (1983) / WMO-NEX: angin utara >= 5 m/s; kuat (Lau 1982) ~7.7 m/s.
BOX_SCS = (108.0, 118.0, 10.0, 20.0)
SURGE_LEVELS = [("Kuat", -8.0), ("Sedang", -6.5), ("Lemah", -5.0)]

# Borneo Vortex (Chang dkk. 2005): sirkulasi siklonik tertutup di kotak berikut.
# Deteksi via vortisitas relatif siklonik (ζ = ∂v/∂x - ∂u/∂y > 0 di BBU). Kita pakai
# angin PERMUKAAN sebagai proksi 925 hPa (vorteks di atas laut, arah mirip).
BOX_BV = (107.5, 117.5, -2.5, 7.5)
BV_VORT_MIN = 4.0      # vortisitas relatif minimum (×1e-5 s^-1) agar dianggap pusaran
BV_VT_MIN = 1.5        # rata-rata angin tangensial siklonik minimum di cincin (m/s)
BV_FRAC_MIN = 0.6      # fraksi titik cincin yang memutar siklonik (uji sirkulasi tertutup)

# Arus monsun beranimasi: angin rata-rata (medan mulus) untuk leaflet-velocity.
VEL_STRIDE = 3         # kasarkan ke ~0.75 derajat (file kecil, animasi tetap mulus)
VEL_SMOOTH_R = 2       # radius rata-rata kotak


def _region_mean(A: np.ndarray, grid: dict, lon0, lon1, lat0, lat1):
    ny, nx = A.shape
    west, south, east, north = grid["west"], grid["south"], grid["east"], grid["north"]
    dx = (east - west) / (nx - 1)
    dy = (north - south) / (ny - 1)
    j0 = max(0, min(nx - 1, int(round((lon0 - west) / dx))))
    j1 = max(0, min(nx - 1, int(round((lon1 - west) / dx))))
    i0 = max(0, min(ny - 1, int(round((north - lat1) / dy))))   # lat1 (utara) -> baris kecil
    i1 = max(0, min(ny - 1, int(round((north - lat0) / dy))))
    sub = A[i0:i1 + 1, j0:j1 + 1]
    return float(np.nanmean(sub)) if sub.size else 0.0


_CARD = ["Utara", "Timur Laut", "Timur", "Tenggara", "Selatan", "Barat Daya", "Barat", "Barat Laut"]


def _from_cardinal(u: float, v: float) -> str:
    """Arah datang angin (8 penjuru) dari komponen u (timur+), v (utara+)."""
    to_deg = np.degrees(np.arctan2(u, v))               # arah TUJU (0=utara, CW)
    frm = (to_deg + 180.0) % 360.0                       # arah DATANG
    return _CARD[int((frm + 22.5) // 45) % 8]


def _classify(ubar: float, steadiness: float) -> dict:
    """Monsun DOMINAN dari arah angin rata-rata di Indonesia (u timur+), DIGERBANG
    keajekan arah. Keajekan rendah = arah tak konsisten = PANCAROBA (walau ada
    sedikit dominasi). Berbalik otomatis saat data berubah musim (JJA<->DJF)."""
    if steadiness < STEADY_MIN:   # arah angin tak konsisten → pancaroba (SON/MAM)
        return {"code": "TRANS", "label": "Masa Peralihan (Pancaroba)", "season": "Transisi",
                "note": "Arah angin belum konsisten, cuaca bisa berubah cepat."}
    if ubar <= -U_THRESHOLD:      # angin timuran konsisten = datang dari tenggara = arah Australia
        return {"code": "AUS", "label": "Monsun Australia", "season": "Musim Kemarau",
                "note": "Angin timuran dari arah Australia sedang mendominasi dan konsisten. Indonesia umumnya lebih kering."}
    if ubar >= U_THRESHOLD:       # angin baratan konsisten = datang dari barat laut = arah Asia
        return {"code": "ASIA", "label": "Monsun Asia", "season": "Musim Hujan",
                "note": "Angin baratan dari arah Asia sedang mendominasi dan konsisten. Indonesia umumnya lebih basah."}
    return {"code": "TRANS", "label": "Masa Peralihan (Pancaroba)", "season": "Transisi",
            "note": "Angin konsisten tapi tanpa arah timur/barat yang jelas, cenderung peralihan."}


def build_monsoon(series: dict, times: list, grid: dict) -> dict:
    """series = {'u':[arr..], 'v':[arr..]} sejajar `times` (m/s, baris-0=utara).
    Kembalikan {phase, asia, australia, times, arrows:[per-waktu [[lon,lat,u,v]..]]}."""
    U, V = series.get("u"), series.get("v")
    if not U or not V:
        return {"phase": None, "times": [], "arrows": []}
    nt = min(len(times), len(U), len(V))
    Us = [np.asarray(U[t], "float32") for t in range(nt)]
    Vs = [np.asarray(V[t], "float32") for t in range(nt)]

    # rata-rata sepanjang jendela untuk klasifikasi (monsun stabil dlm 3 hari)
    Umean = np.mean(Us, axis=0)
    Vmean = np.mean(Vs, axis=0)
    # keajekan = |vektor angin rata-rata| / rata-rata kelajuan (0=arah acak, 1=arah tetap)
    spdmean = np.mean([np.sqrt(Us[t] ** 2 + Vs[t] ** 2) for t in range(nt)], axis=0)
    ui = _region_mean(Umean, grid, *BOX_INDONESIA)
    vi = _region_mean(Vmean, grid, *BOX_INDONESIA)
    spd = _region_mean(spdmean, grid, *BOX_INDONESIA)
    vecmag = (ui * ui + vi * vi) ** 0.5
    steadiness = vecmag / max(spd, 0.1)
    phase = _classify(ui, steadiness)
    phase["wind_from"] = _from_cardinal(ui, vi)
    phase["speed_kt"] = int(round(vecmag * MS_TO_KT))
    phase["steadiness"] = round(float(steadiness), 2)
    return {"phase": phase, "surge": _cold_surge(Vmean, grid),
            "vortex": _borneo_vortex(Umean, Vmean, grid)}


def _borneo_vortex(Umean: np.ndarray, Vmean: np.ndarray, grid: dict) -> dict:
    """Borneo Vortex: pusat vortisitas relatif SIKLONIK (ζ>0) di kotak Kalimantan
    barat (Chang dkk. 2005). ζ = ∂v/∂x - ∂u/∂y. Aktif bila ζ puncak >= ambang &
    ada angin >= 2 m/s (sirkulasi). Fenomena DJF; picu hujan lebat Indonesia barat."""
    ny, nx = Umean.shape
    west, south, east, north = grid["west"], grid["south"], grid["east"], grid["north"]
    dx = (east - west) / (nx - 1)
    dy = (north - south) / (ny - 1)
    lat = north - np.arange(ny) * dy
    lon = west + np.arange(nx) * dx
    Us, Vs = _box_mean(Umean, 3), _box_mean(Vmean, 3)
    coslat = np.cos(np.radians(lat)).clip(0.2, 1.0)
    dVdx = np.gradient(Vs, lon, axis=1) / (111320.0 * coslat[:, None])
    dUdy = np.gradient(Us, lat, axis=0) / 111320.0
    zeta = (dVdx - dUdy) * 1e5                        # ×1e-5 s^-1 (siklonik BBU = positif)

    lon0, lon1, lat0, lat1 = BOX_BV
    j0, j1 = int(round((lon0 - west) / dx)), int(round((lon1 - west) / dx))
    i0, i1 = int(round((north - lat1) / dy)), int(round((north - lat0) / dy))
    sub = zeta[i0:i1 + 1, j0:j1 + 1]
    if sub.size == 0:
        return {"active": False, "vort": 0.0}
    k = np.unravel_index(int(np.nanargmax(sub)), sub.shape)
    vmax = float(sub[k])
    ci, cj = i0 + k[0], j0 + k[1]                     # indeks pusat di grid penuh
    # UJI SIRKULASI TERTUTUP (Chang: bukan sekadar vortisitas, tapi pusaran nyata):
    # ambil angin di cincin ~2° sekeliling pusat, ukur komponen tangensial siklonik.
    rc = max(2, int(round(2.0 / dx)))
    vts = []
    for a in np.linspace(0.0, 2 * np.pi, 12, endpoint=False):
        ii, jj = ci - int(round(rc * np.sin(a))), cj + int(round(rc * np.cos(a)))
        if 0 <= ii < ny and 0 <= jj < nx:
            vts.append(-Us[ii, jj] * np.sin(a) + Vs[ii, jj] * np.cos(a))  # + = siklonik BBU
    vt_mean = float(np.mean(vts)) if len(vts) >= 8 else 0.0
    cyc_frac = float(np.mean(np.asarray(vts) > 0)) if len(vts) >= 8 else 0.0
    active = vmax >= BV_VORT_MIN and vt_mean >= BV_VT_MIN and cyc_frac >= BV_FRAC_MIN
    if active:
        return {"active": True, "lat": round(float(lat[i0 + k[0]]), 2), "lon": round(float(lon[j0 + k[1]]), 2),
                "vort": round(vmax, 1),
                "note": "Pusaran siklonik di Kalimantan barat, picu hujan lebat di Sumatra, Kalimantan & Semenanjung Malaysia."}
    return {"active": False, "vort": round(vmax, 1),
            "note": "Belum terbentuk pusaran siklonik di Kalimantan barat (fenomena musim hujan / DJF)."}


def _cold_surge(Vmean: np.ndarray, grid: dict) -> dict:
    """Seruakan Dingin: angin UTARAAN kuat (v<0) di Laut Cina Selatan → udara dingin
    menghunjam ke selatan, picu hujan lebat Sumatra/Jawa (fenomena DJF)."""
    v = _region_mean(Vmean, grid, *BOX_SCS)
    north_kt = round(float(-v) * MS_TO_KT, 1)        # kecepatan angin utaraan (kt), positif saat surge
    for level, thr in SURGE_LEVELS:                  # dari terkuat; onset >= 5 m/s (Lau & Chan)
        if v <= thr:
            return {"active": True, "level": level, "v": round(float(v), 1), "north_kt": north_kt,
                    "note": "Angin utara kuat di Laut Cina Selatan, udara dingin menghunjam ke selatan. Waspada hujan lebat di Sumatra & Jawa."}
    return {"active": False, "level": None, "v": round(float(v), 1), "north_kt": north_kt,
            "note": "Angin utara di Laut Cina Selatan belum cukup kuat (kriteria >= 5 m/s belum terpenuhi)."}


def _box_mean(A: np.ndarray, r: int) -> np.ndarray:
    if r <= 0:
        return A
    out = np.zeros_like(A)
    n = 0
    for di in range(-r, r + 1):
        for dj in range(-r, r + 1):
            out += np.roll(np.roll(A, di, axis=0), dj, axis=1)
            n += 1
    return out / n


def build_monsoon_velocity(series: dict, grid: dict, run, lat_min: float = -90.0,
                           lat_max: float = 90.0, lon_min: float = -360.0,
                           lon_max: float = 360.0, stride: int | None = None) -> list | None:
    """Medan angin RATA-RATA (mulus) dlm format leaflet-velocity utk animasi arus.
    Bisa dibatasi KOTAK [lon_min..lon_max, lat_min..lat_max] agar bisa dipakai buat
    arus monsun (seluruh domain) maupun swirl Borneo Vortex (kotak kecil, stride halus).
    Kembalikan payload (list 2 dict) atau None."""
    U, V = series.get("u"), series.get("v")
    if not U or not V:
        return None
    st = stride or VEL_STRIDE
    Um = _box_mean(np.mean([np.asarray(a, "float32") for a in U], axis=0), VEL_SMOOTH_R)
    Vm = _box_mean(np.mean([np.asarray(a, "float32") for a in V], axis=0), VEL_SMOOTH_R)
    Ud, Vd = Um[::st, ::st], Vm[::st, ::st]
    ny, nx = Ud.shape
    west, south, east, north = grid["west"], grid["south"], grid["east"], grid["north"]
    dx = (east - west) / (grid["width"] - 1) * st
    dy = (north - south) / (grid["height"] - 1) * st
    latc = north - np.arange(ny) * dy
    lonc = west + np.arange(nx) * dx
    rows = np.where((latc >= lat_min) & (latc <= lat_max))[0]
    cols = np.where((lonc >= lon_min) & (lonc <= lon_max))[0]
    if rows.size < 2 or cols.size < 2:
        return None
    r0, r1, c0, c1 = int(rows[0]), int(rows[-1]), int(cols[0]), int(cols[-1])
    Ud, Vd = Ud[r0:r1 + 1, c0:c1 + 1], Vd[r0:r1 + 1, c0:c1 + 1]
    ny2, nx2 = Ud.shape
    header = {
        "lo1": round(float(lonc[c0]), 4), "la1": round(float(latc[r0]), 4),
        "lo2": round(float(lonc[c1]), 4), "la2": round(float(latc[r1]), 4),
        "nx": nx2, "ny": ny2, "dx": round(dx, 4), "dy": round(dy, 4),
        "parameterCategory": 2, "parameterUnit": "m.s-1",
        "refTime": run.strftime("%Y-%m-%dT%H:00:00Z"), "forecastTime": 0,
    }
    uf = np.nan_to_num(Ud).astype("float32").ravel(order="C").round(1).tolist()
    vf = np.nan_to_num(Vd).astype("float32").ravel(order="C").round(1).tolist()
    return [
        {"header": {**header, "parameterNumber": 2, "parameterNumberName": "U-component_of_wind"}, "data": uf},
        {"header": {**header, "parameterNumber": 3, "parameterNumberName": "V-component_of_wind"}, "data": vf},
    ]
