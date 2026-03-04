// Beta wall
const betaOverlay = document.getElementById("beta-wall-overlay");
const betaForm = document.getElementById("beta-wall-form");
const betaNameInput = document.getElementById("beta-wall-name");

if (localStorage.getItem("beta-name")) {
    betaOverlay.classList.remove("open");
} else {
    betaNameInput.focus();
}

betaForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = betaNameInput.value.trim();
    if (!name) return;
    try {
        const resp = await fetch(`/api/beta-check?name=${encodeURIComponent(name)}`);
        if (!resp.ok) {
            betaNameInput.style.borderColor = "var(--danger, #d93025)";
            betaNameInput.placeholder = "Not on the beta list";
            betaNameInput.value = "";
            return;
        }
        localStorage.setItem("beta-name", name);
        betaOverlay.classList.remove("open");
    } catch {
        betaNameInput.style.borderColor = "var(--danger, #d93025)";
        betaNameInput.placeholder = "Something went wrong";
        betaNameInput.value = "";
    }
});

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
let routeLine = null;
let hotspotSearchTimeout = null;
let activeHotspotSearchIndex = -1;

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

// Upload help dialog
document.getElementById("upload-help-btn").addEventListener("click", () => {
    document.getElementById("upload-help-overlay").classList.add("open");
});
document.getElementById("upload-help-close").addEventListener("click", () => {
    document.getElementById("upload-help-overlay").classList.remove("open");
});
document.getElementById("upload-help-overlay").addEventListener("click", (e) => {
    if (e.target === e.currentTarget) document.getElementById("upload-help-overlay").classList.remove("open");
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
let targetMode = "lifelist"; // "search" or "lifelist"
let lifelistSource = "fresh"; // "upload" or "fresh"

function updateOptimizeBtn() {
    const btn = document.getElementById("optimize-btn");
    if (searchMode === "driving") {
        btn.disabled = true;
        btn.textContent = "Driving Time search coming soon";
    } else if (targetMode === "search") {
        btn.disabled = !selectedSpecies;
        btn.textContent = "Find Hotspots";
    } else if (lifelistSource === "fresh") {
        btn.disabled = false;
        btn.textContent = "Find Hotspots";
    } else {
        btn.disabled = allObservations.length === 0;
        btn.textContent = "Find Hotspots";
    }
}

document.querySelectorAll(".target-mode-btn").forEach(btn => {
    btn.addEventListener("click", () => {
        document.querySelectorAll(".target-mode-btn").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        targetMode = btn.dataset.mode;
        document.getElementById("target-search-inputs").style.display = targetMode === "search" ? "" : "none";
        document.getElementById("target-lifelist-inputs").style.display = targetMode === "lifelist" ? "" : "none";
        updateOptimizeBtn();
    });
});

// Lifelist source toggle (upload / fresh)
document.querySelectorAll(".lifelist-source-btn").forEach(btn => {
    btn.addEventListener("click", () => {
        document.querySelectorAll(".lifelist-source-btn").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        lifelistSource = btn.dataset.source;
        document.getElementById("lifelist-upload-inputs").style.display = lifelistSource === "upload" ? "" : "none";
        document.getElementById("lifelist-fresh-inputs").style.display = lifelistSource === "fresh" ? "" : "none";
        updateOptimizeBtn();
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
                dropdown.scrollTop = 0;
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
            if (other !== id) {
                const otherDd = document.getElementById(`${other}-dropdown`);
                otherDd.scrollTop = 0;
                otherDd.classList.remove("open");
            }
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
            const dd = document.getElementById(`${id}-dropdown`);
            dd.scrollTop = 0;
            dd.classList.remove("open");
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
        updateOptimizeBtn();
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
    updateOptimizeBtn();
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
            updateOptimizeBtn();
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
    locationDropdown.scrollTop = 0;
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
        // Parse in chunks so the browser can paint progress updates between batches
        requestAnimationFrame(() => requestAnimationFrame(() => {
            try {
                const text = ev.target.result;
                const lines = text.split("\n");
                if (lines.length < 2) {
                    status.textContent = "No observations found. Is this a valid eBird CSV?";
                    status.className = "form-hint error";
                    return;
                }

                const header = parseCSVLine(lines[0]);
                const nameIdx = header.indexOf("Common Name");
                const sciIdx = header.indexOf("Scientific Name");
                const countIdx = header.indexOf("Count");
                const stateIdx = header.indexOf("State/Province");
                const countyIdx = header.indexOf("County");
                const dateIdx = header.indexOf("Date");
                if (nameIdx === -1) {
                    status.textContent = "No observations found. Is this a valid eBird CSV?";
                    status.className = "form-hint error";
                    return;
                }

                const observations = [];
                const CHUNK = 5000;
                let i = 1;

                function processChunk() {
                    const end = Math.min(i + CHUNK, lines.length);
                    for (; i < end; i++) {
                        if (!lines[i].trim()) continue;
                        const cols = parseCSVLine(lines[i]);
                        let name = cols[nameIdx] || "";
                        const sci = cols[sciIdx] || "";
                        const count = cols[countIdx] || "";
                        if (count === "0") continue;
                        const parenIdx = name.indexOf(" (");
                        if (parenIdx !== -1) name = name.substring(0, parenIdx);
                        if (name.includes("/") || name.includes(" sp.") || sci.includes(" x ")) continue;
                        const stateCode = cols[stateIdx] || "";
                        const countryCode = stateCode.includes("-") ? stateCode.split("-")[0] : "";
                        if (name) {
                            observations.push({ name, sci, countryCode, state: stateCode, county: cols[countyIdx] || "", date: cols[dateIdx] || "" });
                        }
                    }
                    if (i < lines.length) {
                        status.textContent = `Processing eBird data... ${Math.round((i / lines.length) * 100)}%`;
                        setTimeout(processChunk, 0);
                    } else {
                        allObservations = observations;
                        if (allObservations.length === 0) {
                            status.textContent = "No observations found. Is this a valid eBird CSV?";
                            status.className = "form-hint error";
                            return;
                        }
                        populateScopeDropdowns();
                        updateLifeList();
                        updateOptimizeBtn();
                    }
                }
                processChunk();
            } catch (err) {
                status.textContent = "Failed to parse CSV file.";
                status.className = "form-hint error";
            }
        }));
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
        btn.textContent = "Find Hotspots";
        return;
    }

    const body = {
        start_date: getDateValue("start"),
        end_date: getDateValue("end"),
        k: parseInt(document.getElementById("k-input").value),
    };

    if (targetMode === "search") {
        body.target_species = selectedSpecies.comName;
    } else if (lifelistSource === "fresh") {
        body.life_list = [];
    } else {
        body.life_list = lifeList;
    }

    if (searchMode === "driving") {
        if (centerLat == null || centerLon == null) {
            alert("Please select a location first.");
            btn.disabled = false;
            btn.textContent = "Find Hotspots";
            return;
        }
        const maxMin = parseInt(document.getElementById("max-driving-minutes").value);
        if (!maxMin || maxMin < 1) {
            alert("Please enter a valid driving time.");
            btn.disabled = false;
            btn.textContent = "Find Hotspots";
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
        const msg = err instanceof TypeError
            ? "Connection lost. Please check your internet and try again."
            : err.message;
        document.getElementById("results").innerHTML =
            `<div class="empty-state"><p>${msg}</p></div>`;
    } finally {
        btn.disabled = false;
        btn.textContent = "Find Hotspots";
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
        <div id="metrics-container"></div>
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
        });
    });

    // Initialize map
    if (map) { map.remove(); map = null; }

    map = L.map("map");
    const theme = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
    const tiles = tileSets[theme];
    tileLayer = L.tileLayer(tiles.url, { attribution: tiles.attribution }).addTo(map);

    updateMap(true);
    renderResultsView();
}

function updateMap(fitToResults = false) {
    if (!map) return;

    // Clear existing markers
    Object.values(markers).forEach(m => m.remove());
    markers = {};

    const resultBounds = [];
    const allBounds = [];
    const resultIds = new Set();

    // Collect all marker data, then add in z-order (lowest priority first)
    const markerEntries = [];

    if (lastResultsData) {
        // Compute adjusted ranking by marginal gain accounting for itinerary
        const itinMiss = {};
        itinerary.forEach(h => {
            h.target_species.forEach(sp => {
                itinMiss[sp.common_name] = (itinMiss[sp.common_name] ?? 1) * (1 - sp.probability);
            });
        });
        const rankedIds = [...lastResultsData.hotspots]
            .map(h => ({ locality_id: h.locality_id, gain: h.target_species.reduce((sum, sp) => sum + sp.probability * (itinMiss[sp.common_name] ?? 1), 0) }))
            .sort((a, b) => b.gain - a.gain);
        const rankMap = {};
        rankedIds.forEach((r, i) => { rankMap[r.locality_id] = i; });

        lastResultsData.hotspots.forEach(h => {
            resultIds.add(h.locality_id);
            const itineraryIdx = itinerary.findIndex(it => it.locality_id === h.locality_id);
            const inItinerary = itineraryIdx !== -1;
            const adjRank = rankMap[h.locality_id];
            const label = inItinerary ? String(itineraryIdx + 1) : String.fromCharCode(65 + adjRank);
            const markerClass = inItinerary ? "map-marker-inner itinerary-marker" : "map-marker-inner";
            const order = inItinerary ? itineraryIdx : adjRank;
            markerEntries.push({ locality_id: h.locality_id, lat: h.latitude, lng: h.longitude, label, markerClass, inItinerary, order, isResult: true });
            resultBounds.push([h.latitude, h.longitude]);
            allBounds.push([h.latitude, h.longitude]);
        });
    }

    itinerary.forEach((it, idx) => {
        if (resultIds.has(it.locality_id)) return;
        markerEntries.push({ locality_id: it.locality_id, lat: it.latitude, lng: it.longitude, label: String(idx + 1), markerClass: "map-marker-inner itinerary-marker", inItinerary: true, order: idx, isResult: false });
        allBounds.push([it.latitude, it.longitude]);
    });

    // Sort: non-itinerary before itinerary, then higher order first (so lower order renders last = on top)
    markerEntries.sort((a, b) => {
        if (a.inItinerary !== b.inItinerary) return a.inItinerary ? 1 : -1;
        return b.order - a.order;
    });

    markerEntries.forEach((e, i) => {
        const icon = L.divIcon({
            className: "map-marker",
            html: `<div class="${e.markerClass}" data-locality-id="${e.locality_id}">${e.label}</div>`,
            iconSize: [28, 28],
            iconAnchor: [14, 14],
        });
        const marker = L.marker([e.lat, e.lng], { icon, zIndexOffset: i * 10 }).addTo(map);
        markers[e.locality_id] = marker;

        marker.on("mouseover", () => highlightHotspot(e.locality_id, true));
        marker.on("mouseout", () => highlightHotspot(e.locality_id, false));
        marker.on("click", () => toggleHotspotBody(e.locality_id, true));
    });

    // Remove old fit control and add new one
    if (map._fitControl) { map.removeControl(map._fitControl); }
    const fitBounds = fitToResults && resultBounds.length > 0 ? resultBounds : allBounds;
    if (fitToResults && fitBounds.length > 0) {
        map.fitBounds(fitBounds, { padding: [30, 30] });
    }
    if (allBounds.length > 0) {
        const FitControl = L.Control.extend({
            options: { position: "topright" },
            onAdd() {
                const btn = L.DomUtil.create("button", "map-fit-btn");
                btn.title = "Zoom to fit all hotspots";
                btn.innerHTML = `<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><polyline points="1,5 1,1 5,1"/><polyline points="11,1 15,1 15,5"/><polyline points="15,11 15,15 11,15"/><polyline points="5,15 1,15 1,11"/></svg>`;
                L.DomEvent.disableClickPropagation(btn);
                btn.addEventListener("click", () => map.fitBounds(allBounds, { padding: [30, 30] }));
                return btn;
            }
        });
        map._fitControl = new FitControl();
        map.addControl(map._fitControl);
    }
}

let routeRequestId = 0;
let lastRouteKey = null;
let lastDrivingTime = "—";

function clearRoute() {
    if (routeLine) { routeLine.remove(); routeLine = null; }
}

async function updateDrivingTime() {
    const el = document.getElementById("driving-time-value");
    if (!el) return;

    const routeKey = itinerary.map(h => h.locality_id).join(",");
    if (routeKey === lastRouteKey) {
        el.textContent = lastDrivingTime;
        return;
    }

    clearRoute();
    lastRouteKey = routeKey;
    const requestId = ++routeRequestId;
    if (!el) return;
    if (itinerary.length < 2) {
        lastDrivingTime = "—";
        el.textContent = lastDrivingTime;
        return;
    }
    el.textContent = "...";
    const coords = itinerary.map(h => `${h.longitude},${h.latitude}`).join(";");
    try {
        const res = await fetch(`https://router.project-osrm.org/route/v1/driving/${coords}?overview=full&geometries=geojson`);
        if (requestId !== routeRequestId) return;
        const data = await res.json();
        if (data.code === "Ok" && data.routes.length > 0) {
            const seconds = data.routes[0].duration;
            const hours = Math.floor(seconds / 3600);
            const minutes = Math.round((seconds % 3600) / 60);
            lastDrivingTime = hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m`;
            el.textContent = lastDrivingTime;

            if (map) {
                const geojson = data.routes[0].geometry;
                const accent = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim();
                routeLine = L.geoJSON(geojson, {
                    style: { color: accent, weight: 3, opacity: 0.7 }
                }).addTo(map);
            }
        } else {
            lastDrivingTime = "N/A";
            el.textContent = lastDrivingTime;
        }
    } catch {
        lastDrivingTime = "N/A";
        el.textContent = lastDrivingTime;
    }
}

function renderMetrics() {
    const data = lastResultsData;
    if (!data) return;
    const metricsEl = document.getElementById("metrics-container");
    if (!metricsEl) return;
    const isSearch = targetMode === "search";
    const noun = (!isSearch && listType === "life") ? "lifers" : "targets";

    if (resultsMode === "explore") {
        const avgLifers = data.hotspots.length > 0
            ? (data.hotspots.reduce((sum, h) => sum + h.target_species.reduce((s, sp) => s + sp.probability, 0), 0) / data.hotspots.length).toFixed(2)
            : "0";
        metricsEl.innerHTML = `<div class="metrics">
            <div class="metric-card">
                <div class="value">${avgLifers}</div>
                <div class="label">Avg ${noun} per hotspot</div>
            </div>
            <div class="metric-card">
                <div class="value">${data.num_candidate_hotspots}</div>
                <div class="label">Hotspots evaluated</div>
            </div>
            ${isSearch ? "" : `
            <div class="metric-card">
                <div class="value">${data.num_potential_lifers}</div>
                <div class="label">Potential ${noun}</div>
            </div>`}
        </div>`;
    } else {
        // Calculate expected lifers as sum of marginal gains in itinerary order
        const cMiss = {};
        let expectedLifers = 0;
        itinerary.forEach(h => {
            h.target_species.forEach(sp => {
                const miss = cMiss[sp.common_name] ?? 1;
                expectedLifers += sp.probability * miss;
                cMiss[sp.common_name] = miss * (1 - sp.probability);
            });
        });
        const potentialLifers = Object.values(cMiss).filter(miss => (1 - miss) >= 0.001).length;

        metricsEl.innerHTML = `<div class="metrics">
            <div class="metric-card">
                <div class="value">${expectedLifers.toFixed(2)}</div>
                <div class="label">Expected ${noun}</div>
            </div>
            <div class="metric-card">
                <div class="value" id="driving-time-value">—</div>
                <div class="label">Driving time</div>
            </div>
            ${isSearch ? "" : `
            <div class="metric-card">
                <div class="value">${potentialLifers}</div>
                <div class="label">Potential ${noun}</div>
            </div>`}
        </div>`;
        updateDrivingTime();
    }
}

function renderResultsView() {
    renderMetrics();
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

    // Cumulative miss probabilities from itinerary
    const itinMiss = {};
    itinerary.forEach(h => {
        h.target_species.forEach(sp => {
            itinMiss[sp.common_name] = (itinMiss[sp.common_name] ?? 1) * (1 - sp.probability);
        });
    });

    // Compute marginal gains and sort by them
    const ranked = data.hotspots.map(h => ({
        ...h,
        marginalGainAdj: h.target_species.reduce((sum, sp) => {
            const miss = itinMiss[sp.common_name] ?? 1;
            return sum + sp.probability * miss;
        }, 0),
    }));
    ranked.sort((a, b) => b.marginalGainAdj - a.marginalGainAdj);

    ranked.forEach((h, i) => {
        const inItinerary = itinerary.some(it => it.locality_id === h.locality_id);
        const letter = String.fromCharCode(65 + i);
        const marginalGain = h.marginalGainAdj;
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
            <div class="hotspot-card" data-locality-id="${h.locality_id}">
                <div class="hotspot-header">
                    <span class="rank">${letter}</span>
                    <span class="name">${h.locality}</span>
                    <span class="gain-group">
                        <span class="gain">+${h.target_species.reduce((sum, sp) => sum + sp.probability, 0).toFixed(2)} ${noun}</span>
                        <span class="gain-marginal">+${marginalGain.toFixed(2)} marginal</span>
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

    container.innerHTML = html;

    // Wire up hotspot card hover and click
    container.querySelectorAll(".hotspot-card[data-locality-id]").forEach(card => {
        const locId = card.dataset.localityId;
        const header = card.querySelector(".hotspot-header");

        header.addEventListener("click", (e) => {
            if (e.target.closest(".add-itinerary-btn") || e.target.closest(".added-itinerary-btn")) return;
            toggleHotspotBody(locId);
        });
        card.addEventListener("mouseenter", () => highlightHotspot(locId, true));
        card.addEventListener("mouseleave", () => highlightHotspot(locId, false));
    });

    // Wire up add-to-itinerary buttons
    container.querySelectorAll(".add-itinerary-btn").forEach(btn => {
        btn.addEventListener("click", (e) => {
            e.stopPropagation();
            const localityId = parseInt(btn.dataset.localityId);
            const hotspot = data.hotspots.find(h => h.locality_id === localityId);
            if (hotspot && !itinerary.some(it => it.locality_id === localityId)) {
                // FLIP: capture old positions
                const oldPositions = {};
                container.querySelectorAll(".hotspot-card[data-locality-id]").forEach(card => {
                    oldPositions[card.dataset.localityId] = card.getBoundingClientRect().top;
                });

                itinerary.push(hotspot);
                renderResultsView();
                updateMap();

                // FLIP: animate to new positions
                const newContainer = document.getElementById("results-view-container");
                const cardsToAnimate = [];
                newContainer.querySelectorAll(".hotspot-card[data-locality-id]").forEach(card => {
                    const oldTop = oldPositions[card.dataset.localityId];
                    if (oldTop === undefined) return;
                    const newTop = card.getBoundingClientRect().top;
                    const delta = oldTop - newTop;
                    if (Math.abs(delta) < 1) return;
                    card.style.transition = "none";
                    card.style.transform = `translateY(${delta}px)`;
                    cardsToAnimate.push(card);
                });
                // Force reflow so the inverted position is committed
                if (cardsToAnimate.length) cardsToAnimate[0].offsetHeight;
                cardsToAnimate.forEach(card => {
                    card.style.transition = "transform 1s ease";
                    card.style.transform = "";
                    card.addEventListener("transitionend", () => {
                        card.style.transition = "";
                    }, { once: true });
                });
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
        container.querySelectorAll(".hotspot-card[data-locality-id] .hotspot-body").forEach(b => b.classList.add("open"));
    });
    document.getElementById("collapse-all-btn").addEventListener("click", () => {
        container.querySelectorAll(".hotspot-card[data-locality-id] .hotspot-body").forEach(b => b.classList.remove("open"));
    });
}

function getGeoFilterParams() {
    const params = new URLSearchParams();
    if (searchMode === "region") {
        const counties = Array.from(selectedCounties);
        const states = Array.from(selectedStates);
        const countries = Array.from(selectedCountries);
        if (counties.length > 0) {
            params.set("state_counties", counties.join(","));
        } else if (states.length > 0) {
            params.set("states", states.join(","));
        } else if (countries.length === 1) {
            params.set("country", countries[0]);
        }
    }
    return params;
}

async function fetchHotspotSuggestions(query) {
    const params = getGeoFilterParams();
    params.set("q", query);
    try {
        const resp = await fetch(`/api/search-hotspots?${params.toString()}`);
        const results = await resp.json();
        renderHotspotSearchDropdown(results);
    } catch {
        const dropdown = document.getElementById("hotspot-search-dropdown");
        if (dropdown) dropdown.classList.remove("open");
    }
}

function renderHotspotSearchDropdown(results) {
    const dropdown = document.getElementById("hotspot-search-dropdown");
    if (!dropdown) return;
    dropdown.innerHTML = "";
    activeHotspotSearchIndex = -1;

    // Filter out hotspots already in itinerary
    const filtered = results.filter(r => !itinerary.some(it => it.locality_id === r.locality_id));

    if (filtered.length === 0) {
        dropdown.scrollTop = 0;
        dropdown.classList.remove("open");
        return;
    }

    filtered.forEach(r => {
        const item = document.createElement("div");
        item.className = "hotspot-search-dropdown-item";
        item.innerHTML = `${r.locality}<span class="hotspot-search-county">${r.county}</span>`;
        item.addEventListener("mousedown", (e) => {
            e.preventDefault();
            selectHotspotFromSearch(r);
        });
        dropdown.appendChild(item);
    });
    dropdown.classList.add("open");
}

async function selectHotspotFromSearch(summary) {
    const input = document.getElementById("hotspot-search-input");
    const dropdown = document.getElementById("hotspot-search-dropdown");
    if (input) input.value = "";
    if (dropdown) { dropdown.scrollTop = 0; dropdown.classList.remove("open"); }

    if (itinerary.some(it => it.locality_id === summary.locality_id)) return;

    const body = {
        locality_id: summary.locality_id,
        start_date: getDateValue("start"),
        end_date: getDateValue("end"),
    };

    if (targetMode === "search" && selectedSpecies) {
        body.target_species = selectedSpecies.comName;
    } else {
        body.life_list = lifeList;
    }

    try {
        const resp = await fetch("/api/hotspot-details", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        if (!resp.ok) return;
        const hotspot = await resp.json();

        if (!itinerary.some(it => it.locality_id === hotspot.locality_id)) {
            itinerary.push(hotspot);
            renderResultsView();
            updateMap();
        }
    } catch (err) {
        console.error("Error fetching hotspot details:", err);
    }
}

function wireHotspotSearch() {
    const input = document.getElementById("hotspot-search-input");
    const dropdown = document.getElementById("hotspot-search-dropdown");
    if (!input) return;

    input.addEventListener("input", () => {
        const q = input.value.trim();
        if (q.length < 3) {
            dropdown.scrollTop = 0;
            dropdown.classList.remove("open");
            return;
        }
        clearTimeout(hotspotSearchTimeout);
        hotspotSearchTimeout = setTimeout(() => fetchHotspotSuggestions(q), 300);
    });

    input.addEventListener("keydown", (e) => {
        const items = dropdown.querySelectorAll(".hotspot-search-dropdown-item");
        if (!items.length) return;

        if (e.key === "ArrowDown") {
            e.preventDefault();
            activeHotspotSearchIndex = Math.min(activeHotspotSearchIndex + 1, items.length - 1);
            items.forEach((el, i) => el.classList.toggle("active", i === activeHotspotSearchIndex));
            items[activeHotspotSearchIndex].scrollIntoView({ block: "nearest" });
        } else if (e.key === "ArrowUp") {
            e.preventDefault();
            activeHotspotSearchIndex = Math.max(activeHotspotSearchIndex - 1, 0);
            items.forEach((el, i) => el.classList.toggle("active", i === activeHotspotSearchIndex));
            items[activeHotspotSearchIndex].scrollIntoView({ block: "nearest" });
        } else if (e.key === "Enter" && activeHotspotSearchIndex >= 0) {
            e.preventDefault();
            items[activeHotspotSearchIndex].dispatchEvent(new MouseEvent("mousedown"));
        }
    });

    input.addEventListener("blur", () => {
        dropdown.scrollTop = 0;
        dropdown.classList.remove("open");
    });

    input.addEventListener("focus", () => {
        const q = input.value.trim();
        if (q.length >= 3 && dropdown.children.length > 0) {
            dropdown.classList.add("open");
        }
    });
}

function renderPlanView() {
    const container = document.getElementById("results-view-container");
    const isSearch = targetMode === "search";
    const noun = (!isSearch && listType === "life") ? "lifers" : "targets";

    const searchHtml = `
        <div class="hotspot-search">
            <input type="text" id="hotspot-search-input"
                   placeholder="Search for a hotspot to add..."
                   autocomplete="off" />
            <div class="hotspot-search-dropdown" id="hotspot-search-dropdown"></div>
        </div>
    `;

    if (itinerary.length === 0) {
        container.innerHTML = searchHtml + `<div class="empty-state"><p>Add hotspots from the Explore tab or search above to build your itinerary.</p></div>`;
        wireHotspotSearch();
        updateDrivingTime();
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

    html += searchHtml;

    // Calculate marginal gains based on itinerary order
    const cumulativeMiss = {}; // species -> product of (1 - p) for previous stops
    const marginalGains = [];
    itinerary.forEach(h => {
        let gain = 0;
        h.target_species.forEach(sp => {
            const miss = cumulativeMiss[sp.common_name] ?? 1;
            gain += sp.probability * miss;
            cumulativeMiss[sp.common_name] = miss * (1 - sp.probability);
        });
        marginalGains.push(gain);
    });

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
            <div class="hotspot-card plan-card" data-locality-id="${h.locality_id}" data-index="${i}">
                <div class="hotspot-header">
                    <span class="drag-handle" title="Drag to reorder">&#9776;</span>
                    <span class="rank">${rank}</span>
                    <span class="name">${h.locality}</span>
                    <span class="gain-group">
                        <span class="gain">+${marginalGains[i].toFixed(2)} marginal ${noun}</span>
                    </span>
                    <button class="remove-itinerary-btn" data-index="${i}" title="Remove from itinerary"></button>
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

    // Combined species table from itinerary hotspots
    const itineraryMiss = {};
    itinerary.forEach(h => {
        h.target_species.forEach(sp => {
            itineraryMiss[sp.common_name] = (itineraryMiss[sp.common_name] ?? 1) * (1 - sp.probability);
        });
    });
    const combinedProbs = Object.entries(itineraryMiss)
        .map(([name, miss]) => ({ common_name: name, probability: 1 - miss }))
        .filter(sp => sp.probability >= 0.001)
        .sort((a, b) => b.probability - a.probability);

    if (combinedProbs.length > 0) {
        const combinedRows = combinedProbs.map(sp => `
            <tr>
                <td>${sp.common_name}</td>
                <td>
                    <span class="prob-bar" style="width: ${sp.probability * 100}px"></span>
                    ${(sp.probability * 100).toFixed(1)}%
                </td>
            </tr>
        `).join("");

        html += `
            <div class="section-title" style="margin-top: 1.5rem;">All Potential ${isSearch ? "Targets" : "Lifers"}</div>
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
    wireHotspotSearch();

    // Wire up card interactions
    container.querySelectorAll(".plan-card").forEach(card => {
        const locId = card.dataset.localityId;
        const header = card.querySelector(".hotspot-header");

        header.addEventListener("click", (e) => {
            if (e.target.closest(".drag-handle") || e.target.closest(".remove-itinerary-btn")) return;
            toggleHotspotBody(locId);
        });
        card.addEventListener("mouseenter", () => highlightHotspot(locId, true));
        card.addEventListener("mouseleave", () => highlightHotspot(locId, false));
    });

    // Wire up remove buttons
    container.querySelectorAll(".remove-itinerary-btn").forEach(btn => {
        btn.addEventListener("click", (e) => {
            e.stopPropagation();
            const idx = parseInt(btn.dataset.index);
            itinerary.splice(idx, 1);
            renderPlanView();
            updateMap();
        });
    });

    // Pointer-based drag and drop reordering
    let dragState = null;

    function getPointerY(e) {
        return e.touches ? e.touches[0].clientY : e.clientY;
    }

    function startDrag(e, handle) {
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
        dragState = { srcIndex, card, placeholder, startY: getPointerY(e), cardTop: rect.top };
        document.addEventListener("mousemove", onDragMove);
        document.addEventListener("mouseup", onDragEnd);
        document.addEventListener("touchmove", onDragMove, { passive: false });
        document.addEventListener("touchend", onDragEnd);
    }

    container.querySelectorAll(".drag-handle").forEach(handle => {
        handle.addEventListener("mousedown", (e) => startDrag(e, handle));
        handle.addEventListener("touchstart", (e) => startDrag(e, handle), { passive: false });
    });

    function onDragMove(e) {
        if (!dragState) return;
        if (e.cancelable) e.preventDefault();
        const { card, placeholder, startY, cardTop } = dragState;

        card.style.top = (cardTop + getPointerY(e) - startY) + "px";

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
            if (getPointerY(e) < prevRect.top + prevRect.height / 2) {
                movingCard = prev;
                shouldMove = true;
            }
        }
        // Move down: if cursor is below the midpoint of the sibling after the placeholder
        if (!shouldMove && placeholderIdx < siblings.length - 1) {
            const next = siblings[placeholderIdx + 1];
            const nextRect = next.getBoundingClientRect();
            if (getPointerY(e) > nextRect.top + nextRect.height / 2) {
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
        document.removeEventListener("touchmove", onDragMove);
        document.removeEventListener("touchend", onDragEnd);

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
            updateMap();
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

    updateDrivingTime();
}

function highlightHotspot(localityId, on) {
    if (isDragging) return;

    // Highlight map marker via DOM query
    const inner = document.querySelector(`.map-marker-inner[data-locality-id="${localityId}"]`);
    if (inner) inner.classList.toggle("highlight", on);

    // Highlight hotspot card
    const card = document.querySelector(`.hotspot-card[data-locality-id="${localityId}"]`);
    if (card) card.classList.toggle("highlight", on);
}

function toggleHotspotBody(localityId, scroll) {
    const card = document.querySelector(`.hotspot-card[data-locality-id="${localityId}"]`);
    if (!card) return;
    const body = card.querySelector(".hotspot-body");
    if (body) {
        if (scroll) body.classList.add("open");
        else body.classList.toggle("open");
    }

    if (scroll) {
        card.scrollIntoView({ behavior: "smooth", block: "start" });
    }
}

// Species search autocomplete
let allSpecies = []; // loaded from API
let selectedSpecies = null;
let activeDropdownIndex = -1;

// Set initial button state based on default mode
updateOptimizeBtn();

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
    speciesDropdown.scrollTop = 0;
    speciesDropdown.classList.remove("open");
    updateOptimizeBtn();
}

speciesInput.addEventListener("input", () => {
    selectedSpecies = null;
    updateOptimizeBtn();
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
    speciesDropdown.scrollTop = 0;
    speciesDropdown.classList.remove("open");
});

speciesInput.addEventListener("focus", () => {
    const q = speciesInput.value.trim();
    if (q.length >= 2) {
        const matches = rankedSearch(q);
        renderSpeciesDropdown(matches);
    }
});

// ── Walkthrough ──────────────────────────────────────────────────────
(function() {
    const STORAGE_KEY = "walkthrough-completed";
    const FIRST_RESULTS_STEP = 6;

    const MOCK_DATA = {
        num_candidate_hotspots: 47,
        num_potential_lifers: 12,
        hotspots: [
            {
                locality_id: 191106, locality: "Central Park", latitude: 40.7829, longitude: -73.9654,
                county: "New York County",
                target_species: [
                    { common_name: "Blackburnian Warbler", probability: 0.42, recently_observed: true },
                    { common_name: "Scarlet Tanager", probability: 0.38, recently_observed: true },
                    { common_name: "Rose-breasted Grosbeak", probability: 0.25, recently_observed: false },
                ]
            },
            {
                locality_id: 165143, locality: "Jamaica Bay Wildlife Refuge", latitude: 40.6171, longitude: -73.8255,
                county: "Queens County",
                target_species: [
                    { common_name: "American Oystercatcher", probability: 0.55, recently_observed: true },
                    { common_name: "Seaside Sparrow", probability: 0.31, recently_observed: true },
                    { common_name: "Clapper Rail", probability: 0.22, recently_observed: false },
                    { common_name: "Ivory-billed Woodpecker", probability: 0, recently_observed: false },
                ]
            },
            {
                locality_id: 109516, locality: "Prospect Park", latitude: 40.6602, longitude: -73.9690,
                county: "Kings County",
                target_species: [
                    { common_name: "Wood Thrush", probability: 0.35, recently_observed: true },
                    { common_name: "Eastern Towhee", probability: 0.28, recently_observed: true },
                ]
            },
        ]
    };

    const steps = [
        {
            selector: ".form-group:has(.target-mode-btn)",
            title: "Choose Your Targets",
            desc: "Search for a specific species...",
            prefer: "right",
            onEnter: "switchToSearch"
        },
        {
            selector: ".form-group:has(.target-mode-btn)",
            title: "Choose Your Targets",
            desc: "...or upload your eBird data to find birds you haven't seen yet.",
            prefer: "right",
            onEnter: "switchToLifelist"
        },
        {
            selector: ".form-group:has(.target-mode-btn)",
            title: "Choose Your Targets",
            desc: "New to birding? Use Start Fresh to find hotspots with the most species — no eBird account needed.",
            prefer: "right",
            onEnter: "switchToFresh"
        },
        {
            selector: ".form-group:has(.search-mode-btn)",
            title: "Set Your Search Area",
            desc: "Search by geographic region or by driving time from a location.",
            prefer: "right"
        },
        {
            selector: ".date-row",
            title: "Pick a Date Range",
            desc: "Narrow results to a specific window. Detection probabilities are calculated from eBird checklists in this period.",
            prefer: "right"
        },
        {
            selector: "#optimize-btn",
            title: "Find Hotspots",
            desc: "Run the optimizer to find the best birding hotspots for your targets, area, and dates.",
            prefer: "right",
            mobilePrefer: "top"
        },
        {
            selector: ".results-mode-toggle",
            title: "Explore & Plan",
            desc: "Switch between Explore mode to browse recommended hotspots, and Plan mode to build a custom itinerary.",
            prefer: "bottom"
        },
        {
            selector: ".hotspot-card",
            title: "Hotspot Cards",
            desc: "Each card shows a hotspot's expected target species and detection probabilities. Click to expand and see details.",
            prefer: "left",
            onEnter: "switchToExplore"
        },
        {
            selector: ".add-itinerary-btn",
            title: "Build Your Itinerary",
            desc: "Click the + button to add a hotspot to your trip itinerary.",
            prefer: "left",
            onEnter: "switchToExplore"
        },
        {
            selector: ".plan-card",
            title: "Your Itinerary",
            desc: "Drag and drop cards to reorder your stops. The marginal gain updates based on visit order.",
            prefer: "left",
            onEnter: "switchToPlan"
        },
        {
            selector: ".remove-itinerary-btn",
            title: "Remove a Stop",
            desc: "Click the X button to remove a hotspot from your itinerary.",
            prefer: "left"
        },
        {
            selector: "#map",
            title: "Interactive Map",
            desc: "Hotspots appear on the map with ranked markers. Click a marker to jump to that hotspot's card.",
            prefer: "left"
        },
        {
            selector: "#theme-toggle",
            title: "Dark Mode",
            desc: "Toggle between light and dark themes.",
            prefer: "right"
        },
        {
            selector: "#tour-help-btn",
            title: "Need Help?",
            desc: "You can retake this tour any time by clicking this button.",
            prefer: "right"
        },
    ];

    let currentStep = 0;
    let currentHighlight = null;
    let mockResultsActive = false;

    const overlay = document.getElementById("walkthrough-overlay");
    const backdrop = document.getElementById("walkthrough-backdrop");
    const bubble = document.getElementById("walkthrough-bubble");
    const titleEl = document.getElementById("walkthrough-title");
    const descEl = document.getElementById("walkthrough-desc");
    const dotsEl = document.getElementById("walkthrough-dots");
    const btnsEl = document.getElementById("walkthrough-btns");

    function preventScroll(e) { e.preventDefault(); }
    function lockUserScroll() {
        window.addEventListener("wheel", preventScroll, { passive: false });
        window.addEventListener("touchmove", preventScroll, { passive: false });
    }
    function unlockUserScroll() {
        window.removeEventListener("wheel", preventScroll);
        window.removeEventListener("touchmove", preventScroll);
    }

    function startWalkthrough() {
        currentStep = 0;
        overlay.classList.add("open");
        lockUserScroll();
        showStep();
    }

    function cleanupMockResults() {
        if (!mockResultsActive) return;
        mockResultsActive = false;
        lastResultsData = null;
        itinerary = [];
        resultsMode = "explore";
        if (map) { map.remove(); map = null; }
        markers = {};
        clearRoute();
        document.getElementById("results").innerHTML =
            '<div class="empty-state"><p>Select your search parameters to get started.</p></div>';
    }

    function endWalkthrough() {
        overlay.classList.remove("open");
        unlockUserScroll();
        if (currentHighlight) {
            currentHighlight.classList.remove("walkthrough-target-highlight");
            currentHighlight = null;
        }
        cleanupMockResults();
        // Reset target mode back to defaults
        targetMode = "lifelist";
        lifelistSource = "fresh";
        document.querySelectorAll(".target-mode-btn").forEach(b => {
            b.classList.toggle("active", b.dataset.mode === "lifelist");
        });
        document.querySelectorAll(".lifelist-source-btn").forEach(b => {
            b.classList.toggle("active", b.dataset.source === "fresh");
        });
        document.getElementById("target-search-inputs").style.display = "none";
        document.getElementById("target-lifelist-inputs").style.display = "";
        document.getElementById("lifelist-upload-inputs").style.display = "none";
        document.getElementById("lifelist-fresh-inputs").style.display = "";
        updateOptimizeBtn();
        localStorage.setItem(STORAGE_KEY, "true");
    }

    function ensureMockResults() {
        if (mockResultsActive) return;
        // Only inject if there are no real results
        if (lastResultsData) return;
        mockResultsActive = true;
        renderResults(MOCK_DATA);
        // Disable exclude & recalculate links during tour
        document.querySelectorAll(".exclude-btn").forEach(btn => {
            btn.style.pointerEvents = "none";
            btn.style.opacity = "0.4";
        });
    }

    function showStep() {
        const step = steps[currentStep];

        // Remove previous highlight
        if (currentHighlight) {
            currentHighlight.classList.remove("walkthrough-target-highlight");
        }

        // Inject mock results when we reach the results steps
        if (currentStep >= FIRST_RESULTS_STEP) {
            ensureMockResults();
        } else if (mockResultsActive) {
            cleanupMockResults();
        }

        // Run step actions
        if (step.onEnter === "switchToSearch") {
            targetMode = "search";
            document.querySelectorAll(".target-mode-btn").forEach(b => {
                b.classList.toggle("active", b.dataset.mode === "search");
            });
            document.getElementById("target-search-inputs").style.display = "";
            document.getElementById("target-lifelist-inputs").style.display = "none";
        } else if (step.onEnter === "switchToLifelist") {
            targetMode = "lifelist";
            lifelistSource = "upload";
            document.querySelectorAll(".target-mode-btn").forEach(b => {
                b.classList.toggle("active", b.dataset.mode === "lifelist");
            });
            document.querySelectorAll(".lifelist-source-btn").forEach(b => {
                b.classList.toggle("active", b.dataset.source === "upload");
            });
            document.getElementById("target-search-inputs").style.display = "none";
            document.getElementById("target-lifelist-inputs").style.display = "";
            document.getElementById("lifelist-upload-inputs").style.display = "";
            document.getElementById("lifelist-fresh-inputs").style.display = "none";
        } else if (step.onEnter === "switchToFresh") {
            targetMode = "lifelist";
            lifelistSource = "fresh";
            document.querySelectorAll(".target-mode-btn").forEach(b => {
                b.classList.toggle("active", b.dataset.mode === "lifelist");
            });
            document.querySelectorAll(".lifelist-source-btn").forEach(b => {
                b.classList.toggle("active", b.dataset.source === "fresh");
            });
            document.getElementById("target-search-inputs").style.display = "none";
            document.getElementById("target-lifelist-inputs").style.display = "";
            document.getElementById("lifelist-upload-inputs").style.display = "none";
            document.getElementById("lifelist-fresh-inputs").style.display = "";
        } else if (step.onEnter === "switchToExplore" && mockResultsActive) {
            if (resultsMode !== "explore") {
                resultsMode = "explore";
                document.querySelectorAll(".results-mode-btn").forEach(b => {
                    b.classList.toggle("active", b.dataset.mode === "explore");
                });
                renderResultsView();
                // Re-disable exclude buttons
                document.querySelectorAll(".exclude-btn").forEach(btn => {
                    btn.style.pointerEvents = "none";
                    btn.style.opacity = "0.4";
                });
            }
        } else if (step.onEnter === "switchToPlan" && mockResultsActive) {
            // Ensure hotspots in itinerary for the plan view
            MOCK_DATA.hotspots.slice(0, 2).forEach(h => {
                if (!itinerary.some(it => it.locality_id === h.locality_id)) {
                    itinerary.push(h);
                }
            });
            resultsMode = "plan";
            document.querySelectorAll(".results-mode-btn").forEach(b => {
                b.classList.toggle("active", b.dataset.mode === "plan");
            });
            renderResultsView();
        }

        const target = document.querySelector(step.selector);

        if (!target) {
            // Skip missing elements
            if (currentStep < steps.length - 1) { currentStep++; showStep(); }
            else endWalkthrough();
            return;
        }

        // Scroll window so target is visible, then measure after paint settles
        const targetTop = target.getBoundingClientRect().top + window.scrollY;
        window.scrollTo({ top: Math.max(0, targetTop - window.innerHeight / 2 + target.offsetHeight / 2), behavior: "instant" });

        // Double rAF ensures scroll has fully applied before measuring
        requestAnimationFrame(() => { requestAnimationFrame(() => {
            // Highlight target
            target.classList.add("walkthrough-target-highlight");
            currentHighlight = target;

            // Cut out the target from the backdrop
            const rect = target.getBoundingClientRect();
            const pad = 8;
            const cx = rect.left + rect.width / 2;
            const cy = rect.top + rect.height / 2;
            const rx = rect.width / 2 + pad;
            const ry = rect.height / 2 + pad;
            backdrop.style.clipPath = `polygon(
                0% 0%, 100% 0%, 100% 100%, 0% 100%, 0% 0%,
                ${cx - rx}px ${cy - ry}px,
                ${cx - rx}px ${cy + ry}px,
                ${cx + rx}px ${cy + ry}px,
                ${cx + rx}px ${cy - ry}px,
                ${cx - rx}px ${cy - ry}px
            )`;

            // Position bubble
            positionBubble(rect, step.prefer, step.mobilePrefer);

            // Content
            titleEl.textContent = step.title;
            descEl.textContent = step.desc;

            // Dots
            dotsEl.innerHTML = steps.map((_, i) =>
                `<div class="walkthrough-dot${i === currentStep ? " active" : ""}"></div>`
            ).join("");

            // Buttons
            let btns = "";
            if (currentStep === 0) {
                btns += `<button class="walkthrough-btn skip" id="wt-skip">Skip</button>`;
                btns += `<button class="walkthrough-btn primary" id="wt-next">Next</button>`;
            } else if (currentStep === steps.length - 1) {
                btns += `<button class="walkthrough-btn secondary" id="wt-prev">Back</button>`;
                btns += `<button class="walkthrough-btn primary" id="wt-done">Done</button>`;
            } else {
                btns += `<button class="walkthrough-btn secondary" id="wt-prev">Back</button>`;
                btns += `<button class="walkthrough-btn primary" id="wt-next">Next</button>`;
            }
            btnsEl.innerHTML = btns;

            // Wire buttons
            const skipBtn = document.getElementById("wt-skip");
            const nextBtn = document.getElementById("wt-next");
            const prevBtn = document.getElementById("wt-prev");
            const doneBtn = document.getElementById("wt-done");
            if (skipBtn) skipBtn.addEventListener("click", endWalkthrough);
            if (nextBtn) nextBtn.addEventListener("click", () => { currentStep++; showStep(); });
            if (prevBtn) prevBtn.addEventListener("click", () => { currentStep--; showStep(); });
            if (doneBtn) doneBtn.addEventListener("click", endWalkthrough);
        }); });
    }

    function positionBubble(targetRect, prefer, mobilePrefer) {
        const gap = 14;
        const vw = window.innerWidth;
        const vh = window.innerHeight;
        const bw = Math.min(320, vw - 24);

        // On narrow screens, prefer vertical placement since there's no room beside
        if (vw <= 768) {
            prefer = mobilePrefer || ((vh - targetRect.bottom - gap) >= 150 ? "bottom" : "top");
        }

        // Reset
        bubble.style.maxWidth = bw + "px";

        // Measure bubble height (render offscreen briefly)
        bubble.style.visibility = "hidden";
        bubble.style.top = "0px";
        bubble.style.left = "0px";
        document.body.offsetHeight;
        const bh = bubble.offsetHeight;
        bubble.style.visibility = "";

        let top, left, arrow;

        if (prefer === "right" && targetRect.right + gap + bw < vw) {
            top = targetRect.top + targetRect.height / 2 - bh / 2;
            left = targetRect.right + gap;
            arrow = "left";
        } else if (prefer === "left" && targetRect.left - gap - bw > 0) {
            top = targetRect.top + targetRect.height / 2 - bh / 2;
            left = targetRect.left - gap - bw;
            arrow = "right";
        } else if (prefer === "bottom" && targetRect.bottom + gap + bh < vh) {
            top = targetRect.bottom + gap;
            left = targetRect.left + targetRect.width / 2 - bw / 2;
            arrow = "top";
        } else if (targetRect.top - gap - bh > 0) {
            top = targetRect.top - gap - bh;
            left = targetRect.left + targetRect.width / 2 - bw / 2;
            arrow = "bottom";
        } else {
            top = targetRect.bottom + gap;
            left = targetRect.left + targetRect.width / 2 - bw / 2;
            arrow = "top";
        }

        // Clamp to viewport
        top = Math.max(8, Math.min(top, vh - bh - 8));
        left = Math.max(8, Math.min(left, vw - bw - 8));

        bubble.style.top = top + "px";
        bubble.style.left = left + "px";
        bubble.setAttribute("data-arrow", arrow);
    }

    // Close button to dismiss
    document.getElementById("walkthrough-close").addEventListener("click", endWalkthrough);

    // Welcome dialog
    const welcomeOverlay = document.getElementById("walkthrough-welcome-overlay");
    const welcomeSkip = document.getElementById("walkthrough-welcome-skip");
    const welcomeStart = document.getElementById("walkthrough-welcome-start");

    function showWelcome() {
        welcomeOverlay.classList.add("open");
    }

    function dismissWelcome() {
        welcomeOverlay.classList.remove("open");
        localStorage.setItem(STORAGE_KEY, "true");
    }

    welcomeSkip.addEventListener("click", dismissWelcome);
    welcomeStart.addEventListener("click", () => {
        welcomeOverlay.classList.remove("open");
        startWalkthrough();
    });
    welcomeOverlay.addEventListener("click", (e) => {
        if (e.target === e.currentTarget) dismissWelcome();
    });

    // Help button — shows welcome dialog
    document.getElementById("tour-help-btn").addEventListener("click", showWelcome);

    // Auto-start on first visit — show welcome dialog (after beta wall is dismissed)
    if (!localStorage.getItem(STORAGE_KEY)) {
        if (localStorage.getItem("beta-name")) {
            setTimeout(showWelcome, 500);
        } else {
            betaForm.addEventListener("submit", () => setTimeout(showWelcome, 300));
        }
    }
})();

// Mobile sidebar toggle
(() => {
    const toggle = document.getElementById("sidebar-toggle");
    const sidebar = document.querySelector(".sidebar");
    const mq = window.matchMedia("(max-width: 768px)");

    function onMediaChange(e) {
        if (e.matches) {
            toggle.hidden = false;
        } else {
            toggle.hidden = true;
            sidebar.classList.remove("collapsed");
            sidebar.style.maxHeight = "";
        }
    }

    function setExpanded() {
        sidebar.classList.remove("collapsed");
        sidebar.style.maxHeight = sidebar.scrollHeight + "px";
        // After transition, remove inline max-height so content can resize naturally
        sidebar.addEventListener("transitionend", function handler() {
            if (!sidebar.classList.contains("collapsed")) sidebar.style.maxHeight = "";
            sidebar.removeEventListener("transitionend", handler);
        });
        toggle.querySelector(".sidebar-toggle-label").textContent = "Filters";
    }

    function setCollapsed() {
        sidebar.style.maxHeight = sidebar.scrollHeight + "px";
        // Force reflow then collapse
        sidebar.offsetHeight;
        sidebar.classList.add("collapsed");
        sidebar.style.maxHeight = "0";
        toggle.querySelector(".sidebar-toggle-label").textContent = "Filters";
    }

    toggle.addEventListener("click", () => {
        if (sidebar.classList.contains("collapsed")) {
            setExpanded();
        } else {
            setCollapsed();
        }
    });

    // Auto-collapse after search on mobile
    document.getElementById("optimize-btn").addEventListener("click", () => {
        if (mq.matches && !sidebar.classList.contains("collapsed")) {
            setTimeout(setCollapsed, 300);
        }
    });

    mq.addEventListener("change", onMediaChange);
    onMediaChange(mq);
})();
