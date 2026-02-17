// Theme: browser/system preference is primary, manual toggle overrides until page reload
function getSystemTheme() {
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    updateMapTiles();
}

let lifeList = [];
let allObservations = []; // {name, sci, state, county, countryCode}
let regionNames = {}; // code -> display name, loaded from API
let map = null;
let tileLayer = null;

applyTheme(getSystemTheme());

// Listen for system/browser preference changes (e.g. time-based auto switch)
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    applyTheme(getSystemTheme());
});

// Manual toggle overrides system preference
document.getElementById("theme-toggle").addEventListener("click", () => {
    const isDark = document.documentElement.getAttribute("data-theme") === "dark";
    applyTheme(isDark ? "light" : "dark");
});

const tileSets = {
    light: {
        url: "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        attribution: '&copy; OpenStreetMap contributors',
    },
    dark: {
        url: "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
        attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
    },
};

function updateMapTiles() {
    if (!map || !tileLayer) return;
    const theme = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
    tileLayer.setUrl(tileSets[theme].url);
}

// Set default dates to today + 7 days
const today = new Date();
const nextWeek = new Date(today);
nextWeek.setDate(today.getDate() + 7);
document.getElementById("start-date").value = today.toISOString().split("T")[0];
document.getElementById("end-date").value = nextWeek.toISOString().split("T")[0];

// Load reference data
fetch("/api/region-names")
    .then(r => r.json())
    .then(data => { regionNames = data; });

let selectedCounties = new Set();

fetch("/api/counties")
    .then(r => r.json())
    .then(counties => {
        const dropdown = document.getElementById("county-dropdown");
        counties.forEach(c => {
            const item = document.createElement("div");
            item.className = "multi-select-item";
            item.dataset.value = c;
            item.innerHTML = `<span>${c}</span><span class="check">✓</span>`;
            item.addEventListener("click", (e) => {
                e.stopPropagation();
                if (selectedCounties.has(c)) {
                    selectedCounties.delete(c);
                    item.classList.remove("selected");
                } else {
                    selectedCounties.add(c);
                    item.classList.add("selected");
                }
                updateCountyDisplay();
            });
            dropdown.appendChild(item);
        });
    });

document.getElementById("county-display").addEventListener("click", () => {
    document.getElementById("county-dropdown").classList.toggle("open");
});

document.addEventListener("click", (e) => {
    const select = document.getElementById("county-select");
    if (!select.contains(e.target)) {
        document.getElementById("county-dropdown").classList.remove("open");
    }
});

function updateCountyDisplay() {
    const display = document.getElementById("county-display");
    if (selectedCounties.size === 0) {
        display.textContent = "All counties";
    } else {
        display.textContent = Array.from(selectedCounties).join(", ");
    }
}

// Parse CSV client-side
document.getElementById("csv-input").addEventListener("change", e => {
    const file = e.target.files[0];
    if (!file) return;

    const status = document.getElementById("file-status");
    status.textContent = "Processing eBird data...";
    status.className = "file-status";

    const reader = new FileReader();
    reader.onload = ev => {
        const text = ev.target.result;
        allObservations = parseObservations(text);
        populateCountryDropdown();
        resetScopeDropdowns("state");
        resetScopeDropdowns("county");
        updateLifeList();
        document.getElementById("optimize-btn").disabled = false;
    };
    reader.readAsText(file);
});

function parseObservations(csvText) {
    const lines = csvText.split("\n");
    if (lines.length < 2) return [];

    const header = parseCSVLine(lines[0]);
    const nameIdx = header.indexOf("Common Name");
    const sciIdx = header.indexOf("Scientific Name");
    const countIdx = header.indexOf("Count");
    const stateIdx = header.indexOf("State/Province");
    const countyIdx = header.indexOf("County");
    if (nameIdx === -1) return [];

    const observations = [];
    for (let i = 1; i < lines.length; i++) {
        if (!lines[i].trim()) continue;
        const cols = parseCSVLine(lines[i]);
        let name = cols[nameIdx] || "";
        const sci = cols[sciIdx] || "";
        const count = cols[countIdx] || "";

        // Skip zero-count entries (species reported as absent)
        if (count === "0") continue;

        // Strip subspecies parenthetical
        const parenIdx = name.indexOf(" (");
        if (parenIdx !== -1) name = name.substring(0, parenIdx);

        // Skip hybrids and sp. groups
        if (name.includes("/") || name.includes(" sp.") || sci.includes(" x ")) continue;

        const stateCode = cols[stateIdx] || "";
        const countryCode = stateCode.includes("-") ? stateCode.split("-")[0] : "";

        if (name) {
            observations.push({
                name,
                sci,
                countryCode,
                state: stateCode,
                county: cols[countyIdx] || "",
            });
        }
    }
    return observations;
}

function regionName(code) {
    return regionNames[code] || code;
}

function resetScopeDropdowns(level) {
    if (level === "state" || level === "country") {
        const stateSel = document.getElementById("scope-state");
        stateSel.innerHTML = '<option value="">All states/provinces</option>';
        stateSel.disabled = true;
    }
    if (level === "state" || level === "country" || level === "county") {
        const countySel = document.getElementById("scope-county");
        countySel.innerHTML = '<option value="">All counties</option>';
        countySel.disabled = true;
    }
}

function populateCountryDropdown() {
    const sel = document.getElementById("scope-country");
    sel.innerHTML = '<option value="">World (all countries)</option>';

    const countries = new Set();
    for (const obs of allObservations) {
        if (obs.countryCode) countries.add(obs.countryCode);
    }

    for (const code of [...countries].sort((a, b) => regionName(a).localeCompare(regionName(b)))) {
        const opt = document.createElement("option");
        opt.value = code;
        opt.textContent = regionName(code);
        sel.appendChild(opt);
    }
    sel.disabled = false;
}

document.getElementById("scope-country").addEventListener("change", () => {
    resetScopeDropdowns("state");
    const country = document.getElementById("scope-country").value;

    if (country) {
        const stateSel = document.getElementById("scope-state");
        const states = new Set();
        for (const obs of allObservations) {
            if (obs.countryCode === country && obs.state) states.add(obs.state);
        }
        for (const code of [...states].sort((a, b) => regionName(a).localeCompare(regionName(b)))) {
            const opt = document.createElement("option");
            opt.value = code;
            opt.textContent = regionName(code);
            stateSel.appendChild(opt);
        }
        stateSel.disabled = states.size === 0;
    }

    updateLifeList();
});

document.getElementById("scope-state").addEventListener("change", () => {
    resetScopeDropdowns("county");
    const state = document.getElementById("scope-state").value;

    if (state) {
        const countySel = document.getElementById("scope-county");
        const counties = new Set();
        for (const obs of allObservations) {
            if (obs.state === state && obs.county) counties.add(obs.county);
        }
        for (const c of [...counties].sort()) {
            const opt = document.createElement("option");
            opt.value = c;
            opt.textContent = c;
            countySel.appendChild(opt);
        }
        countySel.disabled = counties.size === 0;
    }

    updateLifeList();
});

document.getElementById("scope-county").addEventListener("change", () => {
    updateLifeList();
});

function updateLifeList() {
    const country = document.getElementById("scope-country").value;
    const state = document.getElementById("scope-state").value;
    const county = document.getElementById("scope-county").value;

    let filtered = allObservations;
    if (county) {
        filtered = filtered.filter(o => o.county === county && o.state === state);
    } else if (state) {
        filtered = filtered.filter(o => o.state === state);
    } else if (country) {
        filtered = filtered.filter(o => o.countryCode === country);
    }

    const seen = new Set();
    for (const obs of filtered) seen.add(obs.name);
    lifeList = Array.from(seen);

    // Build scope label
    let label = "World";
    if (county) label = county;
    else if (state) label = regionName(state);
    else if (country) label = regionName(country);

    console.log(`Life list [${label}]: ${lifeList.length} species`, {country, state, county, observations: filtered.length, species: lifeList});

    const status = document.getElementById("file-status");
    status.textContent = `${lifeList.length} species on your ${label} life list`;
    status.className = "file-status loaded";
}

function parseCSVLine(line) {
    const result = [];
    let current = "";
    let inQuotes = false;
    for (let i = 0; i < line.length; i++) {
        const ch = line[i];
        if (inQuotes) {
            if (ch === '"' && line[i + 1] === '"') {
                current += '"';
                i++;
            } else if (ch === '"') {
                inQuotes = false;
            } else {
                current += ch;
            }
        } else {
            if (ch === '"') {
                inQuotes = true;
            } else if (ch === ",") {
                result.push(current.trim());
                current = "";
            } else {
                current += ch;
            }
        }
    }
    result.push(current.trim());
    return result;
}

// Run optimization
document.getElementById("optimize-btn").addEventListener("click", async () => {
    const btn = document.getElementById("optimize-btn");
    btn.disabled = true;
    btn.textContent = "Optimizing...";

    const counties = Array.from(selectedCounties);
    const body = {
        life_list: lifeList,
        start_date: document.getElementById("start-date").value,
        end_date: document.getElementById("end-date").value,
        k: parseInt(document.getElementById("k-input").value),
        counties: counties.length > 0 ? counties : null,
    };

    try {
        const resp = await fetch("/api/optimize", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || "Optimization failed");
        }
        const data = await resp.json();
        renderResults(data);
    } catch (err) {
        document.getElementById("results").innerHTML =
            `<div class="empty-state"><p>Error: ${err.message}</p></div>`;
    } finally {
        btn.disabled = false;
        btn.textContent = "Optimize";
    }
});

function renderResults(data) {
    const el = document.getElementById("results");

    if (!data.hotspots || data.hotspots.length === 0) {
        el.innerHTML = `<div class="empty-state"><p>No potential lifers found for this area and date range.</p></div>`;
        return;
    }

    let html = `
        <div class="metrics">
            <div class="metric-card">
                <div class="value">${data.total_expected_lifers}</div>
                <div class="label">Expected lifers</div>
            </div>
            <div class="metric-card">
                <div class="value">${data.num_candidate_hotspots}</div>
                <div class="label">Hotspots evaluated</div>
            </div>
            <div class="metric-card">
                <div class="value">${data.num_potential_lifers}</div>
                <div class="label">Potential lifer species</div>
            </div>
        </div>
        <div id="map"></div>
        <div class="section-title">Recommended Hotspots</div>
    `;

    data.hotspots.forEach(h => {
        const speciesRows = h.target_species.slice(0, 25).map(sp => `
            <tr>
                <td>${sp.common_name}</td>
                <td>
                    <span class="prob-bar" style="width: ${sp.probability * 100}px"></span>
                    ${(sp.probability * 100).toFixed(1)}%
                </td>
                <td>${sp.recently_observed ? "\u2705" : "\u274C"}</td>
            </tr>
        `).join("");

        html += `
            <div class="hotspot-card">
                <div class="hotspot-header" onclick="this.nextElementSibling.classList.toggle('open')">
                    <span class="rank">#${h.rank}</span>
                    <span class="name">${h.locality}</span>
                    <span class="gain">+${h.marginal_gain.toFixed(2)} lifers</span>
                </div>
                <div class="hotspot-body">
                    <div class="hotspot-meta">
                        ${h.county} &middot;
                        ${h.latitude.toFixed(4)}, ${h.longitude.toFixed(4)} &middot;
                        <a href="https://ebird.org/hotspot/L${h.locality_id}" target="_blank" rel="noopener">View on eBird</a> &middot;
                        <a href="https://www.google.com/maps/search/${encodeURIComponent(h.locality)}/@${h.latitude},${h.longitude},15z" target="_blank" rel="noopener">View on Google Maps</a>
                    </div>
                    <table>
                        <thead><tr><th>Species</th><th>Detection Probability</th><th>Observed in Last 30 Days?</th></tr></thead>
                        <tbody>${speciesRows}</tbody>
                    </table>
                </div>
            </div>
        `;
    });

    // Combined species table
    if (data.species_combined_probs && data.species_combined_probs.length > 0) {
        const combinedRows = data.species_combined_probs.map(sp => `
            <tr>
                <td>${sp.common_name}</td>
                <td>
                    <span class="prob-bar" style="width: ${sp.probability * 100}px"></span>
                    ${(sp.probability * 100).toFixed(1)}%
                </td>
            </tr>
        `).join("");

        html += `
            <div class="section-title" style="margin-top: 1.5rem;">All Potential Lifers (Combined Probability)</div>
            <div class="hotspot-card">
                <div style="padding: 1rem 1.25rem;">
                    <table>
                        <thead><tr><th>Species</th><th>Combined Probability</th></tr></thead>
                        <tbody>${combinedRows}</tbody>
                    </table>
                </div>
            </div>
        `;
    }

    el.innerHTML = html;

    // Initialize map
    if (map) { map.remove(); map = null; }

    map = L.map("map");
    const theme = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
    const tiles = tileSets[theme];
    tileLayer = L.tileLayer(tiles.url, { attribution: tiles.attribution }).addTo(map);

    const bounds = [];
    data.hotspots.forEach(h => {
        const icon = L.divIcon({
            className: "map-marker",
            html: `<div class="map-marker-inner">${h.rank}</div>`,
            iconSize: [28, 28],
            iconAnchor: [14, 14],
            popupAnchor: [0, -16],
        });
        const marker = L.marker([h.latitude, h.longitude], { icon }).addTo(map);
        marker.bindPopup(
            `<b>#${h.rank}: ${h.locality}</b><br>${h.county}<br>+${h.marginal_gain.toFixed(2)} expected lifers`
        );
        bounds.push([h.latitude, h.longitude]);
    });

    if (bounds.length > 0) {
        map.fitBounds(bounds, { padding: [30, 30] });
    }
}
