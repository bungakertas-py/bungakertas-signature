"""
Orchestrator pipeline: ambil run GFS terbaru -> proses tiap layer & langkah
forecast -> rekonsiliasi (retensi window) -> tulis catalog.json untuk frontend.

Jalankan: python run.py
Di produksi dipanggil GitHub Actions (cron 1x/hari 04:00 WIB). Sebelum ini,
hydrate.py memulihkan frame lama dari situs live agar window -24 jam terjaga.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
from pathlib import Path

from config import KEEP_PAST_HOURS, LAYERS, OUTPUT_DIR
from download import download_grib, latest_available_run
from process import process_scalar, process_wind

# Langkah forecast yang diambil (jam): 0..72 tiap 3 jam (3 hari ke depan).
FORECAST_STEPS = list(range(0, 73, 3))


def _var_names(layer: dict) -> list[str]:
    if layer["kind"] == "vector":
        return [layer["u_var"], layer["v_var"]]
    return [layer["var"]]


def run_layer(layer_key: str, run: dt.datetime, steps: list[int]) -> int:
    """Unduh + proses satu layer untuk semua langkah forecast. Kembalikan jumlah frame."""
    layer = LAYERS[layer_key]
    grib_level = layer["grib_level"]
    var_names = _var_names(layer)
    n = 0
    for fstep in steps:
        try:
            grib = download_grib(run, fstep, grib_level, var_names)
            if layer["kind"] == "vector":
                meta = process_wind(grib, layer_key, run, fstep)
                extra = f"max {meta['speed_knots_max']} kt"
            else:
                meta = process_scalar(grib, layer_key, run, fstep)
                extra = f"max {meta['value_max']} {meta['units']}"
            grib.unlink(missing_ok=True)  # buang GRIB mentah, hemat disk
        except Exception as e:  # run belum lengkap / gangguan jaringan / decode
            print(f"  ! lewati {layer_key} f{fstep:03d}: {e}")
            continue
        n += 1
        print(f"  + {layer_key} f{fstep:03d} valid {meta['valid_time']}  ({extra})")
    return n


def _parse(ts: str) -> dt.datetime:
    return dt.datetime.strptime(ts, "%Y-%m-%dT%H:00:00Z").replace(tzinfo=dt.timezone.utc)


def _frame_files(meta: dict) -> list[Path]:
    """Semua file milik satu frame (meta + gambar + velocity)."""
    files = [Path(meta["_path"])]
    for key in ("data_image", "preview_image", "velocity_json"):
        if meta.get(key):
            files.append(OUTPUT_DIR / meta[key])
    return files


def reconcile_and_catalog(run: dt.datetime) -> tuple[dict, int]:
    """Kumpulkan SEMUA frame di disk (lintas run), buang yang lebih tua dari
    (run - KEEP_PAST_HOURS), dedup per (layer, valid_time) pilih run terbaru,
    lalu susun catalog. Mengembalikan (catalog, jumlah_frame)."""
    cutoff = run - dt.timedelta(hours=KEEP_PAST_HOURS)

    metas: list[dict] = []
    for mp in glob.glob(str(OUTPUT_DIR / "*_f*.json")):
        p = Path(mp)
        if p.name.endswith("_velocity.json"):
            continue
        try:
            m = json.loads(p.read_text())
        except Exception:
            continue
        if "valid_time" not in m or "layer" not in m:
            continue
        m["_path"] = mp
        metas.append(m)

    # 1) buang frame lebih tua dari cutoff (-24 jam)
    kept: list[dict] = []
    for m in metas:
        if _parse(m["valid_time"]) < cutoff:
            for f in _frame_files(m):
                Path(f).unlink(missing_ok=True)
        else:
            kept.append(m)

    # 2) dedup per (layer, valid_time) -> run_time terbaru menang; sisanya dihapus
    best: dict[tuple, dict] = {}
    losers: list[dict] = []
    for m in kept:
        k = (m["layer"], m["valid_time"])
        cur = best.get(k)
        if cur is None or _parse(m["run_time"]) > _parse(cur["run_time"]):
            if cur is not None:
                losers.append(cur)
            best[k] = m
        else:
            losers.append(m)
    for m in losers:
        for f in _frame_files(m):
            Path(f).unlink(missing_ok=True)

    # 3) susun catalog dari frame pemenang
    by_layer: dict[str, list[dict]] = {}
    for m in best.values():
        by_layer.setdefault(m["layer"], []).append(m)

    catalog = {
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model": "GFS",
        "run_time": run.strftime("%Y-%m-%dT%H:00:00Z"),
        "region": None,
        "layers": {},
    }
    total = 0
    for layer_key in LAYERS:  # jaga urutan definisi (angin dulu, lalu hujan)
        frames = by_layer.get(layer_key)
        if not frames:
            continue
        frames.sort(key=lambda m: _parse(m["valid_time"]))
        if catalog["region"] is None:
            catalog["region"] = {"bounds": frames[0]["bounds"]}
        entry = {
            "kind": frames[0]["kind"],
            "level": frames[0]["level"],
            "units": frames[0]["units"],
            "frames": [],
        }
        if frames[0].get("unscale") is not None:
            entry["unscale"] = frames[0]["unscale"]
        for m in frames:
            fr = {
                "valid_time": m["valid_time"],
                "forecast_step_hours": m["forecast_step_hours"],
                "preview_image": m["preview_image"],
            }
            for key in ("data_image", "velocity_json", "speed_knots_max", "value_max"):
                if m.get(key) is not None:
                    fr[key] = m[key]
            entry["frames"].append(fr)
        catalog["layers"][layer_key] = entry
        total += len(frames)
    return catalog, total


def main() -> None:
    run = latest_available_run()
    cutoff = run - dt.timedelta(hours=KEEP_PAST_HOURS)
    print("== Pipeline Peta Cuaca (GFS) ==")
    print(f"Run GFS: {run:%Y-%m-%d %HZ} | langkah: {FORECAST_STEPS[0]}..{FORECAST_STEPS[-1]} jam")

    for layer_key in LAYERS:
        print(f"\nLayer: {layer_key}")
        run_layer(layer_key, run, FORECAST_STEPS)

    catalog, total = reconcile_and_catalog(run)
    (OUTPUT_DIR / "catalog.json").write_text(json.dumps(catalog, indent=2))
    days = sorted({f["valid_time"][:10]
                   for L in catalog["layers"].values() for f in L["frames"]})
    print(f"\nSelesai. {total} frame di katalog (retensi >= {cutoff:%Y-%m-%d %HZ}).")
    print(f"Layer: {list(catalog['layers'])} | tanggal: {days}")


if __name__ == "__main__":
    main()
