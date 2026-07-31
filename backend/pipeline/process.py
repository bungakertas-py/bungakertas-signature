"""
Processor: GRIB2 -> aset siap-frontend.

Menghasilkan, untuk satu layer angin pada satu langkah forecast:
  1. <name>.png       : PNG data (R=u, G=v terkemas) untuk engine partikel WeatherLayers GL.
  2. <name>_preview.png: PNG pratinjau kecepatan angin berwarna (skala knots) — untuk
                         verifikasi cepat oleh manusia tanpa perlu frontend.
  3. <name>.json      : metadata (bounds, dimensi, unscale, waktu run & valid).
"""
from __future__ import annotations

import datetime as dt
import json
import warnings
from pathlib import Path

import numpy as np
import xarray as xr
from PIL import Image

from config import LAYERS, OUTPUT_DIR, REGION

warnings.filterwarnings("ignore", message="Ignoring index file")

# Skala warna kecepatan angin (knots) meniru legend BMKG Signature.
# (batas_knots, (R, G, B))
_KNOTS_SCALE = [
    (0,   (0x00, 0x30, 0x50)),   # tenang - biru gelap
    (5,   (0x2b, 0x83, 0xba)),   # biru
    (10,  (0x5a, 0xa8, 0xcf)),
    (15,  (0xab, 0xdd, 0xa4)),   # hijau muda
    (20,  (0x66, 0xbd, 0x63)),   # hijau
    (25,  (0xd9, 0xef, 0x8b)),   # kuning-hijau
    (34,  (0xfe, 0xe0, 0x8b)),   # kuning
    (48,  (0xfd, 0xae, 0x61)),   # oranye
    (64,  (0xf4, 0x6d, 0x43)),   # oranye-merah
    (80,  (0xd7, 0x30, 0x27)),   # merah
    (100, (0xa5, 0x00, 0x26)),   # merah tua
    (120, (0x7a, 0x00, 0x77)),   # ungu
]

MS_TO_KNOTS = 1.943844

# Skala warna hujan (mm/jam) -> RGBA. Alpha 0 saat kering supaya peta tembus.
_RAIN_SCALE = [
    (0.0,   (0x00, 0x00, 0x00,   0)),
    (0.2,   (0xa6, 0xd8, 0xf5,  80)),
    (0.5,   (0x5b, 0xa3, 0xe0, 140)),
    (1.0,   (0x2b, 0x6c, 0xc4, 190)),
    (2.0,   (0x35, 0xa8, 0x5a, 210)),
    (5.0,   (0x9c, 0xd6, 0x3f, 220)),
    (10.0,  (0xf2, 0xd0, 0x3a, 228)),
    (20.0,  (0xf2, 0x8a, 0x1f, 234)),
    (50.0,  (0xe0, 0x37, 0x2a, 240)),
    (100.0, (0x9c, 0x27, 0x8f, 246)),
]


def _load_wind(grib_path: Path) -> tuple[np.ndarray, np.ndarray, dict]:
    """Muat u/v dari GRIB, orientasikan agar baris-0 = utara, kolom-0 = barat."""
    ds = xr.open_dataset(grib_path, engine="cfgrib",
                         backend_kwargs={"indexpath": ""})
    u = ds["u10"]
    v = ds["v10"]

    # pastikan longitude menaik (barat->timur)
    if float(ds.longitude[0]) > float(ds.longitude[-1]):
        u = u.isel(longitude=slice(None, None, -1))
        v = v.isel(longitude=slice(None, None, -1))
    # pastikan latitude menurun (utara di baris-0); GFS subset kita urut naik -> flip
    if float(ds.latitude[0]) < float(ds.latitude[-1]):
        u = u.isel(latitude=slice(None, None, -1))
        v = v.isel(latitude=slice(None, None, -1))

    meta = {
        "west": float(min(ds.longitude.values)),
        "east": float(max(ds.longitude.values)),
        "south": float(min(ds.latitude.values)),
        "north": float(max(ds.latitude.values)),
        "width": int(u.sizes["longitude"]),
        "height": int(u.sizes["latitude"]),
    }
    return u.values.astype("float32"), v.values.astype("float32"), meta


def _encode_vector_png(u: np.ndarray, v: np.ndarray, unscale: list[float], dest: Path) -> None:
    """Kemas u,v ke PNG RGBA: R=u, G=v (skala unscale), A=255 valid / 0 jika NaN."""
    lo, hi = unscale
    rng = hi - lo
    valid = np.isfinite(u) & np.isfinite(v)

    def enc(a):
        n = np.clip((a - lo) / rng, 0.0, 1.0)
        return (n * 255.0).round().astype("uint8")

    r = enc(np.nan_to_num(u))
    g = enc(np.nan_to_num(v))
    b = np.zeros_like(r)
    alpha = np.where(valid, 255, 0).astype("uint8")
    rgba = np.dstack([r, g, b, alpha])
    Image.fromarray(rgba, mode="RGBA").save(dest)


def _speed_to_rgb(speed_knots: np.ndarray) -> np.ndarray:
    """Petakan kecepatan (knots) ke RGB via interpolasi linear skala BMKG."""
    stops = np.array([s[0] for s in _KNOTS_SCALE], dtype="float32")
    cols = np.array([s[1] for s in _KNOTS_SCALE], dtype="float32")
    out = np.empty(speed_knots.shape + (3,), dtype="float32")
    for c in range(3):
        out[..., c] = np.interp(speed_knots, stops, cols[:, c])
    return out.round().astype("uint8")


def _render_speed_preview(u: np.ndarray, v: np.ndarray, dest: Path, scale: int = 4) -> None:
    """PNG pratinjau kecepatan angin berwarna (untuk mata manusia)."""
    speed_kt = np.sqrt(u ** 2 + v ** 2) * MS_TO_KNOTS
    rgb = _speed_to_rgb(np.nan_to_num(speed_kt))
    img = Image.fromarray(rgb, mode="RGB")
    if scale > 1:
        img = img.resize((img.width * scale, img.height * scale), Image.BILINEAR)
    img.save(dest)


def _load_scalar(grib_path: Path, filter_keys: dict | None = None) -> tuple[np.ndarray, dict]:
    """Muat satu variabel skalar dari GRIB, orientasi baris-0=utara, kolom-0=barat.

    filter_keys mis. {'stepType': 'instant'} untuk memilih satu varian bila GRIB
    punya beberapa (spt PRATE instant vs avg).
    """
    backend_kwargs = {"indexpath": ""}
    if filter_keys:
        backend_kwargs["filter_by_keys"] = filter_keys
    ds = xr.open_dataset(grib_path, engine="cfgrib", backend_kwargs=backend_kwargs)
    name = list(ds.data_vars)[0]            # subset kita hanya 1 variabel
    da = ds[name]
    if float(ds.longitude[0]) > float(ds.longitude[-1]):
        da = da.isel(longitude=slice(None, None, -1))
    if float(ds.latitude[0]) < float(ds.latitude[-1]):
        da = da.isel(latitude=slice(None, None, -1))
    meta = {
        "west": float(min(ds.longitude.values)),
        "east": float(max(ds.longitude.values)),
        "south": float(min(ds.latitude.values)),
        "north": float(max(ds.latitude.values)),
        "width": int(da.sizes["longitude"]),
        "height": int(da.sizes["latitude"]),
    }
    return da.values.astype("float32"), meta


def _scalar_to_rgba(values: np.ndarray, scale: list) -> np.ndarray:
    """Petakan nilai skalar ke RGBA via interpolasi linear skala warna."""
    stops = np.array([s[0] for s in scale], dtype="float32")
    cols = np.array([s[1] for s in scale], dtype="float32")   # (n, 4)
    v = np.nan_to_num(values)
    out = np.empty(v.shape + (4,), dtype="float32")
    for c in range(4):
        out[..., c] = np.interp(v, stops, cols[:, c])
    return out.round().astype("uint8")


def _render_scalar_preview(values: np.ndarray, scale: list, dest: Path, scale_up: int = 4) -> None:
    """PNG heatmap skalar berwarna (RGBA, transparan di area nilai ~0)."""
    rgba = _scalar_to_rgba(values, scale)
    img = Image.fromarray(rgba, mode="RGBA")
    if scale_up > 1:
        img = img.resize((img.width * scale_up, img.height * scale_up), Image.BILINEAR)
    img.save(dest)


_SCALAR_SCALES = {"rain_surface": _RAIN_SCALE}


def _export_velocity_json(u: np.ndarray, v: np.ndarray, grid: dict,
                          run: dt.datetime, fstep: int, dest: Path) -> None:
    """Tulis JSON format 'velocity' (dipakai leaflet-velocity / earth wind-js).

    Urutan data: baris-major dari la1(utara) ke la2(selatan), lo1(barat) ke lo2(timur).
    u = parameterNumber 2, v = parameterNumber 3 (parameterCategory 2 = momentum).
    """
    nx, ny = grid["width"], grid["height"]
    dx = round((grid["east"] - grid["west"]) / (nx - 1), 4)
    dy = round((grid["north"] - grid["south"]) / (ny - 1), 4)
    header = {
        "lo1": grid["west"], "la1": grid["north"],
        "lo2": grid["east"], "la2": grid["south"],
        "nx": nx, "ny": ny, "dx": dx, "dy": dy,
        "parameterCategory": 2, "parameterUnit": "m.s-1",
        "refTime": run.strftime("%Y-%m-%dT%H:00:00Z"),
        "forecastTime": fstep,
    }
    u_flat = np.nan_to_num(u).astype("float32").ravel(order="C").round(2).tolist()
    v_flat = np.nan_to_num(v).astype("float32").ravel(order="C").round(2).tolist()
    payload = [
        {"header": {**header, "parameterNumber": 2, "parameterNumberName": "U-component_of_wind"}, "data": u_flat},
        {"header": {**header, "parameterNumber": 3, "parameterNumberName": "V-component_of_wind"}, "data": v_flat},
    ]
    dest.write_text(json.dumps(payload, separators=(",", ":")))


def process_wind(grib_path: Path, layer_key: str, run: dt.datetime, fstep: int,
                 out_dir: Path = OUTPUT_DIR) -> dict:
    """Proses satu file GRIB angin -> PNG data + preview + velocity JSON + metadata."""
    layer = LAYERS[layer_key]
    u, v, grid = _load_wind(grib_path)

    valid_time = run + dt.timedelta(hours=fstep)
    stamp = f"{run:%Y%m%d_%H}_f{fstep:03d}"
    base = f"{layer_key}_{stamp}"

    data_png = out_dir / f"{base}.png"
    preview_png = out_dir / f"{base}_preview.png"
    velocity_json = out_dir / f"{base}_velocity.json"
    meta_json = out_dir / f"{base}.json"

    _encode_vector_png(u, v, layer["unscale"], data_png)
    _render_speed_preview(u, v, preview_png)
    _export_velocity_json(u, v, grid, run, fstep, velocity_json)

    speed_kt = np.sqrt(u ** 2 + v ** 2) * MS_TO_KNOTS
    meta = {
        "layer": layer_key,
        "kind": layer["kind"],
        "model": "GFS",
        "level": layer["level_label"],
        "run_time": run.strftime("%Y-%m-%dT%H:00:00Z"),
        "forecast_step_hours": fstep,
        "valid_time": valid_time.strftime("%Y-%m-%dT%H:00:00Z"),
        "bounds": [grid["west"], grid["south"], grid["east"], grid["north"]],
        "width": grid["width"],
        "height": grid["height"],
        "unscale": layer["unscale"],
        "units": "m/s",
        "data_image": data_png.name,
        "preview_image": preview_png.name,
        "velocity_json": velocity_json.name,
        "speed_knots_max": round(float(np.nanmax(speed_kt)), 1),
    }
    meta_json.write_text(json.dumps(meta, indent=2))
    return meta


def process_scalar(grib_path: Path, layer_key: str, run: dt.datetime, fstep: int,
                   out_dir: Path = OUTPUT_DIR) -> dict:
    """Proses satu file GRIB skalar (mis. hujan) -> PNG heatmap berwarna + metadata."""
    layer = LAYERS[layer_key]
    values, grid = _load_scalar(grib_path, layer.get("filter_keys"))
    values = values * float(layer.get("to_unit", 1.0))   # ke satuan tampilan (mm/jam)

    valid_time = run + dt.timedelta(hours=fstep)
    base = f"{layer_key}_{run:%Y%m%d_%H}_f{fstep:03d}"
    preview_png = out_dir / f"{base}_preview.png"
    meta_json = out_dir / f"{base}.json"

    scale = _SCALAR_SCALES.get(layer_key, _RAIN_SCALE)
    _render_scalar_preview(values, scale, preview_png)

    meta = {
        "layer": layer_key,
        "kind": "scalar",
        "model": "GFS",
        "level": layer["level_label"],
        "run_time": run.strftime("%Y-%m-%dT%H:00:00Z"),
        "forecast_step_hours": fstep,
        "valid_time": valid_time.strftime("%Y-%m-%dT%H:00:00Z"),
        "bounds": [grid["west"], grid["south"], grid["east"], grid["north"]],
        "width": grid["width"],
        "height": grid["height"],
        "units": layer["units"],
        "preview_image": preview_png.name,
        "value_max": round(float(np.nanmax(values)), 2),
    }
    meta_json.write_text(json.dumps(meta, indent=2))
    return meta


if __name__ == "__main__":
    import glob
    from config import RAW_DIR

    grib = sorted(glob.glob(str(RAW_DIR / "gfs_*_f000_10_m_above_ground.grib2")))[-1]
    # ekstrak run & fstep dari nama file
    name = Path(grib).stem  # gfs_YYYYMMDD_HH_fSSS_...
    parts = name.split("_")
    run = dt.datetime.strptime(parts[1] + parts[2], "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)
    fstep = int(parts[3][1:])
    meta = process_wind(Path(grib), "wind_surface", run, fstep)
    print("OK — metadata:")
    print(json.dumps(meta, indent=2))
