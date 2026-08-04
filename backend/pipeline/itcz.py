"""
Deteksi ZONA KONVERGENSI ANTARTROPIS (ITCZ) dari medan angin 10 m GFS.

INDIKASI dari model global GFS (0.25°) — BUKAN analisis resmi. Frontend WAJIB
melabeli demikian.

Mekanisme (A + C, satu pita MENYAMBUNG dari barat ke timur):
  1. Konvergensi angin C = -(du/dx + dv/dy). ITCZ = sabuk C positif kuat.
  2. GARIS (sumbu): jalur optimal lewat DYNAMIC PROGRAMMING (Viterbi). Untuk
     tiap bujur dipilih lintang, dgn skor = konvergensi & penalti lompatan antar
     kolom → satu kurva MULUS menembus puncak konvergensi, melenggok utara-selatan
     mengikuti musim, tak terputus (persis gaya garis ITCZ di peta cuaca).
  3. PITA (zona): dari sumbu, lebar ke utara & selatan selama konvergensi tetap
     kuat → pita lebar-variabel (tebal di zona kuat, tipis di zona lemah).

Array masukan berorientasi baris-0 = UTARA, kolom-0 = BARAT (sama seperti
process.py). Satuan u/v m/s. Keluaran ringan (JSON polyline, ~puluhan KB).
"""
from __future__ import annotations

import numpy as np

# --- Parameter deteksi -----------------------------------------------------
BELT_LAT = 20.0        # sumbu dicari di sabuk |lat| <= ini (derajat)
SAMPLE_DEG = 1.0       # jarak sampling bujur untuk sumbu (grid dikasarkan)
SMOOTH_R = 2           # radius rata-rata kotak (sel grid kasar) utk hilangkan bising
LON_MARGIN = 1.0       # abaikan margin tepi domain (deg) agar tak ada artefak batas
SMOOTH_LAMBDA = 0.05   # penalti kehalusan jalur (per deg^2 lompatan lintang antar kolom)
BAND_FRAC = 0.5        # tepi pita = konvergensi turun di bawah frac x nilai sumbu
BAND_FLOOR = 0.2       # ambang mutlak (ternormalisasi) tepi pita di zona lemah
MAX_HALFWIDTH = 8.0    # setengah-lebar pita maksimum ke tiap sisi (deg)
MIN_HALFWIDTH = 1.2    # setengah-lebar minimum agar pita selalu terlihat (deg)


def _box_mean(A: np.ndarray, r: int) -> np.ndarray:
    """Rata-rata kotak (2r+1)^2 pada grid KASAR (murah). Pakai roll (wrap) — efek
    tepi kecil karena sabuk yang dipakai ada di bagian dalam."""
    if r <= 0:
        return A
    out = np.zeros_like(A)
    n = 0
    for di in range(-r, r + 1):
        for dj in range(-r, r + 1):
            out += np.roll(np.roll(A, di, axis=0), dj, axis=1)
            n += 1
    return out / n


def _detect_frame(U: np.ndarray, V: np.ndarray, grid: dict) -> list:
    """Kembalikan satu segmen menyambung = list [lon, lat, latN, latS]
    (sumbu + tepi utara/selatan pita) menyusuri seluruh bujur domain."""
    ny, nx = U.shape
    west, south, east, north = grid["west"], grid["south"], grid["east"], grid["north"]
    dx = (east - west) / (nx - 1)
    dy = (north - south) / (ny - 1)

    stride = max(1, int(round(SAMPLE_DEG / dx)))
    Us = _box_mean(U[::stride, ::stride].astype("float32"), SMOOTH_R)
    Vs = _box_mean(V[::stride, ::stride].astype("float32"), SMOOTH_R)
    nyc, nxc = Us.shape
    dxc, dyc = dx * stride, dy * stride
    latc = north - np.arange(nyc) * dyc          # baris 0 = utara (lintang tinggi)
    lonc = west + np.arange(nxc) * dxc
    cos = np.cos(np.radians(latc)).clip(0.2, 1.0)

    # Konvergensi C = -(du/dx + dv/dy). np.gradient dgn koordinat latc (menurun)
    # menangani tanda otomatis; faktor cos utk metrik arah-x.
    dUdlon = np.gradient(Us, lonc, axis=1) / cos[:, None]
    dVdlat = np.gradient(Vs, latc, axis=0)
    C = -(dUdlon + dVdlat)

    belt_rows = np.where(np.abs(latc) <= BELT_LAT)[0]
    if belt_rows.size < 3:
        return []
    L = latc[belt_rows]                          # lintang sabuk (menurun)
    R = len(L)

    Cn = np.clip(C[belt_rows, :], 0.0, None)     # konvergensi ternormalisasi
    peak = float(np.percentile(Cn[Cn > 0], 90)) if np.any(Cn > 0) else 0.0
    if peak <= 0:
        return []
    Cn = Cn / peak                               # ~[0, 1+]

    j0 = int(np.searchsorted(lonc, west + LON_MARGIN))
    j1 = int(np.searchsorted(lonc, east - LON_MARGIN))
    cols = list(range(j0, j1))
    if len(cols) < 5:
        return []

    # --- Viterbi: satu jalur kontinu memaksimalkan konvergensi, mulus ---
    Pmat = SMOOTH_LAMBDA * (L[:, None] - L[None, :]) ** 2   # penalti (curr, prev)
    best = Cn[:, cols[0]].copy()
    back = []
    for j in cols[1:]:
        M = best[None, :] - Pmat                 # (R_curr, R_prev)
        arg = M.argmax(axis=1)
        best = Cn[:, j] + M[np.arange(R), arg]
        back.append(arg)
    r = int(best.argmax())
    rows = [r]
    for arg in reversed(back):
        r = int(arg[r])
        rows.append(r)
    rows.reverse()                               # indeks baris sabuk per kolom

    # --- Bangun sumbu + tepi pita per kolom ---
    seg = []
    for jc, ci in zip(cols, rows):
        lat_x = float(L[ci])
        gci = belt_rows[ci]                       # indeks di grid penuh
        cax = float(Cn[ci, jc])
        edge = max(BAND_FRAC * cax, BAND_FLOOR)
        coln = np.clip(C[:, jc], 0.0, None) / peak
        rn = gci
        while rn - 1 >= 0 and coln[rn - 1] > edge:
            rn -= 1
        rs = gci
        while rs + 1 < nyc and coln[rs + 1] > edge:
            rs += 1
        latN = min(latc[rn], lat_x + MAX_HALFWIDTH)
        latS = max(latc[rs], lat_x - MAX_HALFWIDTH)
        latN = max(latN, lat_x + MIN_HALFWIDTH)
        latS = min(latS, lat_x - MIN_HALFWIDTH)
        seg.append([float(lonc[jc]), lat_x, latN, latS])

    # haluskan sumbu & tepi pita (2x rata-rata bergerak, edge-aware) + bulatkan
    arr = np.asarray(seg, dtype="float32")
    for col in (1, 2, 3):
        arr[:, col] = _smooth1d(_smooth1d(arr[:, col], 2), 1)
    out = [[round(float(a[0]), 1), round(float(a[1]), 2),
            round(float(a[2]), 2), round(float(a[3]), 2)] for a in arr]
    return [out]


def _smooth1d(x: np.ndarray, r: int) -> np.ndarray:
    """Rata-rata bergerak (2r+1) dgn padding TEPI (bukan nol) supaya ujung tak
    tertarik ke lintang 0."""
    if r <= 0 or len(x) < 2 * r + 1:
        return x
    k = np.ones(2 * r + 1) / (2 * r + 1)
    return np.convolve(np.pad(x, r, mode="edge"), k, mode="valid")


def detect_itcz(series: dict, times: list, grid: dict) -> dict:
    """series = {'u':[arr..], 'v':[arr..]} sejajar `times` (m/s, baris-0=utara).
    Kembalikan {'times':[...], 'frames':[ per-waktu: [segmen,...] ]}."""
    U, V = series.get("u"), series.get("v")
    if not U or not V:
        return {"times": [], "frames": []}
    nt = min(len(times), len(U), len(V))
    frames = []
    for t in range(nt):
        frames.append(_detect_frame(np.asarray(U[t], "float32"),
                                    np.asarray(V[t], "float32"), grid))
    return {"times": list(times[:nt]), "frames": frames}
