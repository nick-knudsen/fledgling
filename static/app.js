let lifeList = [];
let map = null;

// Set default dates to today + 7 days
const today = new Date();
const nextWeek = new Date(today);
nextWeek.setDate(today.getDate() + 7);
document.getElementById("start-date").value = today.toISOString().split("T")[0];
document.getElementById("end-date").value = nextWeek.toISOString().split("T")[0];

// Load counties
fetch("/api/counties")
    .then(r => r.json())
    .then(counties => {
        const sel = document.getElementById("county-select");
        counties.forEach(c => {
            const opt = document.createElement("option");
            opt.value = c;
            opt.textContent = c;
            sel.appendChild(opt);
        });
    });

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
        lifeList = parseLifeList(text);
        status.textContent = `${lifeList.length} species on your life list`;
        status.className = "file-status loaded";
        document.getElementById("optimize-btn").disabled = false;
    };
    reader.readAsText(file);
});

function parseLifeList(csvText) {
    const lines = csvText.split("\n");
    if (lines.length < 2) return [];

    const header = parseCSVLine(lines[0]);
    const nameIdx = header.indexOf("Common Name");
    const sciIdx = header.indexOf("Scientific Name");
    if (nameIdx === -1) return [];

    const seen = new Set();
    for (let i = 1; i < lines.length; i++) {
        if (!lines[i].trim()) continue;
        const cols = parseCSVLine(lines[i]);
        let name = cols[nameIdx] || "";
        const sci = cols[sciIdx] || "";

        // Strip subspecies parenthetical
        const parenIdx = name.indexOf(" (");
        if (parenIdx !== -1) name = name.substring(0, parenIdx);

        // Skip hybrids and sp. groups
        if (name.includes("/") || name.includes(" sp.") || sci.includes(" x ")) continue;

        if (name) seen.add(name);
    }
    return Array.from(seen);
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

    const county = document.getElementById("county-select").value || null;
    const body = {
        life_list: lifeList,
        start_date: document.getElementById("start-date").value,
        end_date: document.getElementById("end-date").value,
        k: parseInt(document.getElementById("k-input").value),
        county: county,
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
                        Cumulative expected: ${h.cumulative_expected.toFixed(2)} lifers
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
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: '&copy; OpenStreetMap contributors',
    }).addTo(map);

    const bounds = [];
    data.hotspots.forEach(h => {
        const marker = L.marker([h.latitude, h.longitude]).addTo(map);
        marker.bindPopup(
            `<b>#${h.rank}: ${h.locality}</b><br>${h.county}<br>+${h.marginal_gain.toFixed(2)} expected lifers`
        );
        bounds.push([h.latitude, h.longitude]);
    });

    if (bounds.length > 0) {
        map.fitBounds(bounds, { padding: [30, 30] });
    }
}
