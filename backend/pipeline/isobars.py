"""
Garis ISOBAR (kontur tekanan muka laut) + pusat tekanan TINGGI (H) / RENDAH (L)
dari medan PRMSL GFS. Dipakai frontend HANYA saat layer Tekanan aktif (auto,
tanpa tombol) untuk melengkapi heatmap jadi peta tekanan gaya klasik.

Nol download tambahan: memakai array tekanan yang sudah ada di pipeline. Kontur
dihitung dgn contourpy (marching squares). Keluaran ringan (polyline + titik H/L).

Array masukan berorientasi baris-0 = UTARA (sama seperti process.py). Satuan hPa.
"""
from __future__ import annotations

import numpy as np
from contourpy import contour_generator

ISO_INTERVAL = 4       # jarak antar-isobar (hPa)
STRIDE_DEG = 0.75      # kasarkan grid (isobar berskala besar, tak perlu 0.25°)
SMOOTH_R = 2           # radius rata-rata kotak (sel grid kasar) utk garis mulus
DECIMATE_DEG = 0.8     # buang titik pd polyline yg lebih rapat dari ini (perkecil file)
MIN_LINE_DEG = 3.0     # buang garis kontur pendek (rentang < ini) — kurangi keramaian
PROM = 2.0             # prominence minimum pusat H/L (hPa) — buang tonjolan remeh
MIN_SEP = 6.0          # jarak minimum antar pusat H/L sejenis (derajat)
EDGE_MARGIN = 2.0      # abaikan pusat H/L dekat tepi domain (derajat)
MAX_HL = 12            # batas jumlah H dan L per waktu


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


def _decimate(line: np.ndarray) -> list:
    """Kurangi titik polyline: simpan titik bila >= DECIMATE_DEG dari titik terakhir
    (ujung selalu disimpan). Bulatkan ke 1 desimal."""
    if len(line) == 0:
        return []
    out = [line[0]]
    for p in line[1:-1]:
        if abs(p[0] - out[-1][0]) + abs(p[1] - out[-1][1]) >= DECIMATE_DEG:
            out.append(p)
    if len(line) > 1:
        out.append(line[-1])
    return [[round(float(p[0]), 1), round(float(p[1]), 2)] for p in out]


def _extrema(P, latc, lonc, dxc, want_high: bool) -> list:
    """Cari pusat tekanan tinggi (max) atau rendah (min) yang menonjol."""
    ny, nx = P.shape
    r = max(2, int(round(4.0 / dxc)))            # jendela ekstremum lokal ~4°
    Pp = np.pad(P, r, mode="edge")
    ext = np.full_like(P, -np.inf if want_high else np.inf)
    for di in range(2 * r + 1):
        for dj in range(2 * r + 1):
            win = Pp[di:di + ny, dj:dj + nx]
            ext = np.maximum(ext, win) if want_high else np.minimum(ext, win)
    mask = (P >= ext) if want_high else (P <= ext)

    cands = []
    ii, jj = np.where(mask)
    rr = max(3, int(round(4.0 / dxc)))
    for i, j in zip(ii.tolist(), jj.tolist()):
        lat, lon = float(latc[i]), float(lonc[j])
        if (lat > latc[0] - EDGE_MARGIN or lat < latc[-1] + EDGE_MARGIN
                or lon < lonc[0] + EDGE_MARGIN or lon > lonc[-1] - EDGE_MARGIN):
            continue
        ring = P[max(0, i - rr):i + rr + 1, max(0, j - rr):j + rr + 1]
        prom = (P[i, j] - float(np.nanmean(ring))) if want_high else (float(np.nanmean(ring)) - P[i, j])
        if prom < PROM:
            continue
        cands.append((prom, lat, lon, float(P[i, j])))
    cands.sort(reverse=True)                     # prominence terbesar dulu
    keep = []
    for prom, lat, lon, val in cands:
        if all(abs(lat - k[1]) + abs(lon - k[2]) > MIN_SEP for k in keep):
            keep.append((prom, lat, lon, val))
        if len(keep) >= MAX_HL:
            break
    tcode = 1 if want_high else 0                # 1=H (tinggi), 0=L (rendah)
    return [[tcode, int(round(val)), round(lat, 2), round(lon, 2)] for _, lat, lon, val in keep]


def _detect_frame(P: np.ndarray, grid: dict) -> dict:
    ny, nx = P.shape
    west, south, east, north = grid["west"], grid["south"], grid["east"], grid["north"]
    dx = (east - west) / (nx - 1)

    stride = max(1, int(round(STRIDE_DEG / dx)))
    Ps = _box_mean(P[::stride, ::stride].astype("float64"), SMOOTH_R)
    nyc, nxc = Ps.shape
    dxc = dx * stride
    latc = north - np.arange(nyc) * dxc          # baris 0 = utara (menurun)
    lonc = west + np.arange(nxc) * dxc

    # contourpy butuh y menaik → balik baris (utara ke bawah).
    z = Ps[::-1, :]
    y_asc = latc[::-1]
    gen = contour_generator(x=lonc, y=y_asc, z=z)

    lo = int(np.floor(np.nanmin(Ps) / ISO_INTERVAL) * ISO_INTERVAL)
    hi = int(np.ceil(np.nanmax(Ps) / ISO_INTERVAL) * ISO_INTERVAL)
    iso = []
    for lv in range(lo, hi + 1, ISO_INTERVAL):
        for line in gen.lines(float(lv)):
            line = np.asarray(line)
            span = (np.ptp(line[:, 0]) + np.ptp(line[:, 1])) if len(line) else 0.0
            if span < MIN_LINE_DEG:            # buang garis kontur pendek
                continue
            pts = _decimate(line)
            if len(pts) >= 2:
                iso.append([lv, pts])

    hl = _extrema(Ps, latc, lonc, dxc, True) + _extrema(Ps, latc, lonc, dxc, False)
    return {"iso": iso, "hl": hl}


def build_isobars(series: dict, times: list, grid: dict) -> dict:
    """series['pressure'] = [array hPa per waktu] (baris-0=utara). Kembalikan
    {'times':[...], 'interval':N, 'frames':[ {'iso':[[lv,[[lon,lat]..]]..], 'hl':[[t,v,lat,lon]..]} ]}."""
    P = series.get("pressure")
    if not P:
        return {"times": [], "interval": ISO_INTERVAL, "frames": []}
    nt = min(len(times), len(P))
    frames = [_detect_frame(np.asarray(P[t], "float64"), grid) for t in range(nt)]
    return {"times": list(times[:nt]), "interval": ISO_INTERVAL, "frames": frames}
