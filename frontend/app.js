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
    cells: [["0.5", "#d6e6f6", 0], ["1", "#accdef", 0], ["3", "#6faae4", 0], ["8", "#357fd4", 1],
            ["20", "#1854ba", 1], ["50", "#0d338f", 1], ["100+", "#08205e", 1]],
  },
};

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
  const rain = activeLayer === "rain_surface";
  // Label: terang saat hujan, gelap saat angin.
  if (rain) {
    if (map.hasLayer(darkLabels)) map.removeLayer(darkLabels);
    if (!map.hasLayer(lightLabels)) lightLabels.addTo(map);
  } else {
    if (map.hasLayer(lightLabels)) map.removeLayer(lightLabels);
    if (!map.hasLayer(darkLabels)) darkLabels.addTo(map);
  }
  // Batas: gelap saat hujan (kontras di putih), putih saat angin.
  const color = rain ? "#1c1b1b" : "#ffffff";
  const opacity = rain ? 0.7 : 0.85;
  if (worldLayer) worldLayer.setStyle({ color, opacity });
  if (provLayer) provLayer.setStyle({ color, opacity });
}

// Warna partikel angin sesuai tema: gelap di atas hujan-putih, putih di atas angin-gelap.
function particleColor() {
  return activeLayer === "rain_surface" ? "#2b3550" : "#ffffff";
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
  speedLayer.setOpacity(activeLayer === "rain_surface" ? 1 : 0.92); // hujan opaque (latar putih)

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

  const vt = $("valid-time"); if (vt) vt.textContent = fmtValid(frame.valid_time);
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
