/* Peta Cuaca — frontend (angin + hujan, GFS)
 * Membaca catalog.json + aset dari pipeline backend; angin = partikel + heatmap
 * kecepatan, hujan = heatmap laju hujan. Layout & gaya ala BMKG Signature.
 */
const DATA_BASE = "../backend/data/output/";

// Definisi legend per layer: [label, warna, teksPutih?]
const LEGENDS = {
  wind_surface: {
    head: "KNOTS",
    cells: [["5", "#2b83ba", 1], ["10", "#5aa8cf", 0], ["15", "#abdda4", 0], ["20", "#66bd63", 0],
            ["25", "#d9ef8b", 0], ["34", "#fee08b", 0], ["48", "#fdae61", 0], ["64", "#f46d43", 1],
            ["80", "#d73027", 1], ["100+", "#a50026", 1]],
  },
  rain_surface: {
    head: "mm/jam",
    cells: [["2", "#14378f", 1], ["4", "#2360c8", 1], ["8", "#22a5e0", 0], ["10", "#23d3c0", 0],
            ["15", "#35c84a", 0], ["20", "#8ed82a", 0], ["25", "#ead821", 0], ["30", "#f5a91e", 0],
            ["35", "#f2701c", 1], ["40", "#e42320", 1], ["50", "#e33bbf", 1], ["60", "#8a29c8", 1]],
  },
  rain_accum_surface: {
    head: "mm/hari",
    cells: [["5", "#14378f", 1], ["10", "#2360c8", 1], ["20", "#22a5e0", 0], ["40", "#23d3c0", 0],
            ["60", "#35c84a", 0], ["90", "#8ed82a", 0], ["120", "#ead821", 0], ["150", "#f5a91e", 0],
            ["200", "#f2701c", 1], ["300", "#e42320", 1], ["400", "#e33bbf", 1], ["500", "#8a29c8", 1]],
  },
  temp_surface: {
    head: "°C",
    cells: [["0", "#253fa0", 1], ["8", "#2b83ba", 1], ["16", "#4daf8f", 0], ["22", "#a6d96a", 0],
            ["28", "#fee08b", 0], ["32", "#fdae61", 0], ["36", "#f46d43", 1], ["42", "#a50026", 1]],
  },
  humidity_surface: {
    head: "%",
    cells: [["0", "#7a450a", 1], ["25", "#b9843a", 1], ["50", "#88b055", 0], ["70", "#359a86", 1],
            ["85", "#216bb0", 1], ["100", "#123f86", 1]],
  },
  cloud_surface: {
    head: "%",
    cells: [["20", "#c8d0d8", 0], ["50", "#aab4be", 0], ["80", "#96a0ac", 1], ["100", "#78828e", 1]],
  },
  pressure_surface: {
    head: "hPa",
    cells: [["980", "#5e3c99", 1], ["995", "#356bc4", 1], ["1005", "#7dc8d8", 0], ["1013", "#f0f0e0", 0],
            ["1020", "#f4c060", 0], ["1030", "#e05a3a", 1]],
  },
};

// Tema per-layer: "dark" = latar peta gelap (overlay putih); "light" = latar
// terang (overlay gelap). Menentukan label/batas/partikel.
const LAYER_THEME = {
  wind_surface: "dark", rain_surface: "dark", rain_accum_surface: "dark",
  temp_surface: "dark", humidity_surface: "dark", cloud_surface: "dark", pressure_surface: "dark",
};

// Override warna border batas administrasi per-layer (selain default tema).
const BORDER_COLOR = {
  temp_surface: "#000000",       // batas hitam di atas heatmap suhu
  humidity_surface: "#000000",   // batas hitam di atas heatmap kelembapan
  pressure_surface: "#000000",   // batas hitam di atas heatmap tekanan
};

// Layer dengan data HARIAN (1 frame/hari; slider = tanggal saja, tanpa jam).
const DAILY_LAYERS = new Set(["rain_accum_surface"]);

// ---- Peta dasar (gelap, ala screenshot) --------------------------------
const map = L.map("map", {
  center: [5, 116],
  zoom: 4,
  minZoom: 3,
  maxZoom: 9,
  zoomSnap: 0,             // izinkan zoom pecahan → bingkai bisa pas mengisi layar
  zoomControl: false,      // pakai tombol zoom neubrutalist sendiri
  attributionControl: false, // kredit ditaruh di footer sidebar
  maxBoundsViscosity: 1.0, // dinding keras: tak bisa geser keluar kotak
  preferCanvas: true,      // render vektor (batas) via Canvas: hanya yang masuk frame
  wheelPxPerZoomLevel: 120,// scroll-zoom lebih landai → terasa lebih mulus
  wheelDebounceTime: 30,
});

// Kotak inti yang WAJIB selalu tampak penuh: India–Pasifik Barat, Cina Selatan–
// tengah Australia. Bingkai tampilan diturunkan dari kotak ini, diperlebar
// mengikuti rasio layar. Domain DATA (dari catalog) lebih luas dari kotak ini di
// tiap sisi → tepi data tak pernah terlihat.
const VIEW_CORE = L.latLngBounds([-28, 68], [28, 174]);

L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
  attribution: '&copy; OpenStreetMap &copy; CARTO | Data: NOAA GFS',
  subdomains: "abcd",
  maxZoom: 12,
  updateWhenZooming: false, // tunda muat tile sampai zoom selesai → animasi mulus
  keepBuffer: 4,
}).addTo(map);

// Pane heatmap kecepatan angin: di atas peta dasar (z200), di bawah partikel
// (overlayPane z400) & label (z650). Ini "kontur warna" ala BMKG Signature.
const speedPane = map.createPane("speed");
speedPane.style.zIndex = 350;
speedPane.style.pointerEvents = "none";

// Pane batas administrasi (garis negara & provinsi): di atas partikel
// (overlayPane z400), di bawah label (z650).
const adminPane = map.createPane("admin");
adminPane.style.zIndex = 450;
adminPane.style.pointerEvents = "none";

// Label negara/laut di atas partikel supaya tetap terbaca
const labelPane = map.createPane("labels");
labelPane.style.zIndex = 650;
labelPane.style.pointerEvents = "none";
// Dua set label: GELAP (teks terang, utk tema gelap/angin) & TERANG (teks gelap,
// utk tema terang/hujan). Ditukar oleh applyTheme() sesuai layer aktif.
const _lblOpts = { subdomains: "abcd", pane: "labels", updateWhenZooming: false, keepBuffer: 4 };
const darkLabels = L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_only_labels/{z}/{x}/{y}{r}.png", _lblOpts).addTo(map);
const lightLabels = L.tileLayer("https://{s}.basemaps.cartocdn.com/light_only_labels/{z}/{x}/{y}{r}.png", _lblOpts);

// ---- State -------------------------------------------------------------
let frames = [];
let current = 0;
let velocityLayer = null;
let speedLayer = null;      // heatmap (imageOverlay preview PNG) — dipakai kedua layer
let dataBounds = null;      // L.latLngBounds domain data penuh (untuk overlay)
let playing = false;
let playTimer = null;
let activeLayer = "wind_surface";
let catalog = null;
let windVelByTime = {};     // valid_time -> velocity_json (partikel angin utk SEMUA layer)
let worldLayer = null, provLayer = null;   // layer batas (warna diatur per-tema)
const dataCache = new Map();

// Tema per-layer: angin = gelap (latar peta gelap), hujan = terang (latar putih).
function applyTheme() {
  const light = LAYER_THEME[activeLayer] === "light";
  // Label: teks gelap (light) saat tema terang, teks terang (dark) saat gelap.
  if (light) {
    if (map.hasLayer(darkLabels)) map.removeLayer(darkLabels);
    if (!map.hasLayer(lightLabels)) lightLabels.addTo(map);
  } else {
    if (map.hasLayer(lightLabels)) map.removeLayer(lightLabels);
    if (!map.hasLayer(darkLabels)) darkLabels.addTo(map);
  }
  // Batas: override per-layer bila ada, jika tidak ikut tema (gelap/putih).
  const color = BORDER_COLOR[activeLayer] || (light ? "#1c1b1b" : "#ffffff");
  const opacity = light ? 0.7 : 0.85;
  if (worldLayer) worldLayer.setStyle({ color, opacity });
  if (provLayer) provLayer.setStyle({ color, opacity });
}

// Warna partikel angin sesuai tema: gelap di latar terang, putih di latar gelap.
function particleColor() {
  return LAYER_THEME[activeLayer] === "light" ? "#2b3550" : "#ffffff";
}

const $ = (id) => document.getElementById(id);

function toWIB(iso) {
  // GFS memberi waktu UTC; WIB = UTC+7.
  return new Date(new Date(iso).getTime() + 7 * 3600 * 1000);
}

function fmtValid(iso) {
  // "2026-07-31T00:00:00Z" -> "Jum, 31 Jul 07:00 WIB"
  const opt = { weekday: "short", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "UTC" };
  return toWIB(iso).toLocaleString("id-ID", opt).replace(/\./g, ":") + " WIB";
}

function fmtDay(iso) {
  // Untuk layer harian: tampilkan TANGGAL saja (hari akumulasi, UTC).
  return new Date(iso).toLocaleDateString("id-ID",
    { weekday: "short", day: "2-digit", month: "short", timeZone: "UTC" });
}

// Frame terdekat ke waktu "sekarang" (untuk posisi awal slider, karena window
// bisa memuat masa lalu -24 jam).
function nearestNowIndex() {
  const now = Date.now();
  let best = 0, bestDiff = Infinity;
  frames.forEach((f, i) => {
    const d = Math.abs(new Date(f.valid_time).getTime() - now);
    if (d < bestDiff) { bestDiff = d; best = i; }
  });
  return best;
}

// Label tanggal/jam (WIB) di bawah slider: tanggal ditandai tebal saat harinya
// berganti, sisanya jam saja.
function buildTicks() {
  const wrap = $("tl-ticks");
  if (!wrap || !frames.length) return;
  const n = frames.length;
  let prevDay = null;
  wrap.innerHTML = frames.map((f, i) => {
    const wib = toWIB(f.valid_time);
    const day = wib.getUTCDate();
    const isDay = i === 0 || day !== prevDay;
    prevDay = day;
    const pos = n === 1 ? 0 : (i / (n - 1)) * 100;
    const edge = i === 0 ? " edge-start" : (i === n - 1 ? " edge-end" : "");
    // Mark untuk tiap frame; label TANGGAL saja (di pergantian hari) biar tak berdesakan.
    const lbl = isDay
      ? `<span class="tl-tick-lbl">${wib.toLocaleDateString("id-ID", { day: "numeric", month: "short", timeZone: "UTC" })}</span>`
      : "";
    return `<div class="tl-tick${isDay ? " day" : ""}${edge}" style="left:${pos}%">` +
      `<span class="tl-tick-mark"></span>${lbl}</div>`;
  }).join("");
}

// ---- Batas administrasi -------------------------------------------------
// Indonesia: batas PROVINSI (garis tipis). Negara lain: batas NEGARA saja.
const ADMIN_BASE = "data/";
async function loadAdmin() {
  // Struktur styling mengikuti portofolio: batas negara solid & tegas,
  // batas provinsi tipis putus-putus. Warna putih agar kontras di atas heatmap gelap.
  const styleCountry = { color: "#ffffff", weight: 1.0, opacity: 0.85, fill: false, lineJoin: "round", lineCap: "round", interactive: false };
  const styleProv = { color: "#ffffff", weight: 1.0, opacity: 0.85, fill: false, dashArray: "3 2", lineJoin: "round", interactive: false };
  // Canvas renderer khusus pane admin: fitur di luar frame (+padding) tak digambar,
  // muncul lagi saat di-pan/zoom-out. Jauh lebih mulus daripada SVG.
  const renderer = L.canvas({ pane: "admin", padding: 0.5 });
  try {
    const [world, prov] = await Promise.all([
      fetch(ADMIN_BASE + "world_countries.geojson").then((r) => (r.ok ? r.json() : null)),
      fetch(ADMIN_BASE + "idn_provinces.geojson").then((r) => (r.ok ? r.json() : null)),
    ]);
    if (world) {
      worldLayer = L.geoJSON(world, {
        pane: "admin",
        renderer,
        style: styleCountry,
        // Indonesia digambar oleh layer provinsi → hindari garis pantai ganda kasar.
        filter: (f) => !f.properties || f.properties.name !== "Indonesia",
      }).addTo(map);
    }
    if (prov) provLayer = L.geoJSON(prov, { pane: "admin", renderer, style: styleProv }).addTo(map);
    applyTheme(); // warna batas sesuai layer aktif saat ini
  } catch (e) {
    console.warn("Batas administrasi gagal dimuat:", e);
  }
}

async function loadVelocity(vj) {
  if (dataCache.has(vj)) return dataCache.get(vj);
  const res = await fetch(DATA_BASE + vj);
  if (!res.ok) throw new Error("Gagal memuat " + vj);
  const data = await res.json();
  dataCache.set(vj, data);
  return data;
}

function renderLegend(layerKey) {
  const def = LEGENDS[layerKey];
  const head = $("legend-head"), cells = $("legend-cells");
  if (!def || !head || !cells) return;
  head.textContent = def.head;
  cells.innerHTML = def.cells.map(([label, bg, dark]) =>
    `<div class="legend-cell${dark ? " dark" : ""}" style="background:${bg}">${label}</div>`).join("");
}

function setActiveLayer(layerKey) {
  if (!catalog || !catalog.layers[layerKey] || layerKey === activeLayer) return;
  activeLayer = layerKey;
  frames = catalog.layers[layerKey].frames;
  document.querySelectorAll(".layer-btn[data-layer]").forEach((b) =>
    b.classList.toggle("active", b.dataset.layer === layerKey));
  renderLegend(layerKey);
  applyTheme();
  if (velocityLayer) { map.removeLayer(velocityLayer); velocityLayer = null; } // recreate warna partikel
  buildTicks();
  const slider = $("time-slider");
  if (slider) slider.max = String(frames.length - 1);
  if (current >= frames.length) current = 0;
  showFrame(current);
  // panel titik ikut variabel aktif
  if (pointData && lastPoint && $("point-panel")?.classList.contains("open"))
    renderPoint(pointData, lastPoint.lat, lastPoint.lon);
}

async function showFrame(i) {
  current = (i + frames.length) % frames.length;
  const frame = frames[current];

  // Heatmap (kedua layer punya preview_image): angin = kecepatan, hujan = laju hujan.
  const url = DATA_BASE + frame.preview_image;
  if (!speedLayer) {
    speedLayer = L.imageOverlay(url, dataBounds, { pane: "speed", opacity: 0.92, interactive: false });
    speedLayer.addTo(map);
  } else {
    speedLayer.setUrl(url);
  }
  speedLayer.setOpacity(activeLayer === "wind_surface" ? 0.92 : 1); // scalar opaque; angin semi

  // Partikel angin PUTIH — SELALU ada (angin & hujan), dari medan angin waktu sama.
  const vj = windVelByTime[frame.valid_time];
  if (vj) {
    const data = await loadVelocity(vj);
    if (!velocityLayer) {
      velocityLayer = L.velocityLayer({
        displayValues: false,
        displayOptions: {
          velocityType: "Angin", position: "bottomleft", emptyString: "Tidak ada data",
          angleConvention: "bearingCW", speedUnit: "kt", directionString: "Arah", speedString: "Kecepatan",
        },
        data,
        minVelocity: 0, maxVelocity: 25, velocityScale: 0.012,
        particleAge: 90, particleMultiplier: 1 / 260, lineWidth: 1.1,
        colorScale: [particleColor()], frameRate: 24,
      });
      velocityLayer.addTo(map);
    } else {
      if (!map.hasLayer(velocityLayer)) velocityLayer.addTo(map);
      velocityLayer.setData(data);
    }
  } else if (velocityLayer && map.hasLayer(velocityLayer)) {
    map.removeLayer(velocityLayer);
  }

  const vt = $("valid-time");
  if (vt) vt.textContent = DAILY_LAYERS.has(activeLayer) ? fmtDay(frame.valid_time) : fmtValid(frame.valid_time);
  const ts = $("time-slider"); if (ts) ts.value = String(current);
}

function togglePlay() {
  playing = !playing;
  $("play-icon").textContent = playing ? "pause" : "play_arrow";
  if (playing) {
    playTimer = setInterval(async () => { await showFrame(current + 1); }, 1100);
  } else {
    clearInterval(playTimer);
  }
}

// ================= POINT DETAIL =================
let pointData = null;      // { meta, vars: {name:{arr,scale,offset}} }
let pointLoading = null;
let pointMarker = null;
let lastPoint = null;      // untuk export CSV
const MS_TO_KT = 1.943844;
const DIRS = ["U", "TL", "T", "TG", "S", "BD", "B", "BL"]; // 8 arah dari Utara searah jarum jam

async function loadPointData() {
  if (pointData) return pointData;
  if (pointLoading) return pointLoading;
  pointLoading = (async () => {
    const meta = await fetch(DATA_BASE + "point_meta.json").then((r) => r.json());
    const gz = await fetch(DATA_BASE + "point_data.bin.gz").then((r) => r.arrayBuffer());
    const stream = new Blob([gz]).stream().pipeThrough(new DecompressionStream("gzip"));
    const buf = await new Response(stream).arrayBuffer();
    const vars = {};
    for (const v of meta.vars) {
      const Ctor = v.dtype === "uint8" ? Uint8Array : Int16Array;
      vars[v.var] = { arr: new Ctor(buf, v.byteOffset, v.byteLength / Ctor.BYTES_PER_ELEMENT),
                      scale: v.scale, offset: v.offset };
    }
    pointData = { meta, vars };
    return pointData;
  })();
  return pointLoading;
}

// Bilinear di titik (lat,lon) untuk semua waktu -> array nilai.
function sampleVar(pd, name, lat, lon) {
  const v = pd.vars[name];
  if (!v) return null;
  const { nx, ny, bounds, dx, dy, times } = pd.meta;
  const [w, , , n] = bounds;
  let fx = Math.max(0, Math.min(nx - 1, (lon - w) / dx));
  let fy = Math.max(0, Math.min(ny - 1, (n - lat) / dy)); // baris-0 = utara
  const x0 = Math.floor(fx), x1 = Math.min(x0 + 1, nx - 1), tx = fx - x0;
  const y0 = Math.floor(fy), y1 = Math.min(y0 + 1, ny - 1), ty = fy - y0;
  const plane = nx * ny, out = [];
  for (let t = 0; t < times.length; t++) {
    const b = t * plane;
    const A = v.arr[b + y0 * nx + x0], B = v.arr[b + y0 * nx + x1];
    const C = v.arr[b + y1 * nx + x0], D = v.arr[b + y1 * nx + x1];
    const raw = (1 - tx) * (1 - ty) * A + tx * (1 - ty) * B + (1 - tx) * ty * C + tx * ty * D;
    out.push(raw * v.scale + v.offset);
  }
  return out;
}

function windAt(u, v) {
  const spd = Math.sqrt(u * u + v * v) * MS_TO_KT;
  const deg = (Math.atan2(-u, -v) * 180 / Math.PI + 360) % 360; // arah DATANG
  return { spd, dir: DIRS[Math.round(deg / 45) % 8] };
}

function fmtCoord(lat, lon) {
  return Math.abs(lat).toFixed(2) + "° " + (lat >= 0 ? "LU" : "LS") + " · " +
         Math.abs(lon).toFixed(2) + "° " + (lon >= 0 ? "BT" : "BB");
}
function fmtHour(iso) { const w = toWIB(iso); return w.getUTCDate() + "/" + String(w.getUTCHours()).padStart(2, "0"); }

// Deret-waktu untuk VARIABEL yang sedang dipilih (ikut layer aktif).
function chartSeries(pd, lat, lon) {
  const times = pd.meta.times;
  const num = (v, label, unit, color, type) =>
    ({ label, unit, color, type, times, values: sampleVar(pd, v, lat, lon) });
  switch (activeLayer) {
    case "wind_surface": {
      const u = sampleVar(pd, "u", lat, lon), v = sampleVar(pd, "v", lat, lon);
      return { label: "Kecepatan Angin", unit: "kt", color: "#0029d7", type: "line",
               times, values: u.map((uu, i) => Math.sqrt(uu * uu + v[i] * v[i]) * MS_TO_KT) };
    }
    case "temp_surface": return num("temp", "Suhu", "°C", "#e42320", "line");
    case "humidity_surface": return num("humidity", "Kelembapan", "%", "#1f8a5c", "line");
    case "cloud_surface": return num("cloud", "Tutupan Awan", "%", "#5a6472", "area");
    case "pressure_surface": return num("pressure", "Tekanan", "hPa", "#7a3fb0", "line");
    case "rain_accum_surface": {
      const rain = sampleVar(pd, "rain", lat, lon), days = {};
      times.forEach((t, i) => { const d = t.slice(0, 10); days[d] = (days[d] || 0) + rain[i] * 3; });
      const dts = Object.keys(days).sort();
      return { label: "Akumulasi Hujan Harian", unit: "mm/hari", color: "#2360c8", type: "bar",
               times: dts.map((d) => d + "T00:00:00Z"), values: dts.map((d) => days[d]), daily: true };
    }
    default: return num("rain", "Hujan", "mm/jam", "#2360c8", "bar"); // rain_surface
  }
}

function chartSVG(spec) {
  const { values, color, type } = spec;
  const W = 330, H = 150, pad = 24, n = values.length;
  if (!n) return "";
  const vmin = Math.min(...values), vmax = Math.max(...values);
  const lo = (type === "bar" || type === "area") ? Math.min(0, vmin) : vmin;
  const hi = vmax === lo ? lo + 1 : vmax;
  const x = (i) => pad + (W - 2 * pad) * (n <= 1 ? 0.5 : i / (n - 1));
  const y = (v) => (H - pad) - (H - 2 * pad) * ((v - lo) / (hi - lo));
  let body = "";
  if (type === "bar") {
    const bw = Math.max(3, (W - 2 * pad) / n * 0.6);
    for (let i = 0; i < n; i++)
      body += `<rect x="${(x(i) - bw / 2).toFixed(1)}" y="${y(values[i]).toFixed(1)}" width="${bw.toFixed(1)}" height="${((H - pad) - y(values[i])).toFixed(1)}" fill="${color}" opacity="0.75"/>`;
  } else {
    const pts = values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
    if (type === "area")
      body += `<path d="${pts}L${x(n - 1).toFixed(1)},${H - pad}L${x(0).toFixed(1)},${H - pad}Z" fill="${color}" opacity="0.18"/>`;
    body += `<path d="${pts}" fill="none" stroke="${color}" stroke-width="2.5" stroke-linejoin="round"/>`;
  }
  const fmt = (v) => Math.abs(v) < 10 ? v.toFixed(1) : v.toFixed(0);
  return `<svg class="pt-meteo" viewBox="0 0 ${W} ${H}" width="100%">` +
    `<rect x="1" y="1" width="${W - 2}" height="${H - 2}" fill="#ffffff" stroke="#1c1b1b" stroke-width="2"/>` +
    body +
    `<text x="4" y="14" class="pt-ax">${fmt(hi)}</text>` +
    `<text x="4" y="${H - pad + 12}" class="pt-ax">${fmt(lo)}</text></svg>`;
}

async function openPoint(lat, lon) {
  const pp = $("point-panel");
  if (pp) { pp.classList.add("open"); pp.classList.remove("hidden"); }
  $("pt-reopen")?.classList.remove("show");
  if (pointMarker) pointMarker.setLatLng([lat, lon]);
  else pointMarker = L.marker([lat, lon], {
    icon: L.divIcon({ className: "point-mark", html: '<span class="pm-diamond"></span>', iconSize: [20, 20] }),
    interactive: false, pane: "labels",
  }).addTo(map);
  const c = $("pt-coords"); if (c) c.textContent = fmtCoord(lat, lon);
  const body = $("pt-body"); if (body) body.innerHTML = '<div class="pt-loading">Memuat data titik…</div>';
  try {
    renderPoint(await loadPointData(), lat, lon);
  } catch (e) {
    if (body) body.innerHTML = '<div class="pt-loading">Gagal memuat data titik.</div>';
    console.error(e);
  }
}

function renderPoint(pd, lat, lon) {
  const times = pd.meta.times;
  const u = sampleVar(pd, "u", lat, lon), v = sampleVar(pd, "v", lat, lon);
  const temp = sampleVar(pd, "temp", lat, lon), rain = sampleVar(pd, "rain", lat, lon);
  const rh = sampleVar(pd, "humidity", lat, lon), cloud = sampleVar(pd, "cloud", lat, lon);
  const pres = sampleVar(pd, "pressure", lat, lon);
  const wind = u && v ? u.map((uu, i) => windAt(uu, v[i])) : null;
  lastPoint = { lat, lon, times, temp, rain, wind, rh, cloud, pres };

  let rows = "";
  for (let i = 0; i < times.length; i++) {
    rows += `<tr><td>${fmtHour(times[i])}</td>` +
      `<td>${temp ? temp[i].toFixed(1) : "–"}</td>` +
      `<td>${wind ? Math.round(wind[i].spd) + " " + wind[i].dir : "–"}</td>` +
      `<td>${rain ? rain[i].toFixed(1) : "–"}</td>` +
      `<td>${rh ? Math.round(rh[i]) : "–"}</td>` +
      `<td>${cloud ? Math.round(cloud[i]) : "–"}</td>` +
      `<td>${pres ? Math.round(pres[i]) : "–"}</td></tr>`;
  }
  const spec = chartSeries(pd, lat, lon);
  $("pt-body").innerHTML =
    `<div class="pt-sec">${spec.label.toUpperCase()} <span>${spec.unit}</span></div>${chartSVG(spec)}` +
    `<div class="pt-sec">DATA PER-JAM (WIB)</div>` +
    `<div class="pt-table-wrap"><table class="pt-table"><thead><tr>` +
    `<th>Tgl/Jam</th><th>°C</th><th>Angin</th><th>mm/j</th><th>RH%</th><th>Awan%</th><th>hPa</th>` +
    `</tr></thead><tbody>${rows}</tbody></table></div>`;
}

function exportCSV() {
  if (!lastPoint) return;
  const p = lastPoint;
  let csv = "waktu_wib,suhu_C,angin_kt,arah,hujan_mmjam,kelembapan_pct,awan_pct,tekanan_hpa\n";
  for (let i = 0; i < p.times.length; i++) {
    const w = toWIB(p.times[i]);
    const wib = `${w.getUTCFullYear()}-${String(w.getUTCMonth() + 1).padStart(2, "0")}-${String(w.getUTCDate()).padStart(2, "0")} ${String(w.getUTCHours()).padStart(2, "0")}:00`;
    csv += [wib, p.temp ? p.temp[i].toFixed(1) : "", p.wind ? Math.round(p.wind[i].spd) : "",
            p.wind ? p.wind[i].dir : "", p.rain ? p.rain[i].toFixed(1) : "",
            p.rh ? Math.round(p.rh[i]) : "", p.cloud ? Math.round(p.cloud[i]) : "",
            p.pres ? Math.round(p.pres[i]) : ""].join(",") + "\n";
  }
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  a.download = `titik_${p.lat.toFixed(2)}_${p.lon.toFixed(2)}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

function closePoint() {
  $("point-panel")?.classList.remove("open", "hidden");
  $("pt-reopen")?.classList.remove("show");
  if (pointMarker) { map.removeLayer(pointMarker); pointMarker = null; }
}
function hidePoint() {           // sembunyikan panel, marker & data tetap
  $("point-panel")?.classList.add("hidden");
  $("pt-reopen")?.classList.add("show");
}
function reopenPoint() {
  $("point-panel")?.classList.remove("hidden");
  $("pt-reopen")?.classList.remove("show");
}

// ================= SEARCH KOTA/KABUPATEN =================
let places = null, placesLoading = null;
async function loadPlaces() {
  if (places) return places;
  if (!placesLoading) placesLoading = fetch(ADMIN_BASE + "id_places.json")
    .then((r) => r.json())
    .then((a) => {
      // b = nama tanpa prefix (utk cari "sleman"), f = nama penuh (lowercase)
      places = a.map((p) => ({ n: p.n, lat: p.lat, lon: p.lon,
        b: p.n.replace(/^(Kabupaten|Kota) /, "").toLowerCase(), f: p.n.toLowerCase() }));
      return places;
    });
  return placesLoading;
}
function renderSearch(q) {
  const box = $("search-results"); if (!box) return;
  q = q.trim().toLowerCase();
  if (!q || !places) { box.innerHTML = ""; return; }
  const pre = [], sub = [];
  for (const p of places) {
    if (p.b.startsWith(q) || p.f.startsWith(q)) pre.push(p);       // awalan diprioritaskan
    else if (p.b.includes(q) || p.f.includes(q)) sub.push(p);
  }
  const res = pre.concat(sub).slice(0, 12);
  box.innerHTML = res.length
    ? res.map((p) => `<div class="search-item" data-lat="${p.lat}" data-lon="${p.lon}">${p.n}</div>`).join("")
    : '<div class="search-empty">Tak ada hasil</div>';
}
function pickPlace(lat, lon) {
  map.setView([lat, lon], 8, { animate: true });
  openPoint(lat, lon);
  $("search-box")?.classList.remove("open");
}

// ---- Init --------------------------------------------------------------
async function init() {
  // Diagnosa dini penyebab umum gagal-muat
  if (location.protocol === "file:") {
    $("loading").innerHTML =
      "⚠️ Halaman dibuka via <b>file://</b> — browser memblokir pemuatan data.<br><br>" +
      "Buka lewat alamat server:<br><b>http://127.0.0.1:8000/frontend/index.html</b>";
    return;
  }
  if (typeof L === "undefined" || typeof L.velocityLayer !== "function") {
    $("loading").textContent =
      "⚠️ Library peta gagal dimuat (cek koneksi internet ke unpkg.com / CDN diblokir).";
    return;
  }
  try {
    const catRes = await fetch(DATA_BASE + "catalog.json");
    if (!catRes.ok) throw new Error(`catalog.json HTTP ${catRes.status} (${DATA_BASE}catalog.json)`);
    const cat = await catRes.json();
    catalog = cat;
    const avail = Object.keys(cat.layers || {});
    if (!avail.length) throw new Error("catalog.json tidak punya layer");
    activeLayer = cat.layers["wind_surface"] ? "wind_surface" : avail[0];
    frames = cat.layers[activeLayer].frames;

    // Domain data penuh (untuk imageOverlay heatmap).
    const [dw, ds, de, dn] = cat.region.bounds;
    dataBounds = L.latLngBounds([ds, dw], [dn, de]);

    // Wire tombol layer: aktif kalau datanya ada di catalog, disabled kalau belum.
    document.querySelectorAll(".layer-btn[data-layer]").forEach((btn) => {
      const key = btn.dataset.layer;
      if (cat.layers[key]) {
        btn.classList.remove("disabled");
        btn.addEventListener("click", () => { if (playing) togglePlay(); setActiveLayer(key); });
      } else {
        btn.classList.add("disabled");
      }
      btn.classList.toggle("active", key === activeLayer);
    });
    renderLegend(activeLayer);

    // Medan angin per-waktu — partikel dipakai di SEMUA layer (termasuk hujan).
    const windL = cat.layers["wind_surface"];
    if (windL) windL.frames.forEach((f) => { if (f.velocity_json) windVelByTime[f.valid_time] = f.velocity_json; });

    // Bingkai tampilan = kotak inti (VIEW_CORE) yang diperlebar pada sumbu yang
    // perlu hingga RASIONYA sama dengan jendela desktop. Efeknya: seluruh wilayah
    // inti (India–Pasifik Barat, Cina Selatan–tengah Australia) mengisi layar
    // penuh, tanpa bar kosong dan tanpa terpotong; tepi domain data (yang lebih
    // luas) tak pernah terlihat. Dihitung ulang tiap kali jendela di-resize.
    function frameRegion() {
      const crs = map.options.crs;
      const sw = crs.project(VIEW_CORE.getSouthWest());
      const ne = crs.project(VIEW_CORE.getNorthEast());
      const cx = (sw.x + ne.x) / 2, cy = (sw.y + ne.y) / 2; // pusat (proyeksi Mercator)
      let halfW = Math.abs(ne.x - sw.x) / 2;
      let halfH = Math.abs(ne.y - sw.y) / 2;
      const size = map.getSize();
      const screenRatio = size.x / size.y;
      if (screenRatio > halfW / halfH) halfW = halfH * screenRatio; // layar lebih lebar → perlebar bujur
      else halfH = halfW / screenRatio;                             // layar lebih tinggi → pertinggi lintang
      const box = L.latLngBounds(
        crs.unproject(L.point(cx - halfW, cy - halfH)),
        crs.unproject(L.point(cx + halfW, cy + halfH))
      );
      // Kunci HANYA zoom-out pada tampilan awal (bingkai inti mengisi layar);
      // zoom-in dan geser tetap bebas di dalam domain data.
      const z = map.getBoundsZoom(box);      // zoom saat bingkai inti mengisi layar
      map.setMinZoom(z);                     // tak bisa zoom-out lebih jauh dari ini
      map.setMaxBounds(dataBounds);          // pan dibatasi domain data, bukan bingkai
      map.setView(crs.unproject(L.point(cx, cy)), z, { animate: false });
    }
    frameRegion();
    map.on("resize", frameRegion);
    loadAdmin(); // batas negara + provinsi Indonesia (non-blocking)

    // Wiring UI dibuat tahan-null: elemen dekoratif yang hilang (mis. cache
    // index.html lama) tak boleh menggagalkan pemuatan peta & data.
    const slider = $("time-slider");
    if (slider) {
      slider.max = String(frames.length - 1);
      slider.addEventListener("input", (ev) => {
        if (playing) togglePlay();
        showFrame(parseInt(ev.target.value, 10));
      });
    }
    $("play-btn")?.addEventListener("click", togglePlay);
    buildTicks(); // label tanggal/jam WIB di bawah slider

    // Tombol zoom neubrutalist → kontrol peta
    $("zoom-in")?.addEventListener("click", () => map.zoomIn());
    $("zoom-out")?.addEventListener("click", () => map.zoomOut());

    // Point detail: klik peta → panel titik
    map.on("click", (e) => openPoint(e.latlng.lat, e.latlng.lng));
    $("pt-close")?.addEventListener("click", closePoint);
    $("pt-export")?.addEventListener("click", exportCSV);
    $("pt-hide")?.addEventListener("click", hidePoint);
    $("pt-reopen")?.addEventListener("click", reopenPoint);

    // Pencarian kota/kabupaten
    const sbox = $("search-box"), sin = $("search-input");
    $("search-btn")?.addEventListener("click", () => {
      if (sbox.classList.toggle("open")) { loadPlaces(); sin.focus(); }
    });
    sin?.addEventListener("input", (e) => renderSearch(e.target.value));
    sin?.addEventListener("keydown", (e) => {
      if (e.key === "Escape") sbox.classList.remove("open");
      if (e.key === "Enter") {
        const f = $("search-results").querySelector(".search-item");
        if (f) pickPlace(parseFloat(f.dataset.lat), parseFloat(f.dataset.lon));
      }
    });
    $("search-results")?.addEventListener("click", (e) => {
      const it = e.target.closest(".search-item");
      if (it) pickPlace(parseFloat(it.dataset.lat), parseFloat(it.dataset.lon));
    });
    // Initial time (dekoratif) diisi dari run model
    const io = $("init-opt");
    if (io) io.textContent = cat.run_time;

    const runEl = $("run-info");
    if (runEl) runEl.title = "Run model: " + cat.run_time;
    current = nearestNowIndex();       // mulai di frame terdekat "sekarang"
    await showFrame(current);
    $("loading").style.display = "none";
  } catch (err) {
    $("loading").textContent = "Gagal memuat data: " + err.message;
    console.error(err);
  }
}

init();
