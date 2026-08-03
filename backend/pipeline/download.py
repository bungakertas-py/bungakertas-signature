"""
Downloader GFS via NOMADS "filter" endpoint.

Kita hanya mengunduh subset kecil: variabel yang dibutuhkan + region ASEAN.
Hasilnya file GRIB2 berukuran kecil (ratusan KB), bukan file global (ratusan MB).
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import requests

from config import GFS, REGION, RAW_DIR


def latest_available_run(now_utc: dt.datetime | None = None) -> dt.datetime:
    """Tentukan run GFS terbaru yang kemungkinan besar sudah tersedia.

    Memperhitungkan jeda rilis (availability_lag_hours). Mengembalikan datetime
    UTC yang jamnya salah satu dari run_hours (00/06/12/18).
    """
    now = now_utc or dt.datetime.now(dt.timezone.utc)
    # mundur sejauh jeda rilis agar tidak menodong run yang belum ada
    candidate = now - dt.timedelta(hours=GFS["availability_lag_hours"])
    # bulatkan turun ke run_hour terdekat
    run_hours = sorted(GFS["run_hours"], reverse=True)
    day = candidate.replace(minute=0, second=0, microsecond=0)
    for h in run_hours:
        if candidate.hour >= h:
            return day.replace(hour=h)
    # sebelum run 00Z hari ini -> ambil 18Z kemarin
    prev = day - dt.timedelta(days=1)
    return prev.replace(hour=max(run_hours))


def build_filter_url(run: dt.datetime, fstep: int, grib_level: str, var_names: list[str],
                     levels: list[str] | None = None) -> str:
    """Susun URL NOMADS filter untuk satu langkah forecast.

    Bila `levels` diberi (daftar level, mis. ['1000_mb','850_mb',...]), semua level
    itu diminta sekaligus dalam SATU file (untuk profil vertikal). Bila tidak,
    pakai `grib_level` tunggal.
    """
    ymd = run.strftime("%Y%m%d")
    cc = run.strftime("%H")
    fff = f"{fstep:03d}"
    params = {
        "file": f"gfs.t{cc}z.pgrb2.0p25.f{fff}",
        "subregion": "",
        "leftlon": REGION["left_lon"],
        "rightlon": REGION["right_lon"],
        "toplat": REGION["top_lat"],
        "bottomlat": REGION["bottom_lat"],
        "dir": f"/gfs.{ymd}/{cc}/atmos",
    }
    for lv in (levels if levels else [grib_level]):
        params[f"lev_{lv}"] = "on"
    for v in var_names:
        params[f"var_{v}"] = "on"

    # requests akan meng-encode; tapi NOMADS butuh key kosong var_*/lev_* tetap "=on"
    query = "&".join(f"{k}={requests.utils.quote(str(v), safe='')}" for k, v in params.items())
    return f"{GFS['filter_url']}?{query}"


def download_grib(run: dt.datetime, fstep: int, grib_level: str, var_names: list[str],
                  dest: Path | None = None, timeout: int = 120,
                  levels: list[str] | None = None) -> Path:
    """Unduh satu file GRIB2 subset. Mengembalikan path file lokal.

    Melempar RuntimeError bila respons bukan GRIB (mis. run belum tersedia).
    `levels` opsional: minta banyak level isobarik sekaligus (profil vertikal).
    """
    url = build_filter_url(run, fstep, grib_level, var_names, levels=levels)
    if dest is None:
        ymd_cc = run.strftime("%Y%m%d_%H")
        tag = "multi" if levels else grib_level
        dest = RAW_DIR / f"gfs_{ymd_cc}_f{fstep:03d}_{tag}.grib2"

    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    content = resp.content
    # GFS/GRIB2 diawali magic bytes "GRIB"
    if not content.startswith(b"GRIB"):
        snippet = content[:200].decode("utf-8", "replace")
        raise RuntimeError(
            f"Respons bukan GRIB (run {run:%Y-%m-%d %HZ} f{fstep:03d} mungkin belum tersedia). "
            f"Cuplikan: {snippet!r}"
        )
    dest.write_bytes(content)
    return dest


if __name__ == "__main__":
    run = latest_available_run()
    print(f"Run GFS terbaru (perkiraan tersedia): {run:%Y-%m-%d %H:%M UTC}")
    path = download_grib(run, fstep=0, grib_level="10_m_above_ground",
                         var_names=["UGRD", "VGRD"])
    print(f"Terunduh: {path}  ({path.stat().st_size/1024:.1f} KB)")
