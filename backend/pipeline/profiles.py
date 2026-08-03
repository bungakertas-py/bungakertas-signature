"""Profil vertikal atmosfer untuk diagram Skew-T log-P (per titik).

Mengunduh T/RH/angin di banyak level tekanan sekaligus (1 request/waktu,
multi-level), men-downsample ke grid lebih kasar (profil mulus, tak perlu 0.25
derajat), lalu menulis file biner terkompresi yang dimuat malas oleh frontend
saat kartu Skew-T dibuka:
  - profile.bin.gz   : biner int16/uint8 gzip, per var array (ntime, nlev, ny, nx).
  - profile_meta.json: layout (levels, times, grid, encoding).
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import warnings
from pathlib import Path

import numpy as np
import xarray as xr

from config import OUTPUT_DIR, PROFILE_LEVELS, PROFILE_STRIDE
from download import download_grib

warnings.filterwarnings("ignore", message="Ignoring index file")

_LEVELS_MB = [f"{p}_mb" for p in PROFILE_LEVELS]

# Encoding per variabel: nilai = stored * scale + offset.
_ENC = {
    "t": {"dtype": "int16", "np": np.int16, "scale": 0.1,  "offset": 0.0},   # °C
    "r": {"dtype": "uint8", "np": np.uint8, "scale": 1.0,  "offset": 0.0},   # %RH
    "u": {"dtype": "int16", "np": np.int16, "scale": 0.01, "offset": 0.0},   # m/s
    "v": {"dtype": "int16", "np": np.int16, "scale": 0.01, "offset": 0.0},
}


def fetch_profile(run: dt.datetime, fstep: int) -> tuple[dict, dict] | None:
    """Unduh + ekstrak profil satu waktu. Kembalikan ({t,r,u,v}, grid) sudah
    di-downsample & berorientasi baris-0=utara, kolom-0=barat. None bila gagal.
    Nilai: t dalam °C, r dalam %, u/v dalam m/s. Array shape (nlev, ny, nx)."""
    g = None
    try:
        g = download_grib(run, fstep, "multi", ["TMP", "RH", "UGRD", "VGRD"], levels=_LEVELS_MB)
        ds = xr.open_dataset(g, engine="cfgrib", backend_kwargs={
            "indexpath": "", "filter_by_keys": {"typeOfLevel": "isobaricInhPa"}}).load()
    except Exception as e:
        print(f"  ! profil f{fstep:03d} gagal: {e}")
        if g is not None:
            g.unlink(missing_ok=True)
        return None
    g.unlink(missing_ok=True)

    # orientasi: longitude menaik (barat->timur), latitude menurun (utara di baris-0)
    if float(ds.longitude[0]) > float(ds.longitude[-1]):
        ds = ds.isel(longitude=slice(None, None, -1))
    if float(ds.latitude[0]) < float(ds.latitude[-1]):
        ds = ds.isel(latitude=slice(None, None, -1))
    # urutkan level tepat PROFILE_LEVELS (permukaan -> atas)
    ds = ds.sel(isobaricInhPa=PROFILE_LEVELS)

    s = PROFILE_STRIDE
    lon = ds.longitude.values[::s]
    lat = ds.latitude.values[::s]
    arrs = {
        "t": ds["t"].values[:, ::s, ::s].astype("float32") - 273.15,
        "r": ds["r"].values[:, ::s, ::s].astype("float32"),
        "u": ds["u"].values[:, ::s, ::s].astype("float32"),
        "v": ds["v"].values[:, ::s, ::s].astype("float32"),
    }
    grid = {"west": float(lon[0]), "east": float(lon[-1]),
            "north": float(lat[0]), "south": float(lat[-1]),
            "nx": int(len(lon)), "ny": int(len(lat))}
    return arrs, grid


def write_profile_data(times: list[str], per_time: list[dict], grid: dict,
                       out_dir: Path = OUTPUT_DIR) -> int:
    """Tulis profile.bin.gz + profile_meta.json dari daftar profil per-waktu.
    per_time[i] = {t,r,u,v} shape (nlev, ny, nx) untuk times[i]."""
    ntime, nlev, ny, nx = len(times), len(PROFILE_LEVELS), grid["ny"], grid["nx"]
    blob = bytearray()
    layout = []
    for var, enc in _ENC.items():
        stacked = np.stack([np.nan_to_num(pt[var]) for pt in per_time]).astype("float32")  # (t,lev,ny,nx)
        stored = np.round((stacked - enc["offset"]) / enc["scale"])
        lo, hi = (0, 255) if enc["dtype"] == "uint8" else (-32768, 32767)
        stored = np.clip(stored, lo, hi).astype(enc["np"])
        b = stored.tobytes(order="C")
        layout.append({"var": var, "dtype": enc["dtype"], "scale": enc["scale"],
                       "offset": enc["offset"], "byteOffset": len(blob), "byteLength": len(b)})
        blob += b
    (out_dir / "profile.bin.gz").write_bytes(gzip.compress(bytes(blob), compresslevel=6))
    meta = {
        "bounds": [grid["west"], grid["south"], grid["east"], grid["north"]],
        "nx": nx, "ny": ny,
        "dx": round((grid["east"] - grid["west"]) / (nx - 1), 4),
        "dy": round((grid["north"] - grid["south"]) / (ny - 1), 4),
        "levels": PROFILE_LEVELS, "times": times, "vars": layout,
    }
    (out_dir / "profile_meta.json").write_text(json.dumps(meta))
    return (out_dir / "profile.bin.gz").stat().st_size


def build_profiles(run: dt.datetime, steps: list[int], times_of: dict) -> int:
    """Unduh & tulis profil untuk daftar langkah forecast. times_of[fstep] =
    valid_time string. Kembalikan ukuran file (byte), 0 bila kosong."""
    ptimes, per, pgrid = [], [], None
    for fstep in steps:
        res = fetch_profile(run, fstep)
        if res is None:
            continue
        arrs, pgrid = res
        per.append(arrs)
        ptimes.append(times_of[fstep])
        print(f"  + profil f{fstep:03d} valid {times_of[fstep]}")
    if not per or pgrid is None:
        return 0
    return write_profile_data(ptimes, per, pgrid)
