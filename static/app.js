// Theme: browser/system preference is primary, manual toggle overrides until page reload
function getSystemTheme() {
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    updateMapTiles();
}

let lifeList = [];
let allObservations = []; // {name, sci, state, county, countryCode, date}
let listType = "life"; // "life" or "year"
let regionNames = {}; // code -> display name, loaded from API
let map = null;
let tileLayer = null;
let markers = {}; // rank -> L.marker
let itinerary = []; // ordered array of hotspot data objects
let resultsMode = "explore"; // "explore" or "plan"
let lastResultsData = null;
let isDragging = false;

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

// Target species mode toggle (search / lifelist)
let targetMode = "search"; // "search" or "lifelist"

document.querySelectorAll(".target-mode-btn").forEach(btn => {
    btn.addEventListener("click", () => {
        document.querySelectorAll(".target-mode-btn").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        targetMode = btn.dataset.mode;
        document.getElementById("target-search-inputs").style.display = targetMode === "search" ? "" : "none";
        document.getElementById("target-lifelist-inputs").style.display = targetMode === "lifelist" ? "" : "none";
    });
});

// List type toggle (life / year)
document.querySelectorAll(".list-type-btn").forEach(btn => {
    btn.addEventListener("click", () => {
        document.querySelectorAll(".list-type-btn").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        listType = btn.dataset.type;
        if (allObservations.length > 0) updateLifeList();
    });
});

// Month/day date selects
const monthNames = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const daysInMonth = [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];

function populateDateSelects(prefix, month, day) {
    const monthSel = document.getElementById(`${prefix}-month`);
    const daySel = document.getElementById(`${prefix}-day`);

    monthNames.forEach((name, i) => {
        const opt = document.createElement("option");
        opt.value = i + 1;
        opt.textContent = name;
        monthSel.appendChild(opt);
    });
    monthSel.value = month;

    function updateDays() {
        const m = parseInt(monthSel.value);
        const maxDay = daysInMonth[m - 1];
        const curDay = parseInt(daySel.value) || day;
        daySel.innerHTML = "";
        for (let d = 1; d <= maxDay; d++) {
            const opt = document.createElement("option");
            opt.value = d;
            opt.textContent = d;
            daySel.appendChild(opt);
        }
        daySel.value = Math.min(curDay, maxDay);
    }

    monthSel.addEventListener("change", updateDays);
    updateDays();
}

const today = new Date();
const nextWeek = new Date(today);
nextWeek.setDate(today.getDate() + 7);
populateDateSelects("start", today.getMonth() + 1, today.getDate());
populateDateSelects("end", nextWeek.getMonth() + 1, nextWeek.getDate());

function getDateValue(prefix) {
    const m = document.getElementById(`${prefix}-month`).value;
    const d = document.getElementById(`${prefix}-day`).value;
    // Use 2024 (leap year) so Feb 29 is valid
    return `2024-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}

// Load reference data
fetch("/api/region-names")
    .then(r => r.json())
    .then(data => { regionNames = data; });

let searchMode = "region"; // "region" or "driving"
let centerLat = null;
let centerLon = null;
let locationName = null;
let nominatimTimeout = null;
let activeLocationIndex = -1;

let searchAreas = {}; // country -> { state -> [counties] }
let selectedCountries = new Set();
let selectedStates = new Set();
let selectedCounties = new Set();

fetch("/api/search-areas")
    .then(r => r.json())
    .then(data => {
        searchAreas = data;
        populateMultiSelect("country", Object.keys(data).sort(), selectedCountries, onCountryChange);
        refreshStateDropdown();
        refreshCountyDropdown();
    });

function onCountryChange() {
    selectedStates.clear();
    selectedCounties.clear();
    refreshStateDropdown();
    refreshCountyDropdown();
}

function onStateChange() {
    selectedCounties.clear();
    refreshCountyDropdown();
}

function getVisibleStates() {
    const countries = selectedCountries.size > 0 ? [...selectedCountries] : Object.keys(searchAreas);
    const states = [];
    for (const c of countries) {
        if (searchAreas[c]) states.push(...Object.keys(searchAreas[c]));
    }
    return states.sort();
}

function getVisibleCounties() {
    const countries = selectedCountries.size > 0 ? [...selectedCountries] : Object.keys(searchAreas);
    const states = selectedStates.size > 0 ? [...selectedStates] : null;
    const counties = [];
    for (const c of countries) {
        if (!searchAreas[c]) continue;
        for (const [st, cts] of Object.entries(searchAreas[c])) {
            if (states && !states.includes(st)) continue;
            for (const county of cts) {
                counties.push(`${st}|${county}`);
            }
        }
    }
    return counties.sort((a, b) => a.split("|")[1].localeCompare(b.split("|")[1]));
}

function refreshStateDropdown() {
    populateMultiSelect("state", getVisibleStates(), selectedStates, onStateChange);
    updateMultiSelectDisplay("state", selectedStates, "All states/provinces");
}

function refreshCountyDropdown() {
    populateMultiSelect("county", getVisibleCounties(), selectedCounties, () => {
        updateMultiSelectDisplay("county", selectedCounties, "All counties");
    });
    updateMultiSelectDisplay("county", selectedCounties, "All counties");
}

let multiSelectActive = {}; // id -> boolean

function countyDisplayName(val, allItems) {
    const [state, county] = val.split("|");
    const dupes = allItems.filter(v => v.split("|")[1] === county);
    return dupes.length > 1 ? `${county} (${regionName(state)})` : county;
}

function populateMultiSelect(id, items, selectedSet, onChange) {
    if (!Array.isArray(items)) items = [];
    multiSelectActive[id] = false;
    const dropdown = document.getElementById(`${id}-dropdown`);
    dropdown.innerHTML = "";

    const defaultText = id === "county" ? "All counties" : (id === "state" ? "All states/provinces" : "World (all countries)");

    const actions = document.createElement("div");
    actions.className = "multi-select-actions";
    const multiBtn = document.createElement("button");
    multiBtn.textContent = "Select Multiple";
    multiBtn.dataset.action = "multi";
    const allNoneBtn = document.createElement("button");
    allNoneBtn.textContent = "Select All / None";
    allNoneBtn.dataset.action = "allnone";
    actions.appendChild(multiBtn);
    actions.appendChild(allNoneBtn);
    dropdown.appendChild(actions);

    multiBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        multiSelectActive[id] = !multiSelectActive[id];
        multiBtn.classList.toggle("active", multiSelectActive[id]);
    });

    allNoneBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        if (!multiSelectActive[id]) {
            multiSelectActive[id] = true;
            multiBtn.classList.add("active");
        }
        if (selectedSet.size > 0) {
            selectedSet.clear();
            dropdown.querySelectorAll(".multi-select-item").forEach(item => {
                item.classList.remove("selected");
            });
        } else {
            dropdown.querySelectorAll(".multi-select-item").forEach(item => {
                selectedSet.add(item.dataset.value);
                item.classList.add("selected");
            });
        }
        updateMultiSelectDisplay(id, selectedSet, defaultText);
        onChange();
    });

    items.forEach(val => {
        const item = document.createElement("div");
        item.className = "multi-select-item";
        if (selectedSet.has(val)) item.classList.add("selected");
        item.dataset.value = val;
        const label = id === "county" ? countyDisplayName(val, items) : val;
        item.innerHTML = `<span>${label}</span><span class="check">✓</span>`;
        item.addEventListener("click", (e) => {
            e.stopPropagation();
            if (multiSelectActive[id]) {
                if (selectedSet.has(val)) {
                    selectedSet.delete(val);
                    item.classList.remove("selected");
                } else {
                    selectedSet.add(val);
                    item.classList.add("selected");
                }
            } else {
                selectedSet.clear();
                dropdown.querySelectorAll(".multi-select-item").forEach(el => el.classList.remove("selected"));
                selectedSet.add(val);
                item.classList.add("selected");
                dropdown.classList.remove("open");
            }
            updateMultiSelectDisplay(id, selectedSet, defaultText);
            onChange();
        });
        dropdown.appendChild(item);
    });
}

function updateMultiSelectDisplay(id, selectedSet, defaultText) {
    const display = document.getElementById(`${id}-display`);
    if (selectedSet.size === 0) {
        display.textContent = defaultText;
    } else if (id === "county") {
        display.textContent = Array.from(selectedSet).map(v => v.split("|")[1]).join(", ");
    } else {
        display.textContent = Array.from(selectedSet).join(", ");
    }
}

// Toggle dropdowns open/closed and close on outside click
for (const id of ["country", "state", "county"]) {
    document.getElementById(`${id}-display`).addEventListener("click", () => {
        // Close other dropdowns
        for (const other of ["country", "state", "county"]) {
            if (other !== id) document.getElementById(`${other}-dropdown`).classList.remove("open");
        }
        const dropdown = document.getElementById(`${id}-dropdown`);
        dropdown.classList.toggle("open");
        if (dropdown.classList.contains("open")) {
            const rect = dropdown.getBoundingClientRect();
            dropdown.style.maxHeight = `${window.innerHeight - rect.top - 16}px`;
        }
    });
}

document.addEventListener("click", (e) => {
    for (const id of ["country", "state", "county"]) {
        const select = document.getElementById(`${id}-select`);
        if (!select.contains(e.target)) {
            document.getElementById(`${id}-dropdown`).classList.remove("open");
        }
    }
});

// Search mode toggle (region / driving)
document.querySelectorAll(".search-mode-btn").forEach(btn => {
    btn.addEventListener("click", () => {
        document.querySelectorAll(".search-mode-btn").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        searchMode = btn.dataset.mode;
        document.getElementById("region-inputs").style.display = searchMode === "region" ? "" : "none";
        document.getElementById("driving-inputs").style.display = searchMode === "driving" ? "" : "none";
    });
});

// Nominatim location search
const locationInput = document.getElementById("location-input");
const locationDropdown = document.getElementById("location-dropdown");
const locationSelected = document.getElementById("location-selected");

locationInput.addEventListener("input", () => {
    const q = locationInput.value.trim();
    if (q.length < 3) {
        locationDropdown.classList.remove("open");
        return;
    }
    clearTimeout(nominatimTimeout);
    nominatimTimeout = setTimeout(() => fetchLocationSuggestions(q), 300);
});

async function fetchLocationSuggestions(query) {
    try {
        const resp = await fetch(
            `https://nominatim.openstreetmap.org/search?q=${encodeURIComponent(query)}&format=json&limit=5`,
            { headers: { "Accept-Language": "en" } }
        );
        const results = await resp.json();
        renderLocationDropdown(results);
    } catch (err) {
        locationDropdown.classList.remove("open");
    }
}

function renderLocationDropdown(results) {
    locationDropdown.innerHTML = "";
    activeLocationIndex = -1;

    if (results.length === 0) {
        locationDropdown.classList.remove("open");
        return;
    }

    results.forEach((r) => {
        const item = document.createElement("div");
        item.className = "location-dropdown-item";
        item.textContent = r.display_name;
        item.addEventListener("mousedown", (e) => {
            e.preventDefault();
            selectLocation(r);
        });
        locationDropdown.appendChild(item);
    });
    locationDropdown.classList.add("open");
}

function selectLocation(result) {
    centerLat = parseFloat(result.lat);
    centerLon = parseFloat(result.lon);
    locationName = result.display_name;
    locationInput.value = result.display_name;
    locationDropdown.classList.remove("open");
    locationSelected.textContent = `${centerLat.toFixed(4)}, ${centerLon.toFixed(4)}`;
}

locationInput.addEventListener("keydown", (e) => {
    const items = locationDropdown.querySelectorAll(".location-dropdown-item");
    if (!items.length) return;

    if (e.key === "ArrowDown") {
        e.preventDefault();
        activeLocationIndex = Math.min(activeLocationIndex + 1, items.length - 1);
        items.forEach((el, i) => el.classList.toggle("active", i === activeLocationIndex));
        items[activeLocationIndex].scrollIntoView({ block: "nearest" });
    } else if (e.key === "ArrowUp") {
        e.preventDefault();
        activeLocationIndex = Math.max(activeLocationIndex - 1, 0);
        items.forEach((el, i) => el.classList.toggle("active", i === activeLocationIndex));
        items[activeLocationIndex].scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter" && activeLocationIndex >= 0) {
        e.preventDefault();
        items[activeLocationIndex].dispatchEvent(new MouseEvent("mousedown"));
    }
});

document.getElementById("use-my-location").addEventListener("click", () => {
    if (!navigator.geolocation) {
        alert("Geolocation is not supported by your browser.");
        return;
    }
    const btn = document.getElementById("use-my-location");
    btn.disabled = true;
    navigator.geolocation.getCurrentPosition(
        (pos) => {
            centerLat = pos.coords.latitude;
            centerLon = pos.coords.longitude;
            locationName = "Current location";
            locationInput.value = "Current location";
            locationDropdown.classList.remove("open");
            locationSelected.textContent = `${centerLat.toFixed(4)}, ${centerLon.toFixed(4)}`;
            btn.disabled = false;
        },
        (err) => {
            alert("Could not get your location. Please search manually.");
            btn.disabled = false;
        },
        { timeout: 10000 }
    );
});

// Scroll wheel adjusts number inputs
function addScrollWheel(id, min, max) {
    document.getElementById(id).addEventListener("wheel", (e) => {
        e.preventDefault();
        const input = e.currentTarget;
        const step = e.shiftKey ? 10 : 1;
        const delta = e.deltaY < 0 ? step : -step;
        const current = parseInt(input.value) || 0;
        input.value = Math.max(min, Math.min(max, current + delta));
    });
}
addScrollWheel("max-driving-minutes", 1, 480);
addScrollWheel("k-input", 1, 20);

locationInput.addEventListener("blur", () => {
    locationDropdown.classList.remove("open");
});

locationInput.addEventListener("focus", () => {
    if (locationDropdown.children.length > 0) {
        locationDropdown.classList.add("open");
    }
});

// Clear stale file input on page load
document.getElementById("csv-input").value = "";

// Parse CSV client-side
document.getElementById("csv-input").addEventListener("change", e => {
    const file = e.target.files[0];
    if (!file) return;

    const status = document.getElementById("file-status");
    status.textContent = "Processing eBird data...";
    status.className = "form-hint";

    const reader = new FileReader();
    reader.onload = ev => {
        // Defer processing so the "Processing..." message paints first
        requestAnimationFrame(() => setTimeout(() => {
            try {
                const text = ev.target.result;
                allObservations = parseObservations(text);
                if (allObservations.length === 0) {
                    status.textContent = "No observations found. Is this a valid eBird CSV?";
                    status.className = "form-hint error";
                    return;
                }
                populateScopeDropdowns();
                updateLifeList();
                document.getElementById("optimize-btn").disabled = false;
            } catch (err) {
                status.textContent = "Failed to parse CSV file.";
                status.className = "form-hint error";
            }
        }, 0));
    };
    reader.onerror = () => {
        status.textContent = "Failed to read file.";
        status.className = "form-hint error";
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
    const dateIdx = header.indexOf("Date");
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
                date: cols[dateIdx] || "",
            });
        }
    }
    return observations;
}

function regionName(code) {
    return regionNames[code] || code;
}

function populateScopeDropdowns() {
    const country = document.getElementById("scope-country").value;
    const state = document.getElementById("scope-state").value;

    // Countries: always all
    const countrySel = document.getElementById("scope-country");
    const countries = new Set();
    for (const obs of allObservations) {
        if (obs.countryCode) countries.add(obs.countryCode);
    }
    countrySel.innerHTML = '<option value="">World (all countries)</option>';
    for (const code of [...countries].sort((a, b) => regionName(a).localeCompare(regionName(b)))) {
        const opt = document.createElement("option");
        opt.value = code;
        opt.textContent = regionName(code);
        countrySel.appendChild(opt);
    }
    countrySel.value = countries.has(country) ? country : "";
    countrySel.disabled = false;

    // States: filtered by country if one is selected
    const stateSel = document.getElementById("scope-state");
    const states = new Set();
    for (const obs of allObservations) {
        if (obs.state && (!countrySel.value || obs.countryCode === countrySel.value)) {
            states.add(obs.state);
        }
    }
    stateSel.innerHTML = '<option value="">All states/provinces</option>';
    for (const code of [...states].sort((a, b) => regionName(a).localeCompare(regionName(b)))) {
        const opt = document.createElement("option");
        opt.value = code;
        opt.textContent = regionName(code);
        stateSel.appendChild(opt);
    }
    stateSel.value = states.has(state) ? state : "";
    stateSel.disabled = states.size === 0;

    // Counties: filtered by country and state if selected
    const countySel = document.getElementById("scope-county");
    const county = countySel.value;
    const counties = new Set();
    for (const obs of allObservations) {
        if (obs.county
            && (!countrySel.value || obs.countryCode === countrySel.value)
            && (!stateSel.value || obs.state === stateSel.value)) {
            counties.add(obs.county);
        }
    }
    countySel.innerHTML = '<option value="">All counties</option>';
    for (const c of [...counties].sort()) {
        const opt = document.createElement("option");
        opt.value = c;
        opt.textContent = c;
        countySel.appendChild(opt);
    }
    countySel.value = counties.has(county) ? county : "";
    countySel.disabled = counties.size === 0;
}

document.getElementById("scope-country").addEventListener("change", () => {
    populateScopeDropdowns();
    updateLifeList();
});

document.getElementById("scope-state").addEventListener("change", () => {
    const state = document.getElementById("scope-state").value;
    if (state && !document.getElementById("scope-country").value) {
        const obs = allObservations.find(o => o.state === state);
        if (obs) document.getElementById("scope-country").value = obs.countryCode;
    }
    populateScopeDropdowns();
    updateLifeList();
});

document.getElementById("scope-county").addEventListener("change", () => {
    const county = document.getElementById("scope-county").value;
    if (county) {
        const obs = allObservations.find(o => o.county === county);
        if (obs) {
            if (!document.getElementById("scope-country").value) {
                document.getElementById("scope-country").value = obs.countryCode;
            }
            if (!document.getElementById("scope-state").value) {
                document.getElementById("scope-state").value = obs.state;
            }
        }
    }
    populateScopeDropdowns();
    updateLifeList();
});

function updateLifeList() {
    const country = document.getElementById("scope-country").value;
    const state = document.getElementById("scope-state").value;
    const county = document.getElementById("scope-county").value;

    let filtered = allObservations;
    if (country) filtered = filtered.filter(o => o.countryCode === country);
    if (state) filtered = filtered.filter(o => o.state === state);
    if (county) filtered = filtered.filter(o => o.county === county);

    if (listType === "year") {
        const currentYear = String(new Date().getFullYear());
        filtered = filtered.filter(o => o.date.startsWith(currentYear));
    }

    const seen = new Set();
    for (const obs of filtered) seen.add(obs.name);
    lifeList = Array.from(seen);

    // Build scope label
    let label = "World";
    if (county) label = county;
    else if (state) label = regionName(state);
    else if (country) label = regionName(country);

    const listLabel = listType === "year" ? "year list" : "life list";

    const status = document.getElementById("file-status");
    status.textContent = `${lifeList.length} species on your ${label} ${listLabel}`;
    status.className = "form-hint loaded";
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
let lastRequestBody = null;
let excludedLocalityIds = [];

document.getElementById("optimize-btn").addEventListener("click", async () => {
    excludedLocalityIds = [];
    await runOptimization();
});

async function runOptimization() {
    const btn = document.getElementById("optimize-btn");
    btn.disabled = true;
    btn.textContent = "Optimizing...";

    if (targetMode === "search" && !selectedSpecies) {
        alert("Please select a species first.");
        btn.disabled = false;
        btn.textContent = "Optimize";
        return;
    }

    const body = {
        start_date: getDateValue("start"),
        end_date: getDateValue("end"),
        k: parseInt(document.getElementById("k-input").value),
    };

    if (targetMode === "search") {
        body.target_species = selectedSpecies.comName;
    } else {
        body.life_list = lifeList;
    }

    if (searchMode === "driving") {
        if (centerLat == null || centerLon == null) {
            alert("Please select a location first.");
            btn.disabled = false;
            btn.textContent = "Optimize";
            return;
        }
        const maxMin = parseInt(document.getElementById("max-driving-minutes").value);
        if (!maxMin || maxMin < 1) {
            alert("Please enter a valid driving time.");
            btn.disabled = false;
            btn.textContent = "Optimize";
            return;
        }
        body.center_lat = centerLat;
        body.center_lon = centerLon;
        body.max_driving_minutes = maxMin;
    } else {
        const counties = Array.from(selectedCounties);
        const states = Array.from(selectedStates);
        const countries = Array.from(selectedCountries);
        if (counties.length > 0) {
            // Send state|county pairs for unambiguous filtering
            body.state_counties = counties.map(v => {
                const [state, county] = v.split("|");
                return { state, county };
            });
        } else if (states.length > 0) {
            body.states = states;
        } else if (countries.length === 1) {
            body.country = countries[0];
        }
    }

    if (excludedLocalityIds.length > 0) {
        body.exclude_locality_ids = excludedLocalityIds;
    }

    lastRequestBody = body;

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
}

function renderResults(data) {
    const el = document.getElementById("results");
    lastResultsData = data;

    const isSearch = targetMode === "search";
    const noun = (!isSearch && listType === "life") ? "lifers" : "targets";

    if (!data.hotspots || data.hotspots.length === 0) {
        el.innerHTML = `<div class="empty-state"><p>No potential ${noun} found for this area and date range.</p></div>`;
        return;
    }

    let html = `
        <div class="results-layout">
        <div class="results-list">
        <div class="results-mode-toggle">
            <button class="results-mode-btn${resultsMode === "explore" ? " active" : ""}" data-mode="explore">Explore</button>
            <button class="results-mode-btn${resultsMode === "plan" ? " active" : ""}" data-mode="plan">Plan</button>
        </div>
        <div class="metrics">
            <div class="metric-card">
                <div class="value">${data.total_expected_lifers}</div>
                <div class="label">Expected ${noun}</div>
            </div>
            <div class="metric-card">
                <div class="value">${data.num_candidate_hotspots}</div>
                <div class="label">Hotspots evaluated</div>
            </div>
            ${isSearch ? "" : `
            <div class="metric-card">
                <div class="value">${data.num_potential_lifers}</div>
                <div class="label">Potential ${noun}</div>
            </div>
            `}
        </div>
        <div id="results-view-container"></div>
        </div>
        <div class="results-map"><div id="map"></div></div>
        </div>
    `;

    el.innerHTML = html;

    // Wire up mode toggle
    document.querySelectorAll(".results-mode-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".results-mode-btn").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            resultsMode = btn.dataset.mode;
            renderResultsView();
            updateMapForMode();
        });
    });

    // Initialize map
    if (map) { map.remove(); map = null; }

    map = L.map("map");
    const theme = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
    const tiles = tileSets[theme];
    tileLayer = L.tileLayer(tiles.url, { attribution: tiles.attribution }).addTo(map);

    updateMapForMode();
    renderResultsView();
}

function updateMapForMode() {
    if (!map || !lastResultsData) return;

    // Clear existing markers
    Object.values(markers).forEach(m => m.remove());
    markers = {};

    const hotspots = resultsMode === "plan" ? itinerary : lastResultsData.hotspots;
    const bounds = [];

    hotspots.forEach((h, i) => {
        const num = resultsMode === "plan" ? i + 1 : h.rank;
        const icon = L.divIcon({
            className: "map-marker",
            html: `<div class="map-marker-inner" data-rank="${num}">${num}</div>`,
            iconSize: [28, 28],
            iconAnchor: [14, 14],
        });
        const marker = L.marker([h.latitude, h.longitude], { icon }).addTo(map);
        markers[num] = marker;

        marker.on("mouseover", () => highlightHotspot(num, true));
        marker.on("mouseout", () => highlightHotspot(num, false));
        marker.on("click", () => toggleHotspotBody(num, true));

        bounds.push([h.latitude, h.longitude]);
    });

    // Remove old fit control and add new one
    if (map._fitControl) { map.removeControl(map._fitControl); }
    if (bounds.length > 0) {
        map.fitBounds(bounds, { padding: [30, 30] });

        const FitControl = L.Control.extend({
            options: { position: "topright" },
            onAdd() {
                const btn = L.DomUtil.create("button", "map-fit-btn");
                btn.title = "Zoom to fit all hotspots";
                btn.innerHTML = `<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><polyline points="1,5 1,1 5,1"/><polyline points="11,1 15,1 15,5"/><polyline points="15,11 15,15 11,15"/><polyline points="5,15 1,15 1,11"/></svg>`;
                L.DomEvent.disableClickPropagation(btn);
                btn.addEventListener("click", () => map.fitBounds(bounds, { padding: [30, 30] }));
                return btn;
            }
        });
        map._fitControl = new FitControl();
        map.addControl(map._fitControl);
    }
}

function renderResultsView() {
    if (resultsMode === "explore") {
        renderExploreView();
    } else {
        renderPlanView();
    }
}

function renderExploreView() {
    const data = lastResultsData;
    if (!data) return;
    const container = document.getElementById("results-view-container");
    const isSearch = targetMode === "search";
    const noun = (!isSearch && listType === "life") ? "lifers" : "targets";

    let html = `
        <div class="section-title-row">
            <div class="section-title">Recommended Hotspots</div>
            <div class="expand-collapse-btns">
                <button class="expand-collapse-btn" id="expand-all-btn">Expand All</button>
                <button class="expand-collapse-btn" id="collapse-all-btn">Collapse All</button>
            </div>
        </div>
    `;

    data.hotspots.forEach(h => {
        const inItinerary = itinerary.some(it => it.locality_id === h.locality_id);
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
            <div class="hotspot-card" data-rank="${h.rank}">
                <div class="hotspot-header">
                    <span class="rank">${h.rank}</span>
                    <span class="name">${h.locality}</span>
                    <span class="gain-group">
                        <span class="gain">+${h.target_species.reduce((sum, sp) => sum + sp.probability, 0).toFixed(2)} ${noun}</span>
                        <span class="gain-marginal">+${h.marginal_gain.toFixed(2)} marginal</span>
                    </span>
                    <button class="${inItinerary ? "added-itinerary-btn" : "add-itinerary-btn"}" data-locality-id="${h.locality_id}" title="${inItinerary ? "Added to itinerary" : "Add to itinerary"}">
                        ${inItinerary ? "&#10003;" : "+"}
                    </button>
                </div>
                <div class="hotspot-body">
                    <div class="hotspot-meta">
                        ${h.county} &middot;
                        ${h.latitude.toFixed(4)}, ${h.longitude.toFixed(4)} &middot;
                        <a href="https://ebird.org/hotspot/L${h.locality_id}" target="_blank" rel="noopener">View on eBird</a> &middot;
                        <a href="https://www.google.com/maps/search/${encodeURIComponent(h.locality)}/@${h.latitude},${h.longitude},15z" target="_blank" rel="noopener">View on Google Maps</a> &middot;
                        <a href="#" class="exclude-btn" data-locality-id="${h.locality_id}">Exclude &amp; Recalculate</a>
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
            <div class="section-title" style="margin-top: 1.5rem;">All Potential ${isSearch ? "Targets" : "Lifers"} (Combined Probability)</div>
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

    container.innerHTML = html;

    // Wire up hotspot card hover and click
    container.querySelectorAll(".hotspot-card[data-rank]").forEach(card => {
        const rank = parseInt(card.dataset.rank);
        const header = card.querySelector(".hotspot-header");

        header.addEventListener("click", (e) => {
            if (e.target.closest(".add-itinerary-btn") || e.target.closest(".added-itinerary-btn")) return;
            toggleHotspotBody(rank);
        });
        card.addEventListener("mouseenter", () => highlightHotspot(rank, true));
        card.addEventListener("mouseleave", () => highlightHotspot(rank, false));
    });

    // Wire up add-to-itinerary buttons
    container.querySelectorAll(".add-itinerary-btn").forEach(btn => {
        btn.addEventListener("click", (e) => {
            e.stopPropagation();
            const localityId = parseInt(btn.dataset.localityId);
            const hotspot = data.hotspots.find(h => h.locality_id === localityId);
            if (hotspot && !itinerary.some(it => it.locality_id === localityId)) {
                itinerary.push(hotspot);
                btn.className = "added-itinerary-btn";
                btn.innerHTML = "&#10003;";
                btn.title = "Added to itinerary";
            }
        });
    });

    container.querySelectorAll(".exclude-btn").forEach(btn => {
        btn.addEventListener("click", (e) => {
            e.preventDefault();
            e.stopPropagation();
            const localityId = parseInt(btn.dataset.localityId);
            excludedLocalityIds.push(localityId);
            runOptimization();
        });
    });

    document.getElementById("expand-all-btn").addEventListener("click", () => {
        container.querySelectorAll(".hotspot-card[data-rank] .hotspot-body").forEach(b => b.classList.add("open"));
    });
    document.getElementById("collapse-all-btn").addEventListener("click", () => {
        container.querySelectorAll(".hotspot-card[data-rank] .hotspot-body").forEach(b => b.classList.remove("open"));
    });
}

function renderPlanView() {
    const container = document.getElementById("results-view-container");
    const isSearch = targetMode === "search";
    const noun = (!isSearch && listType === "life") ? "lifers" : "targets";

    if (itinerary.length === 0) {
        container.innerHTML = `<div class="empty-state"><p>Add hotspots from the Explore tab to build your itinerary.</p></div>`;
        return;
    }

    let html = `
        <div class="section-title-row">
            <div class="section-title">Your Itinerary</div>
            <div class="expand-collapse-btns">
                <button class="expand-collapse-btn" id="expand-all-btn">Expand All</button>
                <button class="expand-collapse-btn" id="collapse-all-btn">Collapse All</button>
            </div>
        </div>
    `;

    itinerary.forEach((h, i) => {
        const rank = i + 1;
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
            <div class="hotspot-card plan-card" data-rank="${rank}" data-index="${i}">
                <div class="hotspot-header">
                    <span class="drag-handle" title="Drag to reorder">&#9776;</span>
                    <span class="rank">${rank}</span>
                    <span class="name">${h.locality}</span>
                    <span class="gain-group">
                        <span class="gain">+${h.target_species.reduce((sum, sp) => sum + sp.probability, 0).toFixed(2)} ${noun}</span>
                        <span class="gain-marginal">+${h.marginal_gain.toFixed(2)} marginal</span>
                    </span>
                    <button class="remove-itinerary-btn" data-index="${i}" title="Remove from itinerary">&times;</button>
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

    container.innerHTML = html;

    // Wire up card interactions
    container.querySelectorAll(".plan-card").forEach(card => {
        const rank = parseInt(card.dataset.rank);
        const header = card.querySelector(".hotspot-header");

        header.addEventListener("click", (e) => {
            if (e.target.closest(".drag-handle") || e.target.closest(".remove-itinerary-btn")) return;
            toggleHotspotBody(rank);
        });
        card.addEventListener("mouseenter", () => highlightHotspot(rank, true));
        card.addEventListener("mouseleave", () => highlightHotspot(rank, false));
    });

    // Wire up remove buttons
    container.querySelectorAll(".remove-itinerary-btn").forEach(btn => {
        btn.addEventListener("click", (e) => {
            e.stopPropagation();
            const idx = parseInt(btn.dataset.index);
            itinerary.splice(idx, 1);
            renderPlanView();
            updateMapForMode();
        });
    });

    // Pointer-based drag and drop reordering
    let dragState = null;

    container.querySelectorAll(".drag-handle").forEach(handle => {
        handle.addEventListener("mousedown", (e) => {
            e.preventDefault();
            const card = handle.closest(".plan-card");
            const srcIndex = parseInt(card.dataset.index);
            const rect = card.getBoundingClientRect();

            const placeholder = document.createElement("div");
            placeholder.className = "drag-placeholder";
            placeholder.style.height = rect.height + "px";

            card.classList.add("dragging");
            card.style.position = "fixed";
            card.style.top = rect.top + "px";
            card.style.left = rect.left + "px";
            card.style.width = rect.width + "px";
            card.style.zIndex = "1000";

            card.parentNode.insertBefore(placeholder, card);

            isDragging = true;
            container.classList.add("drag-active");
            dragState = { srcIndex, card, placeholder, startY: e.clientY, cardTop: rect.top };
            document.addEventListener("mousemove", onDragMove);
            document.addEventListener("mouseup", onDragEnd);
        });
    });

    function onDragMove(e) {
        if (!dragState) return;
        const { card, placeholder, startY, cardTop } = dragState;

        card.style.top = (cardTop + e.clientY - startY) + "px";

        // Get all siblings that are either non-dragging cards or the placeholder
        const siblings = [...container.querySelectorAll(".plan-card:not(.dragging), .drag-placeholder")];
        const placeholderIdx = siblings.indexOf(placeholder);

        // Check if we should move the placeholder up or down
        let shouldMove = false;
        let movingCard = null;

        // Move up: if cursor is above the midpoint of the sibling before the placeholder
        if (placeholderIdx > 0) {
            const prev = siblings[placeholderIdx - 1];
            const prevRect = prev.getBoundingClientRect();
            if (e.clientY < prevRect.top + prevRect.height / 2) {
                movingCard = prev;
                shouldMove = true;
            }
        }
        // Move down: if cursor is below the midpoint of the sibling after the placeholder
        if (!shouldMove && placeholderIdx < siblings.length - 1) {
            const next = siblings[placeholderIdx + 1];
            const nextRect = next.getBoundingClientRect();
            if (e.clientY > nextRect.top + nextRect.height / 2) {
                movingCard = next;
                shouldMove = true;
            }
        }

        if (shouldMove && movingCard) {
            // FLIP: record position before move
            const oldRect = movingCard.getBoundingClientRect();

            // Move placeholder
            if (siblings.indexOf(movingCard) < placeholderIdx) {
                movingCard.parentNode.insertBefore(placeholder, movingCard);
            } else {
                movingCard.parentNode.insertBefore(placeholder, movingCard.nextSibling);
            }

            // FLIP: animate from old position to new
            const newRect = movingCard.getBoundingClientRect();
            const deltaY = oldRect.top - newRect.top;
            if (deltaY !== 0) {
                movingCard.style.transition = "none";
                movingCard.style.transform = `translateY(${deltaY}px)`;
                movingCard.offsetHeight; // force reflow
                movingCard.style.transition = "transform 0.15s ease";
                movingCard.style.transform = "";
            }
        }
    }

    function onDragEnd() {
        if (!dragState) return;
        document.removeEventListener("mousemove", onDragMove);
        document.removeEventListener("mouseup", onDragEnd);

        const { srcIndex, card, placeholder } = dragState;
        isDragging = false;
        container.classList.remove("drag-active");

        // Determine drop index from placeholder position among non-dragging cards
        const cards = [...container.querySelectorAll(".plan-card:not(.dragging)")];
        let dropIndex = cards.length; // default: end
        for (let i = 0; i < cards.length; i++) {
            if (placeholder.compareDocumentPosition(cards[i]) & Node.DOCUMENT_POSITION_FOLLOWING) {
                dropIndex = i;
                break;
            }
        }
        card.classList.remove("dragging");
        card.style.position = "";
        card.style.top = "";
        card.style.left = "";
        card.style.width = "";
        card.style.zIndex = "";

        placeholder.parentNode.insertBefore(card, placeholder);
        placeholder.remove();

        if (srcIndex !== dropIndex) {
            const [moved] = itinerary.splice(srcIndex, 1);
            itinerary.splice(dropIndex, 0, moved);
            renderPlanView();
            updateMapForMode();
        }

        dragState = null;
    }

    // Expand/collapse all
    document.getElementById("expand-all-btn").addEventListener("click", () => {
        container.querySelectorAll(".plan-card .hotspot-body").forEach(b => b.classList.add("open"));
    });
    document.getElementById("collapse-all-btn").addEventListener("click", () => {
        container.querySelectorAll(".plan-card .hotspot-body").forEach(b => b.classList.remove("open"));
    });
}

function highlightHotspot(rank, on) {
    if (isDragging) return;

    // Highlight map marker via DOM query
    const inner = document.querySelector(`.map-marker-inner[data-rank="${rank}"]`);
    if (inner) inner.classList.toggle("highlight", on);

    // Highlight hotspot card
    const card = document.querySelector(`.hotspot-card[data-rank="${rank}"]`);
    if (card) card.classList.toggle("highlight", on);
}

function toggleHotspotBody(rank, scroll) {
    const card = document.querySelector(`.hotspot-card[data-rank="${rank}"]`);
    if (!card) return;
    const body = card.querySelector(".hotspot-body");
    if (body) body.classList.toggle("open");

    if (scroll) {
        card.scrollIntoView({ behavior: "smooth", block: "start" });
    }
}

// Species search autocomplete
let allSpecies = []; // loaded from API
let selectedSpecies = null;
let activeDropdownIndex = -1;

fetch("/api/species")
    .then(r => r.json())
    .then(data => { allSpecies = data; });

const speciesInput = document.getElementById("species-input");
const speciesDropdown = document.getElementById("species-dropdown");

function searchRank(species, query) {
    // Returns 0 (no match) or 1-6 (priority, lower = better match)
    const q = query.toLowerCase();
    if (species.comName.toLowerCase().includes(q)) return 1;
    if (species.bandingCodes.some(c => c.toLowerCase().startsWith(q))) return 2;
    if (species.comNameCodes.some(c => c.toLowerCase().startsWith(q))) return 3;
    if (species.sciNameCodes.some(c => c.toLowerCase().startsWith(q))) return 4;
    if (species.speciesCode.toLowerCase().startsWith(q)) return 5;
    if (species.sciName.toLowerCase().includes(q)) return 6;
    return 0;
}

function rankedSearch(query) {
    const scored = [];
    for (const sp of allSpecies) {
        const rank = searchRank(sp, query);
        if (rank > 0) scored.push({ sp, rank });
    }
    scored.sort((a, b) => a.rank - b.rank);
    return scored.slice(0, 20).map(s => s.sp);
}

function renderSpeciesDropdown(matches) {
    speciesDropdown.innerHTML = "";
    activeDropdownIndex = -1;

    if (matches.length === 0) {
        speciesDropdown.classList.remove("open");
        return;
    }

    matches.slice(0, 20).forEach((sp) => {
        const item = document.createElement("div");
        item.className = "species-dropdown-item";
        item.innerHTML = `${sp.comName}<span class="sci-name">${sp.sciName}</span>`;
        item.addEventListener("mousedown", (e) => {
            e.preventDefault();
            selectSpecies(sp);
        });
        speciesDropdown.appendChild(item);
    });
    speciesDropdown.classList.add("open");
}

function selectSpecies(sp) {
    selectedSpecies = sp;
    speciesInput.value = sp.comName;
    speciesDropdown.classList.remove("open");
    document.getElementById("optimize-btn").disabled = false;
}

speciesInput.addEventListener("input", () => {
    const q = speciesInput.value.trim();
    if (q.length < 2) {
        speciesDropdown.classList.remove("open");
        return;
    }
    const matches = rankedSearch(q);
    renderSpeciesDropdown(matches);
});

speciesInput.addEventListener("keydown", (e) => {
    const items = speciesDropdown.querySelectorAll(".species-dropdown-item");
    if (!items.length) return;

    if (e.key === "ArrowDown") {
        e.preventDefault();
        activeDropdownIndex = Math.min(activeDropdownIndex + 1, items.length - 1);
        items.forEach((el, i) => el.classList.toggle("active", i === activeDropdownIndex));
        items[activeDropdownIndex].scrollIntoView({ block: "nearest" });
    } else if (e.key === "ArrowUp") {
        e.preventDefault();
        activeDropdownIndex = Math.max(activeDropdownIndex - 1, 0);
        items.forEach((el, i) => el.classList.toggle("active", i === activeDropdownIndex));
        items[activeDropdownIndex].scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter" && activeDropdownIndex >= 0) {
        e.preventDefault();
        const q = speciesInput.value.trim();
        const matches = rankedSearch(q);
        if (matches[activeDropdownIndex]) selectSpecies(matches[activeDropdownIndex]);
    }
});

speciesInput.addEventListener("blur", () => {
    speciesDropdown.classList.remove("open");
});

speciesInput.addEventListener("focus", () => {
    const q = speciesInput.value.trim();
    if (q.length >= 2) {
        const matches = rankedSearch(q);
        renderSpeciesDropdown(matches);
    }
});
