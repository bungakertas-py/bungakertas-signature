"""
Orchestrator pipeline: ambil run GFS terbaru -> proses banyak langkah forecast
-> tulis katalog (catalog.json) yang dibaca frontend.

Jalankan: python run.py
Nanti di produksi, script inilah yang dipanggil scheduler (cron / GitHub Actions)
tiap 6 jam.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from config import LAYERS, OUTPUT_DIR, RAW_DIR
from download import download_grib, latest_available_run
from process import process_wind

# Langkah forecast yang diambil (jam): 0..72 tiap 3 jam = 25 frame (3 hari).
# GFS 0p25 mendukung s/d 384 jam; bisa diperpanjang lagi (cadence campuran).
FORECAST_STEPS = list(range(0, 73, 3))


def run_layer_wind(layer_key: str, run: dt.datetime, steps: list[int]) -> list[dict]:
    layer = LAYERS[layer_key]
    var_names = [layer["u_var"], layer["v_var"]]
    grib_level = layer["grib_level"]
    frames: list[dict] = []

    for fstep in steps:
        try:
            grib = download_grib(run, fstep, grib_level, var_names)
        except Exception as e:  # run belum lengkap / gangguan jaringan
            print(f"  ! lewati f{fstep:03d}: {e}")
            continue
        meta = process_wind(grib, layer_key, run, fstep)
        frames.append(meta)
        grib.unlink(missing_ok=True)  # buang GRIB mentah, hemat disk
        print(f"  + f{fstep:03d} valid {meta['valid_time']}  "
              f"(max {meta['speed_knots_max']} kt)")
    return frames


def build_catalog(run: dt.datetime, layers: dict[str, list[dict]]) -> dict:
    """Susun katalog: daftar layer, model, dan frame (per waktu) yang tersedia."""
    catalog = {
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model": "GFS",
        "run_time": run.strftime("%Y-%m-%dT%H:00:00Z"),
        "region": None,
        "layers": {},
    }
    for layer_key, frames in layers.items():
        if not frames:
            continue
        if catalog["region"] is None:
            catalog["region"] = {"bounds": frames[0]["bounds"]}
        catalog["layers"][layer_key] = {
            "kind": frames[0]["kind"],
            "level": frames[0]["level"],
            "units": frames[0]["units"],
            "unscale": frames[0]["unscale"],
            "frames": [
                {
                    "valid_time": f["valid_time"],
                    "forecast_step_hours": f["forecast_step_hours"],
                    "data_image": f["data_image"],
                    "preview_image": f["preview_image"],
                    "velocity_json": f["velocity_json"],
                    "speed_knots_max": f.get("speed_knots_max"),
                }
                for f in frames
            ],
        }
    return catalog


def main() -> None:
    run = latest_available_run()
    print(f"== Pipeline Peta Cuaca (GFS) ==")
    print(f"Run GFS: {run:%Y-%m-%d %HZ} | langkah: {FORECAST_STEPS}")

    layers_out: dict[str, list[dict]] = {}
    for layer_key in LAYERS:
        print(f"\nLayer: {layer_key}")
        layers_out[layer_key] = run_layer_wind(layer_key, run, FORECAST_STEPS)

    catalog = build_catalog(run, layers_out)
    (OUTPUT_DIR / "catalog.json").write_text(json.dumps(catalog, indent=2))
    total = sum(len(v) for v in layers_out.values())
    print(f"\nSelesai. {total} frame ditulis. Katalog: {OUTPUT_DIR / 'catalog.json'}")


if __name__ == "__main__":
    main()
