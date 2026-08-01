"""
Deteksi & pelacakan siklon tropis dari medan GFS (tekanan MSL + angin 10 m).

INDIKASI dari model global GFS (0.25°) — BUKAN peringatan resmi BMKG/TCWC.
Bisa ada false-alarm (low monsun) atau sistem lemah kelewat. Frontend WAJIB
melabeli demikian.

Alur:
  1. Tiap langkah waktu: cari MINIMUM LOKAL tekanan (pusat low).
  2. Saring pusat yang: di sabuk tropis, low tertutup (≥2 hPa lebih rendah dari
     sekitarnya), angin maks sekitar ≥ ambang (default 34 kt = badai tropis),
     dan sirkulasi SIKLONIK (vortisitas relatif bertanda benar per hemisfer).
  3. Rangkai pusat antar-waktu jadi TRACK (nearest-neighbor) → jalur prakiraan.

Array masukan berorientasi baris-0 = UTARA, kolom-0 = BARAT (sama seperti
process.py). Satuan: pressure hPa, u/v m/s.
"""
from __future__ import annotations

import numpy as np

MS_TO_KT = 1.943844
DEG_M = 111320.0  # meter per derajat lintang
MIN_VT_MS = 7.0   # rata-rata angin tangensial (putaran) minimum di cincin (m/s)

# (ambang_knot, kode, label awam). Diperiksa dari terkuat ke terlemah.
_CAT_BANDS = [
    (137, 5, "Topan Super (Kat 5)"),
    (113, 4, "Topan (Kat 4)"),
    (96,  3, "Topan (Kat 3)"),
    (83,  2, "Topan (Kat 2)"),
    (64,  1, "Topan (Kat 1)"),
    (48,  0, "Badai Tropis Kuat"),
    (34, -1, "Badai Tropis"),
]


def _category(wind_kt: float) -> tuple[int, str]:
    for mn, code, label in _CAT_BANDS:
        if wind_kt >= mn:
            return code, label
    return -2, "Depresi Tropis"


# Tingkatan (rank untuk peringkat/peak). tier → warna dipilih frontend.
_TIER_RANK = {"EXTRA": 3, "TC": 2, "SEED": 1, "CIRC": 0}


def _classify(wind_kt: float, lat: float, vt: float) -> tuple[str, int, str]:
    """Tentukan TINGKAT + kode + label dari kecepatan, lintang, & kekuatan putaran.
    - EXTRA (Siklon Lintang Tinggi): |lat|≥30° (dekat tepi domain, cenderung ekstratropis).
    - TC (Siklon Tropis): lintang 5–30°, angin ≥34 kt, putaran kuat (vt≥7).
    - SEED (Bibit): lintang ≥5°, ≥25 kt, berputar. CIRC (Sirkulasi): sisanya."""
    if abs(lat) >= 30 and wind_kt >= 25:
        return "EXTRA", -5, "Siklon Lintang Tinggi"
    if abs(lat) >= 5 and wind_kt >= 34 and vt >= 7.0:
        code, cat_label = _category(wind_kt)
        return "TC", code, f"Siklon Tropis · {cat_label}"
    if abs(lat) >= 5 and wind_kt >= 25:
        return "SEED", -3, "Bibit Siklon Tropis"
    return "CIRC", -4, "Sirkulasi Siklonik"


def _ring_tangential(U, V, i, j, dx, lat):
    """Uji sirkulasi: ambil angin di cincin ~2° sekeliling pusat, ukur komponen
    TANGENSIAL siklonik. Kembalikan (rata2 Vt m/s, fraksi titik yang memutar benar).
    Ini membedakan pusaran sejati dari angin kencang searah (mis. celah gunung)."""
    ny, nx = U.shape
    rc = max(2, int(round(2.0 / dx)))
    hemi = 1.0 if lat >= 0 else -1.0          # NH siklonik = CCW, SH = CW
    vts = []
    for a in np.linspace(0.0, 2 * np.pi, 12, endpoint=False):
        jj = j + int(round(rc * np.cos(a)))
        ii = i - int(round(rc * np.sin(a)))   # baris i naik ke SELATAN
        if ii < 0 or jj < 0 or ii >= ny or jj >= nx:
            continue
        u, v = U[ii, jj], V[ii, jj]
        if not (np.isfinite(u) and np.isfinite(v)):
            continue
        vts.append(hemi * (-u * np.sin(a) + v * np.cos(a)))  # proyeksi tangensial
    if len(vts) < 8:
        return 0.0, 0.0
    vts = np.asarray(vts)
    return float(vts.mean()), float((vts > 0).mean())


def _local_min_mask(P: np.ndarray, r: int) -> np.ndarray:
    """True di sel yang = minimum tekanan dalam jendela (2r+1)²."""
    ny, nx = P.shape
    Pp = np.pad(P, r, mode="constant", constant_values=np.inf)
    mn = np.full_like(P, np.inf)
    for di in range(2 * r + 1):
        for dj in range(2 * r + 1):
            mn = np.minimum(mn, Pp[di:di + ny, dj:dj + nx])
    return (P <= mn) & np.isfinite(P)


def _detect_frame(P, U, V, grid, min_wind_kt):
    ny, nx = P.shape
    west, south, east, north = grid["west"], grid["south"], grid["east"], grid["north"]
    dx = (east - west) / (nx - 1)
    dy = (north - south) / (ny - 1)
    spd_kt = np.sqrt(U * U + V * V) * MS_TO_KT

    r_win = max(2, int(round(1.0 / dx)))       # jendela cari min ~1°
    rad = max(3, int(round(3.0 / dx)))         # radius cek angin ~3°
    rr = min(5, rad)                           # ring cek "low tertutup"
    mask = _local_min_mask(P, r_win)

    cands = []
    ii, jj = np.where(mask)
    for i, j in zip(ii.tolist(), jj.tolist()):
        if i < 2 or j < 2 or i >= ny - 2 or j >= nx - 2:
            continue
        lat = north - i * dy
        lon = west + j * dx
        if abs(lat) < 2 or abs(lat) > 32:
            continue
        i0, i1 = max(0, i - rad), min(ny, i + rad + 1)
        j0, j1 = max(0, j - rad), min(nx, j + rad + 1)
        wmax = float(np.nanmax(spd_kt[i0:i1, j0:j1]))
        if not np.isfinite(wmax) or wmax < min_wind_kt:
            continue
        ring = P[max(0, i - rr):i + rr + 1, max(0, j - rr):j + rr + 1]
        if P[i, j] > float(np.nanmean(ring)) - 1.0:   # bukan low tertutup
            continue
        # SIRKULASI WAJIB: angin harus BERPUTAR mengelilingi pusat (bukan angin
        # kencang searah spt celah gunung). Dekat ekuator dibuat lebih ketat.
        vt_mean, cyc_frac = _ring_tangential(U, V, i, j, dx, lat)
        near_eq = abs(lat) < 5
        # dekat ekuator: andalkan frac tinggi (mayoritas titik berputar), bukan vt besar.
        if cyc_frac < (0.75 if near_eq else 0.66) or vt_mean < (3.0 if near_eq else 2.5):
            continue
        tier, code, label = _classify(wmax, lat, vt_mean)
        cands.append({"lat": round(lat, 3), "lon": round(lon, 3),
                      "mslp": round(float(P[i, j]), 1), "wind_kt": int(round(wmax)),
                      "cat": code, "label": label, "tier": tier})

    # dedup: gabung pusat berdekatan (<1.5°), pilih tekanan terendah
    cands.sort(key=lambda c: c["mslp"])
    keep = []
    for c in cands:
        if all(abs(c["lat"] - k["lat"]) + abs(c["lon"] - k["lon"]) > 1.5 for k in keep):
            keep.append(c)
    return keep


def _dist_deg(a, b):
    latm = (a["lat"] + b["lat"]) / 2.0
    dlat = a["lat"] - b["lat"]
    dlon = (a["lon"] - b["lon"]) * np.cos(np.radians(latm))
    return (dlat * dlat + dlon * dlon) ** 0.5


def detect_and_track(series: dict, times: list, grid: dict, min_wind_kt: float = 20.0) -> dict:
    """series = {'pressure':[arr..], 'u':[arr..], 'v':[arr..]} sejajar `times`.
    Kembalikan {'tracks':[{id,name,peak_*,points:[{t,lat,lon,mslp,wind_kt,cat,label}]}]}."""
    P, U, V = series.get("pressure"), series.get("u"), series.get("v")
    if not P or not U or not V:
        return {"tracks": []}
    nt = min(len(times), len(P), len(U), len(V))

    per_time = []
    for t in range(nt):
        dets = _detect_frame(np.asarray(P[t], "float32"), np.asarray(U[t], "float32"),
                             np.asarray(V[t], "float32"), grid, min_wind_kt)
        for d in dets:
            d["t"] = times[t]
        per_time.append(dets)

    MAX_STEP_DEG = 3.5   # perpindahan maks per langkah (~350 km); jembatani 1 langkah hilang
    tracks = []          # {points, _last, _last_t}
    for t in range(nt):
        used = set()
        # track aktif = terakhir terlihat 1–2 langkah lalu (bridging gap 1 langkah);
        # yang lebih baru diprioritaskan agar tak "dicuri" track basi.
        for tr in sorted([x for x in tracks if t - 2 <= x["_last_t"] < t], key=lambda x: -x["_last_t"]):
            gap = t - tr["_last_t"]
            best, bestd = None, MAX_STEP_DEG * gap
            for k, d in enumerate(per_time[t]):
                if k in used:
                    continue
                dd = _dist_deg(tr["_last"], d)
                if dd < bestd:
                    bestd, best = dd, k
            if best is not None:
                used.add(best)
                d = per_time[t][best]
                tr["points"].append(d); tr["_last"] = d; tr["_last_t"] = t
        for k, d in enumerate(per_time[t]):
            if k not in used:
                tracks.append({"points": [d], "_last": d, "_last_t": t})

    # peringkat track: tingkat tertinggi & angin puncak dulu
    def _rank(tr):
        return max((_TIER_RANK[p["tier"]], p["wind_kt"]) for p in tr["points"])
    tracks.sort(key=_rank, reverse=True)

    # Ambang tampil per tingkat: makin lemah, makin wajib PERSISTEN (kurangi noise
    # low monsun global). MIN_LEN = langkah minimum, MIN_PEAK = angin puncak minimum.
    MIN_LEN = {"EXTRA": 2, "TC": 1, "SEED": 3, "CIRC": 4}
    MIN_PEAK = {"EXTRA": 25, "TC": 34, "SEED": 30, "CIRC": 22}
    MAX_TRACKS = 12

    out = []
    counters = {"EXTRA": 0, "TC": 0, "SEED": 0, "CIRC": 0}
    names = {"EXTRA": "Siklon Lintang Tinggi", "TC": "Siklon Tropis",
             "SEED": "Bibit Siklon", "CIRC": "Sirkulasi Siklonik"}
    for tr in tracks:
        pts = [{"t": p["t"], "lat": p["lat"], "lon": p["lon"], "mslp": p["mslp"],
                "wind_kt": p["wind_kt"], "cat": p["cat"], "label": p["label"], "tier": p["tier"]}
               for p in tr["points"]]
        peak = max(tr["points"], key=lambda p: (_TIER_RANK[p["tier"]], p["wind_kt"]))
        tier = peak["tier"]
        if len(pts) < MIN_LEN[tier] or peak["wind_kt"] < MIN_PEAK[tier]:
            continue
        if tier == "TC" and len(pts) == 1 and peak["wind_kt"] < 40:
            continue
        counters[tier] += 1
        out.append({"id": len(out), "tier": tier,
                    "name": f"{names[tier]} {counters[tier]}",
                    "peak_wind_kt": peak["wind_kt"], "peak_cat": peak["cat"],
                    "peak_label": peak["label"], "points": pts})
        if len(out) >= MAX_TRACKS:
            break
    return {"tracks": out}
