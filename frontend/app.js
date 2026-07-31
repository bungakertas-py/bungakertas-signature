/* Peta Cuaca — frontend Fase 1 (angin permukaan, GFS)
 * Membaca catalog.json + JSON velocity dari pipeline backend,
 * menampilkan animasi partikel angin ala BMKG Signature.
 */
const DATA_BASE = "../backend/data/output/";

// Skala warna knots (selaras dengan legend & pipeline)
const KNOTS_COLORS = [
  "#003050", "#2b83ba", "#5aa8cf", "#abdda4", "#66bd63",
  "#d9ef8b", "#fee08b", "#fdae61", "#f46d43", "#d73027", "#a50026", "#7a0077",
];

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
L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_only_labels/{z}/{x}/{y}{r}.png", {
  subdomains: "abcd", pane: "labels",
  updateWhenZooming: false, keepBuffer: 4,
}).addTo(map);

// ---- State -------------------------------------------------------------
let frames = [];
let current = 0;
let velocityLayer = null;
let speedLayer = null;      // heatmap kecepatan (imageOverlay preview PNG)
let dataBounds = null;      // L.latLngBounds domain data penuh (untuk overlay)
let playing = false;
let playTimer = null;
const dataCache = new Map();

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
      L.geoJSON(world, {
        pane: "admin",
        renderer,
        style: styleCountry,
        // Indonesia digambar oleh layer provinsi → hindari garis pantai ganda kasar.
        filter: (f) => !f.properties || f.properties.name !== "Indonesia",
      }).addTo(map);
    }
    if (prov) L.geoJSON(prov, { pane: "admin", renderer, style: styleProv }).addTo(map);
  } catch (e) {
    console.warn("Batas administrasi gagal dimuat:", e);
  }
}

async function loadFrameData(frame) {
  if (dataCache.has(frame.velocity_json)) return dataCache.get(frame.velocity_json);
  const res = await fetch(DATA_BASE + frame.velocity_json);
  if (!res.ok) throw new Error("Gagal memuat " + frame.velocity_json);
  const data = await res.json();
  dataCache.set(frame.velocity_json, data);
  return data;
}

async function showFrame(i) {
  current = (i + frames.length) % frames.length;
  const frame = frames[current];
  const data = await loadFrameData(frame);

  // --- Kontur warna kecepatan (heatmap ala BMKG) di bawah partikel ----------
  const speedUrl = DATA_BASE + frame.preview_image;
  if (!speedLayer) {
    speedLayer = L.imageOverlay(speedUrl, dataBounds, {
      pane: "speed",
      opacity: 0.92,
      interactive: false,
    });
    speedLayer.addTo(map);
  } else {
    speedLayer.setUrl(speedUrl);
  }

  // --- Partikel angin: garis PUTIH di atas heatmap --------------------------
  if (!velocityLayer) {
    velocityLayer = L.velocityLayer({
      displayValues: false,
      displayOptions: {
        velocityType: "Angin",
        position: "bottomleft",
        emptyString: "Tidak ada data",
        angleConvention: "bearingCW",
        speedUnit: "kt",
        directionString: "Arah",
        speedString: "Kecepatan",
      },
      data,
      minVelocity: 0,
      maxVelocity: 25,          // m/s (~48 kt) rentang skala warna
      velocityScale: 0.012,
      particleAge: 90,
      particleMultiplier: 1 / 260,
      lineWidth: 1.1,
      colorScale: ["#ffffff"], // vektor angin putih (warna kecepatan di heatmap)
      frameRate: 24,
    });
    velocityLayer.addTo(map);
  } else {
    velocityLayer.setData(data);
  }

  const vt = $("valid-time"); if (vt) vt.textContent = fmtValid(frame.valid_time);
  const ri = $("run-info"); if (ri) ri.textContent = `+${frame.forecast_step_hours} jam · maks ${frame.speed_knots_max} kt`;
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
    const wind = cat.layers["wind_surface"];
    if (!wind) throw new Error("layer 'wind_surface' tidak ada di catalog.json");
    frames = wind.frames;

    // Domain data penuh (untuk imageOverlay heatmap kecepatan).
    const [dw, ds, de, dn] = cat.region.bounds;
    dataBounds = L.latLngBounds([ds, dw], [dn, de]);

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
    await showFrame(0);
    $("loading").style.display = "none";
  } catch (err) {
    $("loading").textContent = "Gagal memuat data: " + err.message;
    console.error(err);
  }
}

init();
