/* =========================================================



   SKYTRACK - FLIGHT PRICE TRACKER



========================================================= */



const $ = id => document.getElementById(id);





let latestSearch = null;



let allFlightResults = [];
let currentTrackingFlight = null;
let airportCache = {};

function deduplicateFlights(flights) {
    if (!Array.isArray(flights)) return [];
    const seen = new Map();
    for (const flight of flights) {
        if (!flight) continue;
        const dep = String(flight.departure_airport || "").trim().toUpperCase();
        const arr = String(flight.arrival_airport || "").trim().toUpperCase();
        const airline = String(flight.airline || "").trim().toUpperCase();
        const fnum = String(flight.flight_number || (flight.segments?.[0]?.flight_number) || "").trim().toUpperCase();
        const dtime = String(flight.departure_time || "").trim();
        const atime = String(flight.arrival_time || "").trim();
        const stops = Number(flight.stops) || 0;
        const tripType = String(flight.trip_type || "").trim().toLowerCase();

        let segKey = "";
        if (Array.isArray(flight.segments) && flight.segments.length > 0) {
            segKey = flight.segments.map(s => `${s.flight_number || ''}:${s.departure_time || ''}`).join(",");
        }
        if (Array.isArray(flight.legs) && flight.legs.length > 0) {
            segKey += flight.legs.map(l => `${l.departure || l.departure_airport || ''}:${l.arrival || l.arrival_airport || ''}:${l.flight_number || ''}:${l.departure_time || ''}`).join(",");
        }
        let retKey = "";
        if (flight.return_departure_time || flight.return_flight_number) {
            retKey = `${flight.return_departure_time || ''}:${flight.return_arrival_time || ''}:${flight.return_flight_number || ''}`;
        }

        const scheduleKey = `${dep}_${arr}_${airline}_${fnum}_${dtime}_${atime}_${stops}_${tripType}_${segKey}_${retKey}`;

        if (seen.has(scheduleKey)) {
            const existing = seen.get(scheduleKey);
            const existingOffers = existing.comparison_offers || [];
            const incomingOffers = flight.comparison_offers || [];
            const seenVendors = new Set(existingOffers.map(o => String(o.vendor || o.provider || o.agent_name || o.provider_name || '').toLowerCase()));
            for (const offer of incomingOffers) {
                const vKey = String(offer.vendor || offer.provider || offer.agent_name || offer.provider_name || '').toLowerCase();
                if (!seenVendors.has(vKey)) {
                    seenVendors.add(vKey);
                    existingOffers.push(offer);
                }
            }
            existing.comparison_offers = existingOffers;
            const existingPrice = Number(existing.price);
            const incomingPrice = Number(flight.price);
            if (!isNaN(incomingPrice) && incomingPrice > 0) {
                if (isNaN(existingPrice) || incomingPrice < existingPrice) {
                    existing.price = incomingPrice;
                    if (flight.booking_url) existing.booking_url = flight.booking_url;
                }
            }
        } else {
            const copy = { ...flight };
            if (!Array.isArray(copy.comparison_offers)) {
                copy.comparison_offers = [];
            }
            seen.set(scheduleKey, copy);
        }
    }
    return Array.from(seen.values());
}

/* =========================================================
   TRACKED FLIGHTS AUTHORIZATION TOKENS (IDOR MITIGATION)
========================================================= */

function saveDeleteToken(id, token) {
    if (!id || !token) return;
    try {
        const tokens = JSON.parse(localStorage.getItem("skytracker_delete_tokens") || "{}");
        tokens[String(id)] = String(token);
        localStorage.setItem("skytracker_delete_tokens", JSON.stringify(tokens));
    } catch (e) {
        console.warn("Could not save delete token to localStorage", e);
    }
}

function getDeleteToken(id) {
    if (!id) return null;
    try {
        const tokens = JSON.parse(localStorage.getItem("skytracker_delete_tokens") || "{}");
        return tokens[String(id)] || null;
    } catch (e) {
        return null;
    }
}

function removeDeleteToken(id) {
    if (!id) return;
    try {
        const tokens = JSON.parse(localStorage.getItem("skytracker_delete_tokens") || "{}");
        delete tokens[String(id)];
        localStorage.setItem("skytracker_delete_tokens", JSON.stringify(tokens));
    } catch (e) {
        console.warn("Could not remove delete token from localStorage", e);
    }
}

/* =========================================================



   AIRPORT LOOKUP



========================================================= */



async function getAirportInfo(iata) {



    if (!iata) {

        return null;

    }



    const code = String(iata)

        .trim()

        .toUpperCase();



    if (!code) {

        return null;

    }



    // Reuse an existing request/result

    if (airportCache[code]) {

        return airportCache[code];

    }



    // Cache the Promise immediately so multiple flight cards

    // don't make duplicate requests for the same airport.

    airportCache[code] = fetch(

        `/api/airports?iata=${encodeURIComponent(code)}`

    )

        .then(response => {



            if (!response.ok) {

                return null;

            }



            return response.json();

        })

        .then(data => {



            if (

                !Array.isArray(data) ||

                data.length === 0

            ) {

                return null;

            }



            return data[0];

        })

        .catch(error => {



            console.error(

                "Airport lookup error:",

                error

            );



            return null;

        });



    return airportCache[code];

}





function formatAirport(airport, fallbackCode) {
    if (!airport) return fallbackCode || "";

    const name = airport.name || airport.airport_name || "";
    const city = airport.city || airport.municipality || airport.town || "";
    const country = airport.country || airport.iso_country || "";
    const code = airport.iata || airport.iata_code || fallbackCode || "";
    const place = [city, country].filter(Boolean).join(", ");

    if (name && place) return `${name} — ${place} (${code})`;
    if (name) return `${name} (${code})`;
    if (place) return `${place} (${code})`;
    return code;
}



/* =========================================================



   AIRPORT AUTOCOMPLETE



========================================================= */







function setupAirportAutocomplete(inputId, suggestionsId) {
    const input = typeof inputId === "string" ? $(inputId) : inputId;
    let suggestions = typeof suggestionsId === "string" ? $(suggestionsId) : suggestionsId;

    if (!suggestions && typeof suggestionsId === "string") {
        if (suggestionsId === "departureSuggestions") suggestions = $("departureAutocomplete");
        else if (suggestionsId === "departureAutocomplete") suggestions = $("departureSuggestions");
        else if (suggestionsId === "arrivalSuggestions") suggestions = $("arrivalAutocomplete");
        else if (suggestionsId === "arrivalAutocomplete") suggestions = $("arrivalSuggestions");
    }

    if (!input || !suggestions) {
        return;
    }

    const boundKey = `_ac_bound_${suggestions.id || "elem"}`;
    if (input[boundKey]) {
        return;
    }
    input[boundKey] = true;

    let timer = null;
    let selectedIndex = -1;

    function closeSuggestions() {
        suggestions.innerHTML = "";
        suggestions.style.display = "none";
        suggestions.classList.remove("active");
        selectedIndex = -1;
    }

    function selectOption(option) {
        if (!option) return;
        const iata = (option.dataset.iata || "").toUpperCase();
        const city = option.dataset.city || "";
        const name = option.dataset.name || option.querySelector("strong")?.textContent?.trim() || "";

        if (!iata) return;

        input.value = name && city
            ? `${name} — ${city} (${iata})`
            : name
                ? `${name} (${iata})`
                : city
                    ? `${city} (${iata})`
                    : iata;

        input.dataset.iata = iata;
        input.dataset.city = city;
        input.dataset.name = name;

        input.classList.remove("field-error");
        clearFormErrors();
        closeSuggestions();
    }

    input.addEventListener("input", () => {
        const query = input.value.trim();
        const match = query.match(/\(([A-Z]{3})\)/i) || (query.length === 3 ? query.match(/^([A-Z]{3})$/i) : null);
        if (match) {
            input.dataset.iata = match[1].toUpperCase();
        } else {
            input.dataset.iata = "";
            input.dataset.city = "";
            input.dataset.name = "";
        }

        clearTimeout(timer);
        if (query.length < 2) {
            closeSuggestions();
            return;
        }

        // Show immediate loading state while typing / fetching
        suggestions.innerHTML = `
            <div class="airport-loading-state font-mono" role="status" style="padding: 12px 16px; font-size: 0.8rem; color: var(--text-tertiary); display: flex; align-items: center; gap: 8px;">
                <span class="btn-loader" style="width: 14px; height: 14px; display: inline-block;"></span>
                <span>Searching airport database...</span>
            </div>
        `;
        suggestions.style.display = "block";
        suggestions.classList.add("active");

        timer = setTimeout(async () => {
            try {
                const response = await fetch(`/api/airports?q=${encodeURIComponent(query)}`);
                if (!response.ok) {
                    throw new Error("Airport search failed");
                }

                const airports = await response.json();
                selectedIndex = -1;

                if (!airports || !airports.length) {
                    suggestions.innerHTML = `
                        <div class="airport-no-result font-mono" role="status" style="padding: 12px 16px; font-size: 0.82rem; color: var(--text-tertiary);">
                            No matching airports found
                        </div>
                    `;
                    suggestions.style.display = "block";
                    suggestions.classList.add("active");
                    return;
                }

                suggestions.innerHTML = airports.map((airport, idx) => {
                    const airportName = airport.name || airport.airport_name || airport.municipality || "Airport";
                    const city = airport.city || airport.municipality || airport.city_name || "";
                    const country = airport.country || airport.iso_country || "";
                    const iata = (airport.iata || airport.iata_code || "").toUpperCase();

                    return `
                        <div
                            class="airport-option autocomplete-item"
                            data-iata="${escapeHtml(iata)}"
                            data-city="${escapeHtml(city)}"
                            data-name="${escapeHtml(airportName)}"
                            data-index="${idx}"
                            role="option"
                            tabindex="-1"
                        >
                            <div class="airport-option-main">
                                <span class="airport-icon">
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;display:inline-block;"><path d="M17.8 19.2 16 11l3.5-3.5C21 6 21.5 4 21 3c-1-.5-3 0-4.5 1.5L13 8 4.8 6.2c-.5-.1-.9.1-1.1.5l-.3.5c-.2.5-.1 1 .3 1.3L9 12l-2 3H4l-1 1 3 2 2 3 1-1v-3l3-2 3.5 5.3c.3.4.8.5 1.3.3l.5-.3c.4-.2.6-.6.5-1.1z"/></svg>
                                </span>
                                <div>
                                    <strong>${escapeHtml(airportName)}</strong>
                                    <small>${escapeHtml(city)}${country ? ` • ${escapeHtml(country)}` : ""}</small>
                                </div>
                            </div>
                            <strong class="airport-code">${escapeHtml(iata)}</strong>
                        </div>
                    `;
                }).join("");

                suggestions.style.display = "block";
                suggestions.classList.add("active");

                const options = suggestions.querySelectorAll(".airport-option");
                options.forEach(opt => {
                    opt.addEventListener("click", () => selectOption(opt));
                });
            } catch (error) {
                console.error("Airport autocomplete error:", error);
                suggestions.innerHTML = `
                    <div class="airport-error-state font-mono" role="status" style="padding: 12px 16px; font-size: 0.82rem; color: #f87171;">
                        Could not load airport suggestions. Please check network.
                    </div>
                `;
                suggestions.style.display = "block";
                suggestions.classList.add("active");
            }
        }, 200);
    });

    // Reopen suggestions if field has valid query when focused
    input.addEventListener("focus", () => {
        const query = input.value.trim();
        if (query.length >= 2 && (!suggestions.children.length || suggestions.style.display === "none")) {
            input.dispatchEvent(new Event("input"));
        }
    });

    // Keyboard accessibility: ArrowDown, ArrowUp, Enter, Escape
    input.addEventListener("keydown", e => {
        const isOpen = suggestions.style.display === "block" || suggestions.classList.contains("active");
        const options = suggestions.querySelectorAll(".airport-option");

        if (!isOpen || !options.length) {
            if (e.key === "Escape") {
                closeSuggestions();
            }
            return;
        }

        if (e.key === "ArrowDown") {
            e.preventDefault();
            selectedIndex = (selectedIndex + 1) % options.length;
            options.forEach((opt, i) => opt.classList.toggle("selected", i === selectedIndex));
            options[selectedIndex]?.scrollIntoView({ block: "nearest" });
        } else if (e.key === "ArrowUp") {
            e.preventDefault();
            selectedIndex = (selectedIndex - 1 + options.length) % options.length;
            options.forEach((opt, i) => opt.classList.toggle("selected", i === selectedIndex));
            options[selectedIndex]?.scrollIntoView({ block: "nearest" });
        } else if (e.key === "Enter") {
            if (selectedIndex >= 0 && options[selectedIndex]) {
                e.preventDefault();
                selectOption(options[selectedIndex]);
            } else if (options.length > 0) {
                e.preventDefault();
                selectOption(options[0]);
            }
        } else if (e.key === "Escape") {
            e.preventDefault();
            closeSuggestions();
        }
    });

    document.addEventListener("click", event => {
        if (!input.contains(event.target) && !suggestions.contains(event.target)) {
            closeSuggestions();
        }
    });
}

async function resolveAirportInput(inputEl, fieldLabel) {
    if (!inputEl) return null;
    const raw = (inputEl.value || "").trim();
    if (!raw) return null;

    // 1. Direct dataset check
    if (inputEl.dataset.iata && /^[A-Z]{3}$/i.test(inputEl.dataset.iata)) {
        return {
            iata: inputEl.dataset.iata.toUpperCase(),
            city: inputEl.dataset.city || "",
            name: inputEl.dataset.name || ""
        };
    }

    // 2. Parenthesized code: "Delhi (DEL)"
    const paren = raw.match(/\(([A-Za-z]{3})\)/);
    if (paren) {
        const code = paren[1].toUpperCase();
        inputEl.dataset.iata = code;
        return { iata: code, city: inputEl.dataset.city || "", name: inputEl.dataset.name || "" };
    }

    // 3. Exactly 3 letters: "del", "BOM"
    if (/^[A-Za-z]{3}$/.test(raw)) {
        const code = raw.toUpperCase();
        inputEl.dataset.iata = code;
        return { iata: code, city: "", name: "" };
    }

    // 4. Common historical / colloquial aliases
    const cleanKey = raw.toLowerCase().replace(/[^a-z0-9]/g, "");
    const aliasMap = {
        "bombay": "BOM", "mumbai": "BOM", "delhi": "DEL", "newdelhi": "DEL",
        "madras": "MAA", "chennai": "MAA", "calcutta": "CCU", "kolkata": "CCU",
        "bangalore": "BLR", "bengaluru": "BLR", "cochin": "COK", "kochi": "COK",
        "hyderabad": "HYD", "trivandrum": "TRV", "thiruvananthapuram": "TRV",
        "varanasi": "VNS", "banaras": "VNS", "benares": "VNS",
        "baroda": "BDQ", "vadodara": "BDQ", "poona": "PNQ", "pune": "PNQ",
        "london": "LHR", "newyork": "JFK", "paris": "CDG", "tokyo": "HND",
        "dubai": "DXB", "singapore": "SIN"
    };
    if (aliasMap[cleanKey]) {
        const code = aliasMap[cleanKey];
        inputEl.dataset.iata = code;
        return { iata: code, city: "", name: "" };
    }

    // 5. Look up via API with matching verification (never silently substitute)
    try {
        const resp = await fetch(`/api/airports?q=${encodeURIComponent(raw)}`);
        if (resp.ok) {
            const list = await resp.json();
            if (Array.isArray(list) && list.length > 0) {
                const queryLower = raw.toLowerCase();
                const matched = list.find(a => {
                    const c = (a.iata || a.iata_code || "").toLowerCase();
                    const ci = (a.city || a.municipality || "").toLowerCase();
                    const nm = (a.name || a.airport_name || "").toLowerCase();
                    return c === queryLower || ci === queryLower || nm === queryLower || ci.startsWith(queryLower) || nm.startsWith(queryLower);
                }) || (list.length === 1 ? list[0] : null);

                if (matched) {
                    const code = (matched.iata || matched.iata_code || "").toUpperCase();
                    if (code && code.length === 3) {
                        const name = matched.name || matched.airport_name || "";
                        const city = matched.city || matched.municipality || "";
                        inputEl.dataset.iata = code;
                        inputEl.dataset.city = city;
                        inputEl.dataset.name = name;
                        inputEl.value = name && city ? `${name} — ${city} (${code})` : (city ? `${city} (${code})` : `${name} (${code})`);
                        return { iata: code, city, name };
                    }
                }
            }
        }
    } catch (e) {
        console.warn("Airport resolution fetch failed:", e);
    }

    return null;
}

// Bind Autocomplete to both index.html and flights.html dropdowns
setupAirportAutocomplete("departure", "departureSuggestions");
setupAirportAutocomplete("departure", "departureAutocomplete");
setupAirportAutocomplete("arrival", "arrivalSuggestions");
setupAirportAutocomplete("arrival", "arrivalAutocomplete");

/* =========================================================
   AIRPORT SWAP IMPLEMENTATION
========================================================= */

function initAirportSwap() {
    const swapBtn = $("swapAirports") || $("swapAirportsBtn");
    const depInput = $("departure");
    const arrInput = $("arrival");
    const depSuggestions = $("departureSuggestions") || $("departureAutocomplete");
    const arrSuggestions = $("arrivalSuggestions") || $("arrivalAutocomplete");
    const nearbyDep = $("nearbyDeparture");
    const nearbyArr = $("nearbyArrival");

    if (!swapBtn || !depInput || !arrInput) return;

    function executeSwap(e) {
        if (e) {
            e.preventDefault();
            e.stopPropagation();
        }

        // Close any open autocomplete suggestions dropdowns
        [$("departureSuggestions"), $("departureAutocomplete"), $("arrivalSuggestions"), $("arrivalAutocomplete")].forEach(el => {
            if (el) {
                el.innerHTML = "";
                el.style.display = "none";
                el.classList.remove("active");
            }
        });

        // Read departure state
        const depVal = depInput.value;
        let depIata = depInput.dataset.iata || "";
        const depCity = depInput.dataset.city || "";
        const depName = depInput.dataset.name || "";
        if (!depIata) {
            const m = depVal.match(/\(([A-Z]{3})\)/i) || (depVal.trim().length === 3 ? depVal.trim().match(/^([A-Z]{3})$/i) : null);
            if (m) depIata = m[1].toUpperCase();
        }

        // Read arrival state
        const arrVal = arrInput.value;
        let arrIata = arrInput.dataset.iata || "";
        const arrCity = arrInput.dataset.city || "";
        const arrName = arrInput.dataset.name || "";
        if (!arrIata) {
            const m = arrVal.match(/\(([A-Z]{3})\)/i) || (arrVal.trim().length === 3 ? arrVal.trim().match(/^([A-Z]{3})$/i) : null);
            if (m) arrIata = m[1].toUpperCase();
        }

        // If both fields are blank, do nothing
        if (!depVal && !arrVal && !depIata && !arrIata) return;

        // Exchange values directly without triggering input event
        depInput.value = arrVal;
        arrInput.value = depVal;

        // Exchange dataset attributes
        depInput.dataset.iata = arrIata;
        depInput.dataset.city = arrCity;
        depInput.dataset.name = arrName;

        arrInput.dataset.iata = depIata;
        arrInput.dataset.city = depCity;
        arrInput.dataset.name = depName;

        // Exchange nearby toggles if present
        if (nearbyDep && nearbyArr) {
            const tempChecked = nearbyDep.checked;
            nearbyDep.checked = nearbyArr.checked;
            nearbyArr.checked = tempChecked;
        }

        // Smooth rotation feedback
        const allSwapBtns = [$("swapAirports"), $("swapAirportsBtn")].filter(Boolean);
        allSwapBtns.forEach(btn => {
            const svg = btn.querySelector("svg");
            if (svg) {
                const currentRot = parseInt(svg.dataset.rotation || "0", 10) + 180;
                svg.dataset.rotation = currentRot;
                svg.style.transition = "transform 0.4s cubic-bezier(0.34, 1.56, 0.64, 1)";
                svg.style.transform = `rotate(${currentRot}deg)`;
            }
        });

        // Clear error styling
        depInput.classList.remove("field-error");
        arrInput.classList.remove("field-error");
        clearFormErrors();
    }

    const swapBtns = [$("swapAirports"), $("swapAirportsBtn")].filter(Boolean);
    swapBtns.forEach(btn => {
        btn.addEventListener("click", executeSwap);
        btn.addEventListener("keydown", e => {
            if (e.key === "Enter" || e.key === " ") {
                executeSwap(e);
            }
        });
    });
}

initAirportSwap();

/* =========================================================
   ADVANCED SEARCH FORM CONTROLS & TRIP TYPES
========================================================= */

let currentTripType = "round_trip";
const passengerCounts = { adults: 1, children: 0, infants: 0 };
let multiCityLegs = [
    { id: 1, depIata: "", depVal: "", arrIata: "", arrVal: "", date: "" },
    { id: 2, depIata: "", depVal: "", arrIata: "", arrVal: "", date: "" }
];
let legCounter = 2;

function initTripTypeSelector() {
    const roundBtn = $("btnRoundTrip");
    const oneWayBtn = $("btnOneWay");
    const multiCityBtn = $("btnMultiCity");
    const tabBtns = document.querySelectorAll(".trip-tab-btn");

    const stdInputs = $("standardFlightInputs") || $("standardTripFields");
    const multiContainer = $("multiCityContainer") || $("multiCityFields");
    const returnFieldGroup = $("returnDateFieldGroup") || $("returnDateWrap");
    const returnInput = $("return_date");
    const tripTypeInput = $("tripTypeInput");

    function setTripType(type) {
        currentTripType = type;
        if (tripTypeInput) {
            tripTypeInput.value = type;
        }

        // Clear stale validation state when switching tabs
        clearFormErrors();
        if (returnInput) {
            returnInput.classList.remove("field-error");
        }

        if (roundBtn) roundBtn.classList.toggle("active", type === "round_trip");
        if (oneWayBtn) oneWayBtn.classList.toggle("active", type === "one_way");
        if (multiCityBtn) multiCityBtn.classList.toggle("active", type === "multi_city");

        tabBtns.forEach(tab => {
            const dt = tab.dataset.type;
            const isMatch = (type === "one_way" && dt === "one-way") ||
                            (type === "round_trip" && dt === "round-trip") ||
                            (type === "multi_city" && dt === "multi-city");
            tab.classList.toggle("active", isMatch);
            tab.setAttribute("aria-selected", String(isMatch));
        });

        const depInput = $("departure");
        const arrInput = $("arrival");
        const outDateInput = $("outbound_date");

        if (type === "multi_city") {
            if (stdInputs) stdInputs.style.display = "none";
            if (multiContainer) multiContainer.style.display = "block";
            if (returnFieldGroup) returnFieldGroup.style.display = "none";
            if (returnInput) {
                returnInput.required = false;
                returnInput.value = "";
            }
            if (depInput) depInput.required = false;
            if (arrInput) arrInput.required = false;
            if (outDateInput) outDateInput.required = false;
        } else if (type === "one_way") {
            if (stdInputs) stdInputs.style.display = "";
            if (multiContainer) multiContainer.style.display = "none";
            if (returnFieldGroup) returnFieldGroup.style.display = "none";
            if (returnInput) {
                returnInput.required = false;
                returnInput.value = "";
            }
            if (depInput) depInput.required = true;
            if (arrInput) arrInput.required = true;
            if (outDateInput) outDateInput.required = true;
        } else {
            // round_trip
            if (stdInputs) stdInputs.style.display = "";
            if (multiContainer) multiContainer.style.display = "none";
            if (returnFieldGroup) returnFieldGroup.style.display = "";
            if (returnInput) returnInput.required = true;
            if (depInput) depInput.required = true;
            if (arrInput) arrInput.required = true;
            if (outDateInput) outDateInput.required = true;
        }
    }

    if (roundBtn) roundBtn.addEventListener("click", () => setTripType("round_trip"));
    if (oneWayBtn) oneWayBtn.addEventListener("click", () => setTripType("one_way"));
    if (multiCityBtn) multiCityBtn.addEventListener("click", () => setTripType("multi_city"));

    tabBtns.forEach(tab => {
        tab.addEventListener("click", () => {
            const dt = tab.dataset.type;
            if (dt === "one-way") setTripType("one_way");
            else if (dt === "round-trip") setTripType("round_trip");
            else if (dt === "multi-city") setTripType("multi_city");
        });
    });

    // Detect initial trip type from DOM (e.g. flights.html has one-way active by default, index.html has round-trip)
    const activeTabEl = document.querySelector(".trip-tab-btn.active, .segment-btn.active, [data-trip].active, [data-type].active");
    let initialType = "round_trip";
    if (activeTabEl) {
        const dt = activeTabEl.dataset.type || activeTabEl.dataset.trip;
        if (dt === "one-way" || dt === "one_way") initialType = "one_way";
        else if (dt === "multi-city" || dt === "multi_city") initialType = "multi_city";
        else if (dt === "round-trip" || dt === "round_trip") initialType = "round_trip";
    } else if (tripTypeInput && tripTypeInput.value) {
        initialType = tripTypeInput.value;
    } else if ($("returnDateWrap") && ($("returnDateWrap").style.display === "none" || !$("returnDateFieldGroup"))) {
        initialType = "one_way";
    }
    setTripType(initialType);
    window.setTripType = setTripType;
}

function initPassengerSelector() {
    // index.html elements
    const triggerBtn = $("passengerTriggerBtn");
    const popover = $("passengerPopover");
    const doneBtn = $("btnPassengerDone");

    const btnAdultMinus = $("btnAdultMinus");
    const btnAdultPlus = $("btnAdultPlus");
    const adultDisplay = $("adultCountDisplay");

    const btnChildMinus = $("btnChildMinus");
    const btnChildPlus = $("btnChildPlus");
    const childDisplay = $("childCountDisplay");

    const btnInfantMinus = $("btnInfantMinus");
    const btnInfantPlus = $("btnInfantPlus");
    const infantDisplay = $("infantCountDisplay");

    // flights.html elements
    const summaryBtnFlights = $("passengerSummaryBtn");
    const dropdownFlights = $("passengerDropdown");
    const applyBtnFlights = $("applyPassengersBtn");
    const cabinSelect = $("cabinClassSelect");
    const adultsCountEl = $("adultsCount");
    const childrenCountEl = $("childrenCount");
    const infantsCountEl = $("infantsCount");

    function getCabinLabel() {
        const val = cabinSelect ? cabinSelect.value : ($("travelClass") ? $("travelClass").value : "1");
        if (val === "2" || val === "premium_economy") return "Premium Economy";
        if (val === "3" || val === "business") return "Business";
        if (val === "4" || val === "first") return "First";
        return "Economy";
    }

    function updatePassengerUI() {
        const total = passengerCounts.adults + passengerCounts.children + passengerCounts.infants;

        if (adultDisplay) adultDisplay.textContent = passengerCounts.adults;
        if (childDisplay) childDisplay.textContent = passengerCounts.children;
        if (infantDisplay) infantDisplay.textContent = passengerCounts.infants;

        if (adultsCountEl) adultsCountEl.textContent = passengerCounts.adults;
        if (childrenCountEl) childrenCountEl.textContent = passengerCounts.children;
        if (infantsCountEl) infantsCountEl.textContent = passengerCounts.infants;

        if (btnAdultMinus) btnAdultMinus.disabled = passengerCounts.adults <= 1;
        if (btnAdultPlus) btnAdultPlus.disabled = total >= 9;

        if (btnChildMinus) btnChildMinus.disabled = passengerCounts.children <= 0;
        if (btnChildPlus) btnChildPlus.disabled = total >= 9;

        if (btnInfantMinus) btnInfantMinus.disabled = passengerCounts.infants <= 0;
        if (btnInfantPlus) btnInfantPlus.disabled = passengerCounts.infants >= passengerCounts.adults || total >= 9;

        const parts = [];
        parts.push(`${passengerCounts.adults} Adult${passengerCounts.adults > 1 ? "s" : ""}`);
        if (passengerCounts.children > 0) {
            parts.push(`${passengerCounts.children} Child${passengerCounts.children > 1 ? "ren" : ""}`);
        }
        if (passengerCounts.infants > 0) {
            parts.push(`${passengerCounts.infants} Infant${passengerCounts.infants > 1 ? "s" : ""}`);
        }

        const cabinLabel = getCabinLabel();
        const fullSummary = `${parts.join(", ")} · ${cabinLabel}`;

        document.querySelectorAll("#passengerSummaryText").forEach(el => {
            el.textContent = fullSummary;
        });
    }

    function changeCount(type, delta) {
        const total = passengerCounts.adults + passengerCounts.children + passengerCounts.infants;
        if (type === "adults") {
            const next = passengerCounts.adults + delta;
            if (next >= 1 && (delta < 0 || total < 9)) {
                passengerCounts.adults = next;
                if (passengerCounts.infants > passengerCounts.adults) {
                    passengerCounts.infants = passengerCounts.adults;
                }
            }
        } else if (type === "children") {
            const next = passengerCounts.children + delta;
            if (next >= 0 && (delta < 0 || total < 9)) {
                passengerCounts.children = next;
            }
        } else if (type === "infants") {
            const next = passengerCounts.infants + delta;
            if (next >= 0 && (delta < 0 || (total < 9 && next <= passengerCounts.adults))) {
                passengerCounts.infants = next;
            }
        }
        updatePassengerUI();
    }

    if (btnAdultMinus) btnAdultMinus.addEventListener("click", () => changeCount("adults", -1));
    if (btnAdultPlus) btnAdultPlus.addEventListener("click", () => changeCount("adults", 1));
    if (btnChildMinus) btnChildMinus.addEventListener("click", () => changeCount("children", -1));
    if (btnChildPlus) btnChildPlus.addEventListener("click", () => changeCount("children", 1));
    if (btnInfantMinus) btnInfantMinus.addEventListener("click", () => changeCount("infants", -1));
    if (btnInfantPlus) btnInfantPlus.addEventListener("click", () => changeCount("infants", 1));

    document.querySelectorAll(".btn-counter").forEach(btn => {
        btn.addEventListener("click", () => {
            const action = btn.dataset.action;
            const target = btn.dataset.target;
            const delta = action === "increase" ? 1 : -1;
            if (target === "adultsCount") changeCount("adults", delta);
            else if (target === "childrenCount") changeCount("children", delta);
            else if (target === "infantsCount") changeCount("infants", delta);
        });
    });

    if (cabinSelect) {
        cabinSelect.addEventListener("change", updatePassengerUI);
    }
    if ($("travelClass")) {
        $("travelClass").addEventListener("change", updatePassengerUI);
    }

    // Popover on index.html
    if (triggerBtn && popover) {
        triggerBtn.addEventListener("click", e => {
            e.stopPropagation();
            const isOpen = popover.style.display === "flex";
            popover.style.display = isOpen ? "none" : "flex";
            triggerBtn.classList.toggle("active", !isOpen);
            triggerBtn.setAttribute("aria-expanded", String(!isOpen));
        });

        if (doneBtn) {
            doneBtn.addEventListener("click", () => {
                popover.style.display = "none";
                triggerBtn.classList.remove("active");
                triggerBtn.setAttribute("aria-expanded", "false");
            });
        }

        document.addEventListener("click", e => {
            if (popover.style.display === "flex" && !popover.contains(e.target) && !triggerBtn.contains(e.target)) {
                popover.style.display = "none";
                triggerBtn.classList.remove("active");
                triggerBtn.setAttribute("aria-expanded", "false");
            }
        });
    }

    // Dropdown on flights.html
    if (summaryBtnFlights && dropdownFlights) {
        summaryBtnFlights.addEventListener("click", e => {
            e.stopPropagation();
            const isOpen = dropdownFlights.style.display === "block";
            dropdownFlights.style.display = isOpen ? "none" : "block";
            dropdownFlights.setAttribute("aria-hidden", String(isOpen));
            summaryBtnFlights.setAttribute("aria-expanded", String(!isOpen));
        });

        if (applyBtnFlights) {
            applyBtnFlights.addEventListener("click", () => {
                dropdownFlights.style.display = "none";
                dropdownFlights.setAttribute("aria-hidden", "true");
                summaryBtnFlights.setAttribute("aria-expanded", "false");
            });
        }

        document.addEventListener("click", e => {
            if (dropdownFlights.style.display === "block" && !dropdownFlights.contains(e.target) && !summaryBtnFlights.contains(e.target)) {
                dropdownFlights.style.display = "none";
                dropdownFlights.setAttribute("aria-hidden", "true");
                summaryBtnFlights.setAttribute("aria-expanded", "false");
            }
        });
    }

    updatePassengerUI();
}

function initDatePickers() {
    const today = new Date().toISOString().split("T")[0];
    const depDate = $("outbound_date");
    const retDate = $("return_date");

    if (depDate) {
        depDate.min = today;
        depDate.addEventListener("change", () => {
            if (retDate) {
                retDate.min = depDate.value || today;
                if (retDate.value && retDate.value < depDate.value) {
                    retDate.value = depDate.value;
                }
            }
            if (typeof markResultsAsStale === "function") markResultsAsStale();
        });
        depDate.addEventListener("input", () => {
            if (typeof markResultsAsStale === "function") markResultsAsStale();
        });
    }

    if (retDate) {
        retDate.min = depDate && depDate.value ? depDate.value : today;
        retDate.addEventListener("change", () => {
            if (typeof markResultsAsStale === "function") markResultsAsStale();
        });
        retDate.addEventListener("input", () => {
            if (typeof markResultsAsStale === "function") markResultsAsStale();
        });
    }

    // Ensure calendar icon clicks trigger native/supported date picker on all date fields (including dynamic multi-city legs)
    document.querySelectorAll(".date-input-wrapper .input-icon").forEach(icon => {
        icon.style.cursor = "pointer";
        icon.setAttribute("role", "button");
        icon.setAttribute("aria-label", "Open calendar date picker");
    });
    if (!document.__skytrackerCalendarDelegated) {
        document.__skytrackerCalendarDelegated = true;
        document.addEventListener("click", (e) => {
            const icon = e.target.closest(".date-input-wrapper .input-icon, .input-with-icon .field-icon");
            if (!icon) return;
            const wrap = icon.closest(".date-input-wrapper, .field-group, .input-with-icon");
            const dateInput = wrap ? wrap.querySelector("input[type='date']") : null;
            if (dateInput) {
                e.preventDefault();
                dateInput.focus();
                if (typeof dateInput.showPicker === "function") {
                    try { dateInput.showPicker(); } catch (err) {}
                }
            }
        });
    }
}

function renderMultiCityLegs() {
    const list = $("multiCityLegsList") || $("multiCityLegsContainer");
    const countBadges = document.querySelectorAll("#multiCityLegCountBadge, .multi-city-count-badge");
    const addBtns = document.querySelectorAll("#btnAddLeg, #addLegBtn");
    if (!list) return;

    countBadges.forEach(badge => {
        badge.textContent = `${multiCityLegs.length} of 5 legs`;
    });
    addBtns.forEach(btn => {
        btn.disabled = multiCityLegs.length >= 5;
    });

    const todayStr = new Date().toISOString().split("T")[0];

    list.innerHTML = multiCityLegs.map((leg, index) => {
        const legNum = index + 1;
        const canRemove = multiCityLegs.length > 2;
        const prevLegDate = index > 0 && multiCityLegs[index - 1].date ? multiCityLegs[index - 1].date : todayStr;
        const minDate = prevLegDate > todayStr ? prevLegDate : todayStr;

        return `
            <div class="multi-city-leg-row" data-index="${index}" id="multiCityLegRow_${leg.id}">
                <div class="leg-top-bar">
                    <span class="leg-badge">
                        <span class="leg-badge-dot"></span>
                        Flight ${legNum}
                    </span>
                    <button type="button" class="leg-remove-btn" data-index="${index}" title="Remove this flight leg" ${!canRemove ? "disabled" : ""}>
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
                            <line x1="18" y1="6" x2="6" y2="18"></line>
                            <line x1="6" y1="6" x2="18" y2="18"></line>
                        </svg>
                    </button>
                </div>
                <div class="leg-error-banner" id="mc_leg_error_${leg.id}" style="display:none;" role="alert"></div>
                <div class="leg-grid">
                    <div class="field-group">
                        <label for="mc_dep_${leg.id}" class="field-label">From</label>
                        <div class="airport-input-wrapper">
                            <span class="input-icon">
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2a8 8 0 0 0-8 8c0 5.25 8 12 8 12s8-6.75 8-12a8 8 0 0 0-8-8z"></path><circle cx="12" cy="10" r="3"></circle></svg>
                            </span>
                            <input id="mc_dep_${leg.id}" type="text" placeholder="Enter origin city or airport" autocomplete="off" value="${escapeHtml(leg.depVal || '')}" data-iata="${escapeHtml(leg.depIata || '')}">
                            <div id="mc_dep_sugg_${leg.id}" class="airport-suggestions" role="listbox"></div>
                        </div>
                    </div>

                    <button type="button" class="leg-swap-btn" data-index="${index}" title="Swap origin and destination for Flight ${legNum}">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M4 17h16"></path><path d="m16 21 4-4-4-4"></path><path d="M20 7H4"></path><path d="m8 3-4 4 4 4"></path>
                        </svg>
                    </button>

                    <div class="field-group">
                        <label for="mc_arr_${leg.id}" class="field-label">To</label>
                        <div class="airport-input-wrapper">
                            <span class="input-icon">
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><polygon points="12 8 8 12 12 16 12 8"></polygon><line x1="16" y1="12" x2="8" y2="12"></line></svg>
                            </span>
                            <input id="mc_arr_${leg.id}" type="text" placeholder="Enter destination city or airport" autocomplete="off" value="${escapeHtml(leg.arrVal || '')}" data-iata="${escapeHtml(leg.arrIata || '')}">
                            <div id="mc_arr_sugg_${leg.id}" class="airport-suggestions" role="listbox"></div>
                        </div>
                    </div>

                    <div class="field-group">
                        <label for="mc_date_${leg.id}" class="field-label">Departure</label>
                        <div class="date-input-wrapper">
                            <span class="input-icon">
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect><line x1="16" y1="2" x2="16" y2="6"></line><line x1="8" y1="2" x2="8" y2="6"></line><line x1="3" y1="10" x2="21" y2="10"></line></svg>
                            </span>
                            <input id="mc_date_${leg.id}" type="date" placeholder="Select departure date" value="${escapeHtml(leg.date || '')}" min="${minDate}">
                        </div>
                    </div>
                </div>
            </div>
        `;
    }).join("");

    multiCityLegs.forEach((leg, index) => {
        setupAirportAutocomplete(`mc_dep_${leg.id}`, `mc_dep_sugg_${leg.id}`);
        setupAirportAutocomplete(`mc_arr_${leg.id}`, `mc_arr_sugg_${leg.id}`);

        const depInp = $(`mc_dep_${leg.id}`);
        const arrInp = $(`mc_arr_${leg.id}`);
        const dateInp = $(`mc_date_${leg.id}`);

        if (depInp) {
            depInp.addEventListener("input", () => {
                leg.depVal = depInp.value;
                leg.depIata = depInp.dataset.iata || "";
                depInp.classList.remove("field-error");
                const err = $(`mc_leg_error_${leg.id}`);
                if (err) err.style.display = "none";
                if (typeof markResultsAsStale === "function") markResultsAsStale();
            });
        }
        if (arrInp) {
            arrInp.addEventListener("input", () => {
                leg.arrVal = arrInp.value;
                leg.arrIata = arrInp.dataset.iata || "";
                arrInp.classList.remove("field-error");
                const err = $(`mc_leg_error_${leg.id}`);
                if (err) err.style.display = "none";
                if (typeof markResultsAsStale === "function") markResultsAsStale();
            });
        }
        if (dateInp) {
            dateInp.addEventListener("change", () => {
                leg.date = dateInp.value;
                dateInp.classList.remove("field-error");
                const err = $(`mc_leg_error_${leg.id}`);
                if (err) err.style.display = "none";
                if (typeof markResultsAsStale === "function") markResultsAsStale();
                for (let next = index + 1; next < multiCityLegs.length; next++) {
                    const nextDateEl = $(`mc_date_${multiCityLegs[next].id}`);
                    if (nextDateEl) {
                        nextDateEl.min = leg.date;
                        if (nextDateEl.value && nextDateEl.value < leg.date) {
                            nextDateEl.value = leg.date;
                            multiCityLegs[next].date = leg.date;
                        }
                    }
                }
            });
        }
    });

    list.querySelectorAll(".leg-swap-btn").forEach(btn => {
        btn.addEventListener("click", e => {
            e.preventDefault();
            const idx = parseInt(btn.dataset.index, 10);
            swapMultiCityLeg(idx, btn);
        });
    });

    list.querySelectorAll(".leg-remove-btn").forEach(btn => {
        btn.addEventListener("click", e => {
            e.preventDefault();
            const idx = parseInt(btn.dataset.index, 10);
            removeMultiCityLeg(idx);
        });
    });
}

function syncMultiCityStateFromDOM() {
    multiCityLegs.forEach(leg => {
        const depEl = $(`mc_dep_${leg.id}`);
        const arrEl = $(`mc_arr_${leg.id}`);
        const dateEl = $(`mc_date_${leg.id}`);
        if (depEl) {
            leg.depVal = depEl.value;
            leg.depIata = depEl.dataset.iata || "";
            if (!leg.depIata) {
                const m = depEl.value.match(/\(([A-Z]{3})\)/i) || (depEl.value.trim().length === 3 ? depEl.value.trim().match(/^([A-Z]{3})$/i) : null);
                if (m) leg.depIata = m[1].toUpperCase();
            }
        }
        if (arrEl) {
            leg.arrVal = arrEl.value;
            leg.arrIata = arrEl.dataset.iata || "";
            if (!leg.arrIata) {
                const m = arrEl.value.match(/\(([A-Z]{3})\)/i) || (arrEl.value.trim().length === 3 ? arrEl.value.trim().match(/^([A-Z]{3})$/i) : null);
                if (m) leg.arrIata = m[1].toUpperCase();
            }
        }
        if (dateEl) {
            leg.date = dateEl.value;
        }
    });
}

function addMultiCityLeg() {
    if (multiCityLegs.length >= 5) return;
    syncMultiCityStateFromDOM();

    legCounter++;
    const newLeg = {
        id: legCounter,
        depIata: "",
        depVal: "",
        arrIata: "",
        arrVal: "",
        date: ""
    };
    multiCityLegs.push(newLeg);
    renderMultiCityLegs();
}

function removeMultiCityLeg(index) {
    if (multiCityLegs.length <= 2) return;
    syncMultiCityStateFromDOM();
    multiCityLegs.splice(index, 1);
    renderMultiCityLegs();
}

function swapMultiCityLeg(index, swapBtn) {
    syncMultiCityStateFromDOM();
    const leg = multiCityLegs[index];
    if (!leg) return;

    const tempVal = leg.depVal;
    const tempIata = leg.depIata;
    leg.depVal = leg.arrVal;
    leg.depIata = leg.arrIata;
    leg.arrVal = tempVal;
    leg.arrIata = tempIata;

    const depEl = $(`mc_dep_${leg.id}`);
    const arrEl = $(`mc_arr_${leg.id}`);
    if (depEl && arrEl) {
        depEl.value = leg.depVal;
        depEl.dataset.iata = leg.depIata;
        arrEl.value = leg.arrVal;
        arrEl.dataset.iata = leg.arrIata;

        depEl.classList.remove("input-highlight-pulse");
        arrEl.classList.remove("input-highlight-pulse");
        void depEl.offsetWidth;
        depEl.classList.add("input-highlight-pulse");
        arrEl.classList.add("input-highlight-pulse");
    }

    if (swapBtn) {
        const svg = swapBtn.querySelector("svg");
        if (svg) {
            const rot = parseInt(svg.dataset.rotation || "0", 10) + 180;
            svg.dataset.rotation = rot;
            svg.style.transition = "transform 0.4s cubic-bezier(0.34, 1.56, 0.64, 1)";
            svg.style.transform = `rotate(${rot}deg)`;
        }
    }
    if (typeof markResultsAsStale === "function") markResultsAsStale();
}

function initMultiCity() {
    const addBtns = document.querySelectorAll("#btnAddLeg, #addLegBtn");
    addBtns.forEach(btn => {
        btn.addEventListener("click", addMultiCityLeg);
    });
    renderMultiCityLegs();
}

// Initialize all controls
initTripTypeSelector();
initPassengerSelector();
initDatePickers();
initMultiCity();
if (typeof attachStaleDetectionListeners === "function") attachStaleDetectionListeners();












/* =========================================================



   SEARCH FLIGHTS



========================================================= */







let isSearching = false;
let activeSearchRequestId = 0;

function clearFlightResults(options = {}) {
    const { clearInputs = false } = options;
    allFlightResults = [];
    latestSearch = null;
    currentSearchContext = null;

    const resultsContainer = $("flightsList") || $("results");
    if (resultsContainer) {
        resultsContainer.innerHTML = "";
    }

    const emptyPrompt = $("emptySearchPrompt");
    if (emptyPrompt) emptyPrompt.style.display = "block";

    const filterBar = $("flightFilterBar") || $("flightFilters");
    if (filterBar) filterBar.style.display = "none";

    const compSection = $("comparison-section");
    if (compSection) compSection.style.display = "none";

    const navCompare = $("navCompareLink");
    if (navCompare) navCompare.style.display = "none";

    const statusStrip = $("flightStatusStrip");
    if (statusStrip) {
        statusStrip.style.display = "none";
        statusStrip.classList.remove("is-stale");
    }

    const resultSummary = $("resultSummary");
    if (resultSummary) resultSummary.textContent = "";

    const resultsCountLabel = $("resultsCountLabel");
    if (resultsCountLabel) resultsCountLabel.textContent = "";

    document.querySelectorAll(".stale-results-notice").forEach(el => el.remove());
}

function markResultsAsStale() {
    if (!allFlightResults || allFlightResults.length === 0) return;

    const strip = $("flightStatusStrip");
    if (strip && strip.style.display !== "none") {
        strip.classList.add("is-stale");
        const observedEl = $("flightStatusObserved");
        if (observedEl && !observedEl.textContent.includes("(Modified)")) {
            observedEl.textContent += " (Modified)";
        }
    }

    const resultsContainer = $("flightsList") || $("results");
    if (resultsContainer && !$("staleResultsNotice")) {
        const notice = document.createElement("div");
        notice.id = "staleResultsNotice";
        notice.className = "stale-results-notice";
        notice.setAttribute("role", "status");
        notice.innerHTML = `
            <span class="stale-notice-icon">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>
            </span>
            <span>Search parameters modified. Previous search results shown below — click <strong>Search</strong> to refresh.</span>
        `;
        resultsContainer.prepend(notice);
    }
}

function updateFlightStatusStrip(searchContext, flights, data = {}) {
    const strip = $("flightStatusStrip");
    if (!strip) return;

    if (!searchContext || !Array.isArray(flights) || flights.length === 0) {
        strip.style.display = "none";
        strip.classList.remove("is-stale");
        return;
    }

    const routeEl = $("flightStatusRoute");
    const metaEl = $("flightStatusMeta");
    const providerEl = $("flightStatusProvider");
    const observedEl = $("flightStatusObserved");
    const cacheEl = $("flightStatusCache");

    if (routeEl) {
        if (searchContext.tripType === "multi_city" && Array.isArray(searchContext.legs)) {
            const stops = searchContext.legs.map(l => l.departure);
            if (searchContext.legs.length > 0) {
                stops.push(searchContext.legs[searchContext.legs.length - 1].arrival);
            }
            routeEl.textContent = stops.join(" → ");
        } else if (searchContext.tripType === "round_trip" || searchContext.returnDate) {
            routeEl.textContent = `${searchContext.departureCode} ⇄ ${searchContext.arrivalCode}`;
        } else {
            routeEl.textContent = `${searchContext.departureCode} → ${searchContext.arrivalCode}`;
        }
    }

    if (metaEl) {
        const pax = searchContext.passengers || 1;
        const classNames = { "1": "Economy", "2": "Premium Economy", "3": "Business", "4": "First" };
        const cName = classNames[String(searchContext.travelClass)] || "Economy";
        metaEl.textContent = `${pax} Traveler${pax > 1 ? "s" : ""} • ${cName}`;
    }

    const minPrice = flights.reduce((min, f) => {
        const p = Number(f.price || f.fare || 0);
        return p > 0 && (min === 0 || p < min) ? p : min;
    }, 0);

    if (observedEl) {
        if (minPrice > 0) {
            observedEl.textContent = `Lowest Fare: ₹${minPrice.toLocaleString("en-IN")}`;
        } else {
            observedEl.textContent = `Itineraries: ${flights.length}`;
        }
    }

    if (providerEl) {
        const provName = data.provider_name || data.provider || flights[0]?.provider || flights[0]?.source || "Verified Source";
        providerEl.textContent = provName;
    }

    if (cacheEl) {
        if (data.from_cache || data.cached) {
            const timeStr = data.cached_at ? new Date(data.cached_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : "Recently";
            cacheEl.textContent = `Cached (${timeStr})`;
        } else {
            cacheEl.textContent = "Live Provider Response";
        }
    }

    strip.classList.remove("is-stale");
    strip.style.display = "block";
}

function showFormError(msg, targetEl, legId) {
    isSearching = false;
    clearFlightResults({ clearInputs: false });

    const submitBtns = document.querySelectorAll("#searchSubmitBtn, .search-launch-btn, .search-submit-btn");
    submitBtns.forEach(btn => {
        btn.disabled = false;
        btn.classList.remove("loading");
    });
    const spinners = document.querySelectorAll("#searchBtnSpinner, .btn-loader");
    spinners.forEach(s => s.style.display = "none");
    const btnTexts = document.querySelectorAll("#searchBtnText, .primary-btn > span:not(.btn-loader)");
    btnTexts.forEach(t => {
        if (t.dataset.origText) t.textContent = t.dataset.origText;
        else if (t.id === "searchBtnText") t.textContent = "Surveil Flight Trajectories";
    });

    const statusEl = $("status");
    const wrapper = $("statusWrapper");
    if (statusEl) statusEl.textContent = msg;
    if (wrapper) wrapper.style.display = "flex";

    const errBanner = $("searchErrorBanner");
    const errText = $("searchErrorText");
    if (errText) errText.textContent = msg;
    if (errBanner) errBanner.style.display = "flex";

    if (legId) {
        const legErr = $(`mc_leg_error_${legId}`);
        if (legErr) {
            legErr.textContent = msg;
            legErr.style.display = "flex";
        }
    } else if (targetEl && targetEl.id) {
        const m = targetEl.id.match(/^mc_(?:dep|arr|date)_(\d+)$/);
        if (m) {
            const legErr = $(`mc_leg_error_${m[1]}`);
            if (legErr) {
                legErr.textContent = msg;
                legErr.style.display = "flex";
            }
        }
    }

    if (targetEl) {
        try { targetEl.focus(); } catch (e) {}
        targetEl.classList.add("field-error");
    }
}

function clearFormErrors() {
    document.querySelectorAll(".field-error").forEach(el => el.classList.remove("field-error"));
    document.querySelectorAll(".leg-error-banner").forEach(el => {
        el.style.display = "none";
        el.textContent = "";
    });
    if ($("status")) $("status").textContent = "";
    if ($("statusWrapper")) $("statusWrapper").style.display = "none";
    if ($("searchErrorBanner")) $("searchErrorBanner").style.display = "none";
}

function attachStaleDetectionListeners() {
    const inputs = [
        $("departure"), $("arrival"), $("outbound_date"), $("return_date"),
        $("travelClass"), $("cabinClassSelect"), $("nearbyDeparture"), $("nearbyArrival"),
        $("passengersInput")
    ];
    inputs.forEach(el => {
        if (!el) return;
        el.addEventListener("input", markResultsAsStale);
        el.addEventListener("change", markResultsAsStale);
    });
}

function setFlightSearchState(state, options = {}) {
    const submitBtns = document.querySelectorAll("#searchSubmitBtn, .search-launch-btn, .search-submit-btn");
    const spinners = document.querySelectorAll("#searchBtnSpinner, .btn-loader");
    const btnTexts = document.querySelectorAll("#searchBtnText, .primary-btn > span:not(.btn-loader)");
    const emptyPrompt = $("emptySearchPrompt");
    const filterBar = $("flightFilterBar") || $("flightFilters");
    const resultsContainer = $("flightsList") || $("results");

    if (state === "loading") {
        isSearching = true;
        submitBtns.forEach(btn => {
            btn.disabled = true;
            btn.classList.add("loading");
        });
        spinners.forEach(s => s.style.display = "inline-block");
        btnTexts.forEach(t => {
            if (t.id === "searchBtnText" || (!t.closest(".cta-icon-wrap") && t.textContent.trim())) {
                if (!t.dataset.origText) t.dataset.origText = t.textContent;
                t.textContent = "Surveilling Trajectories...";
            }
        });

        clearFormErrors();
        if (emptyPrompt) emptyPrompt.style.display = "none";
        if (filterBar) filterBar.style.display = "none";
        if ($("comparison-section")) $("comparison-section").style.display = "none";

        if (resultsContainer) {
            resultsContainer.innerHTML = `
                <div class="results-loading-state glass-panel">
                    <div class="radar-scan-spinner">
                        <div class="radar-sweep"></div>
                        <div class="radar-blip"></div>
                    </div>
                    <h3 class="font-serif">Surveilling Global Trajectories</h3>
                    <p class="font-mono text-muted">Querying airline carriers and fare feeds across global nodes...</p>
                </div>
            `;
        }
    } else if (state === "success") {
        isSearching = false;
        submitBtns.forEach(btn => {
            btn.disabled = false;
            btn.classList.remove("loading");
        });
        spinners.forEach(s => s.style.display = "none");
        btnTexts.forEach(t => {
            if (t.dataset.origText) t.textContent = t.dataset.origText;
            else if (t.id === "searchBtnText") t.textContent = "Surveil Flight Trajectories";
        });

        if (emptyPrompt) emptyPrompt.style.display = "none";
        clearFormErrors();
        if (filterBar) filterBar.style.display = "flex";

        const scrollTarget = $("resultsContainer") || $("results-section");
        if (scrollTarget) {
            scrollTarget.scrollIntoView({ behavior: "smooth", block: "start" });
        }
    } else if (state === "empty") {
        isSearching = false;
        submitBtns.forEach(btn => {
            btn.disabled = false;
            btn.classList.remove("loading");
        });
        spinners.forEach(s => s.style.display = "none");
        btnTexts.forEach(t => {
            if (t.dataset.origText) t.textContent = t.dataset.origText;
            else if (t.id === "searchBtnText") t.textContent = "Surveil Flight Trajectories";
        });

        if (emptyPrompt) emptyPrompt.style.display = "none";
        if (filterBar) filterBar.style.display = "none";
        clearFormErrors();

        if (resultsContainer) {
            resultsContainer.innerHTML = `
                <div class="empty-state-card glass-panel" style="padding: 48px 24px; text-align: center;">
                    <div class="empty-state-icon" style="margin-bottom: 16px; opacity: 0.7;">
                        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
                            <circle cx="12" cy="12" r="10"></circle>
                            <line x1="8" y1="12" x2="16" y2="12"></line>
                        </svg>
                    </div>
                    <h3 class="font-serif" style="font-size: 1.4rem; margin-bottom: 8px;">No Flight Trajectories Available</h3>
                    <p style="color: var(--text-secondary); max-width: 480px; margin: 0 auto 20px auto; font-size: 0.92rem;">
                        No verified flights were found for this route and date combination. Try selecting a different date or enabling nearby airports.
                    </p>
                    <button type="button" class="cta-secondary" id="btnModifySearch">
                        <span>Modify Search Parameters</span>
                    </button>
                </div>
            `;
            const modifyBtn = resultsContainer.querySelector("#btnModifySearch");
            if (modifyBtn) {
                modifyBtn.addEventListener("click", () => {
                    const originEl = $("departure");
                    if (originEl) originEl.focus();
                });
            }
        }
    } else if (state === "error") {
        isSearching = false;
        submitBtns.forEach(btn => {
            btn.disabled = false;
            btn.classList.remove("loading");
        });
        spinners.forEach(s => s.style.display = "none");
        btnTexts.forEach(t => {
            if (t.dataset.origText) t.textContent = t.dataset.origText;
            else if (t.id === "searchBtnText") t.textContent = "Surveil Flight Trajectories";
        });

        if (emptyPrompt) emptyPrompt.style.display = "none";
        if (filterBar) filterBar.style.display = "none";

        const errorMsg = options.message || "Flight telemetry retrieval interrupted. Please check parameters and try again.";
        showFormError(errorMsg);

        if (resultsContainer) {
            resultsContainer.innerHTML = `
                <div class="empty-state-card glass-panel" style="padding: 48px 24px; text-align: center; border-color: rgba(239, 68, 68, 0.3);">
                    <div class="empty-state-icon" style="margin-bottom: 16px; color: #ef4444;">
                        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
                            <circle cx="12" cy="12" r="10"></circle>
                            <line x1="12" y1="8" x2="12" y2="12"></line>
                            <line x1="12" y1="16" x2="12.01" y2="16"></line>
                        </svg>
                    </div>
                    <h3 class="font-serif" style="font-size: 1.4rem; margin-bottom: 8px;">Flight Telemetry Retrieval Interrupted</h3>
                    <p style="color: var(--text-secondary); max-width: 480px; margin: 0 auto 20px auto; font-size: 0.92rem;">
                        ${escapeHtml(errorMsg)}
                    </p>
                    <button type="button" id="btnRetrySearch" class="cta-secondary">
                        <span>Retry Search</span>
                    </button>
                </div>
            `;
            const retryBtn = resultsContainer.querySelector("#btnRetrySearch");
            if (retryBtn) {
                retryBtn.addEventListener("click", () => executeFlightSearch());
            }
        }
    } else {
        isSearching = false;
        submitBtns.forEach(btn => {
            btn.disabled = false;
            btn.classList.remove("loading");
        });
        spinners.forEach(s => s.style.display = "none");
        if (filterBar) filterBar.style.display = "none";
        if (emptyPrompt) emptyPrompt.style.display = "block";
    }
}

async function executeFlightSearch(event) {
    if (event) {
        event.preventDefault();
        event.stopPropagation();
    }

    if (isSearching) {
        return;
    }
    isSearching = true;

    try {
        clearFormErrors();
        const today = new Date().toISOString().split("T")[0];
        let payload = null;

        // Synchronize effective trip type immediately with DOM state & hidden field rules
        let effectiveTripType = currentTripType;
        const tripTypeEl = $("tripTypeInput");
        if (tripTypeEl && tripTypeEl.value) {
            effectiveTripType = tripTypeEl.value;
        } else {
            const activeTabEl = document.querySelector(".trip-tab-btn.active, .segment-btn.active, [data-trip].active, [data-type].active");
            if (activeTabEl) {
                const dt = activeTabEl.dataset.type || activeTabEl.dataset.trip;
                if (dt === "one-way" || dt === "one_way") effectiveTripType = "one_way";
                else if (dt === "round-trip" || dt === "round_trip") effectiveTripType = "round_trip";
                else if (dt === "multi-city" || dt === "multi_city") effectiveTripType = "multi_city";
            }
        }

        // Critical safety rule: A hidden return-date field must never block a one-way search
        const returnFieldGroup = $("returnDateFieldGroup") || $("returnDateWrap");
        const isReturnFieldHidden = returnFieldGroup && (
            returnFieldGroup.style.display === "none" ||
            (typeof window !== "undefined" && window.getComputedStyle && window.getComputedStyle(returnFieldGroup).display === "none")
        );
        if (isReturnFieldHidden && effectiveTripType === "round_trip") {
            effectiveTripType = "one_way";
        }
        currentTripType = effectiveTripType;
        if (tripTypeEl) {
            tripTypeEl.value = effectiveTripType;
        }

        let cabinClassVal = 1;
        const cabinSel = $("cabinClassSelect") || $("travelClass");
        if (cabinSel) {
            const raw = cabinSel.value;
            const classMap = { "economy": 1, "premium_economy": 2, "business": 3, "first": 4 };
            cabinClassVal = classMap[raw] || parseInt(raw, 10) || 1;
        }

        if (effectiveTripType === "multi_city") {
            syncMultiCityStateFromDOM();
            if (multiCityLegs.length < 2) {
                showFormError("Multi-city itineraries must contain at least 2 flight legs.");
                return;
            }

        const legPayloads = [];
        let prevDate = null;

        for (let i = 0; i < multiCityLegs.length; i++) {
            const leg = multiCityLegs[i];
            const legNum = i + 1;
            const depEl = $(`mc_dep_${leg.id}`);
            const arrEl = $(`mc_arr_${leg.id}`);
            const dateEl = $(`mc_date_${leg.id}`);

            const depRes = await resolveAirportInput(depEl, `Flight ${legNum} departure`);
            const arrRes = await resolveAirportInput(arrEl, `Flight ${legNum} arrival`);

            if (!depRes || !depRes.iata) {
                showFormError(`Please select a departure airport for Flight ${legNum}.`, depEl, leg.id);
                return;
            }

            if (!arrRes || !arrRes.iata) {
                showFormError(`Please select an arrival airport for Flight ${legNum}.`, arrEl, leg.id);
                return;
            }

            if (depRes.iata === arrRes.iata) {
                showFormError(`Flight ${legNum} origin and destination cannot be the same airport.`, arrEl, leg.id);
                return;
            }

            if (!leg.date) {
                showFormError(`Please select a departure date for Flight ${legNum}.`, dateEl, leg.id);
                return;
            }

            if (leg.date < today) {
                showFormError(`Departure date for Flight ${legNum} cannot be in the past.`, dateEl, leg.id);
                return;
            }

            if (prevDate && leg.date < prevDate) {
                showFormError(`Flight ${legNum} departure date cannot be before Flight ${i} date.`, dateEl, leg.id);
                return;
            }
            prevDate = leg.date;

            legPayloads.push({
                departure: depRes.iata,
                arrival: arrRes.iata,
                outbound_date: leg.date
            });
        }

        payload = {
            trip_type: "multi_city",
            legs: legPayloads,
            adults: passengerCounts.adults,
            children: passengerCounts.children,
            infants: passengerCounts.infants,
            travel_class: cabinClassVal
        };

        latestSearch = payload;
        currentSearchContext = {
            tripType: "multi_city",
            legs: legPayloads,
            departureCode: legPayloads[0].departure,
            arrivalCode: legPayloads[legPayloads.length - 1].arrival,
            departureDate: legPayloads[0].outbound_date,
            passengers: passengerCounts.adults + passengerCounts.children + passengerCounts.infants,
            adults: passengerCounts.adults,
            children: passengerCounts.children,
            infants: passengerCounts.infants,
            travelClass: String(cabinClassVal)
        };
        try {
            sessionStorage.setItem("skytrack_search_context", JSON.stringify(currentSearchContext));
        } catch (e) {}

    } else {
        const depInput = $("departure");
        const arrInput = $("arrival");

        if (!depInput || !arrInput) {
            console.error("Flight inputs missing from DOM.");
            return;
        }

        const depRes = await resolveAirportInput(depInput, "Departure airport");
        if (!depRes || !depRes.iata) {
            showFormError("Please select a valid origin city or airport from the suggestions.", depInput);
            return;
        }

        const arrRes = await resolveAirportInput(arrInput, "Arrival airport");
        if (!arrRes || !arrRes.iata) {
            showFormError("Please select a valid destination city or airport from the suggestions.", arrInput);
            return;
        }

        if (depRes.iata === arrRes.iata) {
            showFormError("Origin and destination airports cannot be identical.", arrInput);
            return;
        }

        const departureDate = $("outbound_date") ? $("outbound_date").value : "";
        const returnDate = $("return_date") ? $("return_date").value : "";

        if (!departureDate) {
            showFormError("Please select a departure date.", $("outbound_date"));
            return;
        }

        if (departureDate < today) {
            showFormError("Departure date cannot be in the past.", $("outbound_date"));
            return;
        }

        if (effectiveTripType === "round_trip") {
            if (!returnDate) {
                showFormError("Please select a return date for round-trip search.", $("return_date"));
                return;
            }
            if (returnDate < departureDate) {
                showFormError("Return date cannot be before departure date.", $("return_date"));
                return;
            }
        }

        const nearbyDeparture = $("nearbyDeparture") ? $("nearbyDeparture").checked : false;
        const nearbyArrival = $("nearbyArrival") ? $("nearbyArrival").checked : false;

        payload = {
            trip_type: effectiveTripType,
            departure: depRes.iata,
            arrival: arrRes.iata,
            outbound_date: departureDate,
            return_date: effectiveTripType === "round_trip" ? (returnDate || null) : null,
            nearby_departure: nearbyDeparture,
            nearby_arrival: nearbyArrival,
            adults: passengerCounts.adults,
            children: passengerCounts.children,
            infants: passengerCounts.infants,
            travel_class: cabinClassVal
        };

        latestSearch = payload;
        currentSearchContext = {
            tripType: effectiveTripType,
            departureCode: depRes.iata,
            arrivalCode: arrRes.iata,
            departureDate: departureDate,
            returnDate: effectiveTripType === "round_trip" ? returnDate : null,
            passengers: passengerCounts.adults + passengerCounts.children + passengerCounts.infants,
            adults: passengerCounts.adults,
            children: passengerCounts.children,
            infants: passengerCounts.infants,
            travelClass: String(cabinClassVal)
        };
        try {
            sessionStorage.setItem("skytrack_search_context", JSON.stringify(currentSearchContext));
        } catch (e) {}
    }

    const thisRequestId = ++activeSearchRequestId;

    setFlightSearchState("loading");

    const response = await fetch("/api/search", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify(payload)
        });

        let data = null;
        try {
            data = await response.json();
        } catch (parseErr) {
            throw new Error(`Invalid server response (${response.status})`);
        }

        if (thisRequestId !== activeSearchRequestId) {
            console.warn(`Ignoring stale response for search #${thisRequestId} (active is #${activeSearchRequestId})`);
            return;
        }

        if (!response.ok) {
            throw new Error(data && data.error ? data.error : `Flight search failed with status ${response.status}.`);
        }

        if (!data || !data.results || !data.results.length) {
            setFlightSearchState("empty");
            updateFlightStatusStrip(currentSearchContext, [], data);
            return;
        }

        allFlightResults = deduplicateFlights(data.results);

        if (typeof renderNearbyAirportComparison === "function") {
            try {
                await renderNearbyAirportComparison(data);
            } catch (err) {
                console.warn("Nearby comparison error:", err);
            }
        }

        if (data.comparison && typeof renderProviderComparison === "function") {
            renderProviderComparison(data.comparison);
        } else {
            if ($("comparison-section")) $("comparison-section").style.display = "none";
            if ($("navCompareLink")) $("navCompareLink").style.display = "none";
        }

        if (typeof createAirlineFilters === "function") {
            createAirlineFilters();
        }

        applyFlightFilters();
        updateFlightStatusStrip(currentSearchContext, allFlightResults, data);

        const countStr = `${allFlightResults.length} flights found`;
        if ($("status")) $("status").textContent = countStr;
        if ($("resultSummary")) $("resultSummary").textContent = countStr;
        if ($("resultsCountLabel")) $("resultsCountLabel").textContent = `${allFlightResults.length} FLIGHTS LOCATED`;

        setFlightSearchState("success");

    } catch (error) {
        console.error("Flight search error:", error);
        setFlightSearchState("error", {
            message: error.message || "Could not retrieve flight trajectories. Please check parameters and try again."
        });
    } finally {
        isSearching = false;
    }
}

// Bind search submission to all forms and buttons
if ($("searchForm")) {
    $("searchForm").addEventListener("submit", executeFlightSearch);
}
if ($("flightSearchForm")) {
    $("flightSearchForm").addEventListener("submit", executeFlightSearch);
}
document.querySelectorAll("#searchSubmitBtn, .search-launch-btn, .search-submit-btn").forEach(btn => {
    btn.addEventListener("click", e => {
        executeFlightSearch(e);
    });
});

/* =========================================================



   CREATE AIRLINE FILTERS



========================================================= */

function createAirlineFilters() {
    const container = $("airlineFilters");

    if (!container) {
        return;
    }

    const airlineCounts = {};

    allFlightResults.forEach(flight => {
        const airline = String(
            flight.airline || "Unknown Airline"
        ).trim();

        if (!airline) {
            return;
        }

        airlineCounts[airline] =
            (airlineCounts[airline] || 0) + 1;
    });

    const airlines = Object.entries(airlineCounts)
        .sort((a, b) => b[1] - a[1]);

    container.innerHTML = airlines
        .map(([airline, count], index) => {
            const checkboxId =
                `airline-checkbox-${index}`;

            return `
                <label
                    class="airline-option"
                    for="${checkboxId}"
                >

                    <input
                        id="${checkboxId}"
                        type="checkbox"
                        class="airline-filter-checkbox"
                        value="${escapeHtml(airline)}"
                    >

                    <span class="airline-name">
                        ${escapeHtml(airline)}
                    </span>

                    <span class="airline-count">
                        (${count})
                    </span>

                </label>
            `;
        })
        .join("");
}
/* =========================================================



   APPLY FILTERS



========================================================= */







function applyFlightFilters() {
    if (!allFlightResults.length) {
        return;
    }

    let filteredFlights = deduplicateFlights(allFlightResults);

    // Stops filter (supporting both flights.html filter-pills and index.html checkboxes)
    const activeStopsPill = document.querySelector("#flightFilterBar .filter-pill.active");
    const pillStopsVal = activeStopsPill ? activeStopsPill.dataset.stops : null;

    const nonstop = $("filterNonstop")?.checked || pillStopsVal === "0";
    const oneStop = $("filterOneStop")?.checked || pillStopsVal === "1";
    const twoPlus = $("filterTwoPlus")?.checked || pillStopsVal === "2";
    const stopFilterActive = nonstop || oneStop || twoPlus;

    if (stopFilterActive) {
        filteredFlights = filteredFlights.filter(flight => {
            const stops = Number(flight.stops) || 0;
            if (stops === 0 && nonstop) return true;
            if (stops === 1 && oneStop) return true;
            if (stops >= 2 && twoPlus) return true;
            return false;
        });
    }

    // Maximum price filter
    const maxPrice = Number($("maxPrice")?.value);
    if (maxPrice > 0) {
        filteredFlights = filteredFlights.filter(flight => Number(flight.price) <= maxPrice);
    }

    // Airline filters
    const selectedAirlines = Array.from(document.querySelectorAll(".airline-filter-checkbox:checked"))
        .map(checkbox => checkbox.value);
    if (selectedAirlines.length > 0) {
        filteredFlights = filteredFlights.filter(flight => {
            const airline = String(flight.airline || "Unknown Airline").trim();
            return selectedAirlines.includes(airline);
        });
    }

    // Sorting (supporting both flights.html #flightSortSelect and index.html #sortFlights)
    const sortVal = $("flightSortSelect") ? $("flightSortSelect").value : ($("sortFlights")?.value || "cheapest");
    if (sortVal === "fastest" || sortVal === "duration_asc") {
        filteredFlights.sort((a, b) => Number(a.duration || 0) - Number(b.duration || 0));
    } else if (sortVal === "price_desc") {
        filteredFlights.sort((a, b) => {
            const pA = (!isNaN(Number(a.price)) && Number(a.price) > 0) ? Number(a.price) : -Infinity;
            const pB = (!isNaN(Number(b.price)) && Number(b.price) > 0) ? Number(b.price) : -Infinity;
            return pB - pA;
        });
    } else if (sortVal === "departure_asc") {
        filteredFlights.sort((a, b) => String(a.departure_time || "").localeCompare(String(b.departure_time || "")));
    } else {
        // cheapest / price_asc: valid lowest price first; missing/0 pushed to end
        filteredFlights.sort((a, b) => {
            const pA = (!isNaN(Number(a.price)) && Number(a.price) > 0) ? Number(a.price) : Infinity;
            const pB = (!isNaN(Number(b.price)) && Number(b.price) > 0) ? Number(b.price) : Infinity;
            return pA - pB;
        });
    }

    // Update count labels
    if ($("resultSummary")) {
        $("resultSummary").textContent = `${filteredFlights.length} of ${allFlightResults.length} flights shown`;
    }
    if ($("resultsCountLabel")) {
        $("resultsCountLabel").textContent = `${filteredFlights.length} FLIGHT${filteredFlights.length === 1 ? "" : "S"} LOCATED`;
    }

    renderFlightResults(filteredFlights);
}

/* =========================================================



   RENDER FLIGHT RESULTS



========================================================= */







/* =========================================================



   RENDER FLIGHT RESULTS



========================================================= */







async function renderFlightResults(flights) {

    const resultsEl = $("flightsList") || $("results");

    if (!resultsEl) {
        console.error("Results container not found.");
        return;
    }

    if (!Array.isArray(flights) || flights.length === 0) {

        resultsEl.innerHTML = `
            <div class="no-results">
                <div class="no-results-icon"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;display:inline-block;"><path d="M17.8 19.2 16 11l3.5-3.5C21 6 21.5 4 21 3c-1-.5-3 0-4.5 1.5L13 8 4.8 6.2c-.5-.1-.9.1-1.1.5l-.3.5c-.2.5-.1 1 .3 1.3L9 12l-2 3H4l-1 1 3 2 2 3 1-1v-3l3-2 3.5 5.3c.3.4.8.5 1.3.3l.5-.3c.4-.2.6-.6.5-1.1z"/></svg></div>

                <h3>No flights match your filters</h3>

                <p>
                    Try removing a filter or increasing
                    your maximum price.
                </p>
            </div>
        `;

        return;
    }

    /* =========================================================
       HELPERS
    ========================================================= */

    const safeText = value => {

        if (
            value === null ||
            value === undefined ||
            value === ""
        ) {
            return "";
        }

        return String(value);
    };


    const money = value => {

        const number = Number(value);

        if (Number.isNaN(number)) {
            return "N/A";
        }

        return number.toLocaleString("en-IN");
    };


    const durationText = minutes => {

        const total = Number(minutes) || 0;

        const hours = Math.floor(total / 60);
        const mins = total % 60;

        if (hours > 0 && mins > 0) {
            return `${hours}h ${mins}m`;
        }

        if (hours > 0) {
            return `${hours}h`;
        }

        return `${mins}m`;
    };


    const stopText = stops => {

        const value = Number(stops) || 0;

        if (value === 0) {
            return "Non-stop";
        }

        return `${value} stop${value > 1 ? "s" : ""}`;
    };


    /* =========================================================
       CHEAPEST FLIGHT
    ========================================================= */

    const prices = flights
        .map(flight => Number(flight.price))
        .filter(price => !Number.isNaN(price));

    const cheapestPrice =
        prices.length
            ? Math.min(...prices)
            : null;

    const cheapestIndex =
        flights.findIndex(
            flight =>
                Number(flight.price) === cheapestPrice
        );


    /* =========================================================
       RENDER ONE LEG
    ========================================================= */

    const renderLeg = async ({
        label,
        leg,
        fallbackAirline
    }) => {

        if (!leg) {
            return "";
        }

        const airline =
            leg.airline ||
            fallbackAirline ||
            "Unknown Airline";

        const departureCode =
            leg.departure_airport ||
            "N/A";

        const arrivalCode =
            leg.arrival_airport ||
            "N/A";

        const departureTime =
            leg.departure_time ||
            "--:--";

        const arrivalTime =
            leg.arrival_time ||
            "--:--";

        const duration =
            Number(leg.duration) || 0;

        const stops =
            Number(leg.stops) || 0;

        const segments =
            Array.isArray(leg.segments)
                ? leg.segments
                : [];


        /* =====================================================
           SEGMENTS
        ===================================================== */

        let segmentsHtml = "";

        if (segments.length > 0) {

            for (
                let i = 0;
                i < segments.length;
                i++
            ) {

                const segment = segments[i] || {};

                const segmentAirline =
                    segment.airline ||
                    airline ||
                    "Unknown Airline";

                const flightNumber =
                    segment.flight_number ||
                    "";

                const from =
                    segment.departure_airport ||
                    departureCode;

                const to =
                    segment.arrival_airport ||
                    arrivalCode;

                const segmentDeparture =
                    segment.departure_time ||
                    "--:--";

                const segmentArrival =
                    segment.arrival_time ||
                    "--:--";

                const segmentDuration =
                    Number(segment.duration) || 0;


                /* ---------------------------------------------
                   AIRPORT NAMES
                --------------------------------------------- */

                let fromLabel = from;
                let toLabel = to;

                try {

                    const fromInfo =
                        await getAirportInfo(from);

                    const toInfo =
                        await getAirportInfo(to);

                    fromLabel =
                        formatAirport(
                            fromInfo,
                            from
                        );

                    toLabel =
                        formatAirport(
                            toInfo,
                            to
                        );

                } catch (error) {

                    console.warn(
                        "Airport information unavailable:",
                        error
                    );

                }


                segmentsHtml += `

                    <div class="flight-segment">

                        <div class="segment-header">

                            <span class="segment-airline">
                                ${escapeHtml(
                    segmentAirline
                )}
                            </span>

                            ${flightNumber
                        ? `
                                        <span class="segment-flight-number">
                                            ${escapeHtml(
                            flightNumber
                        )}
                                        </span>
                                      `
                        : ""
                    }

                        </div>


                        <div class="segment-route">

                            <div class="segment-point">

                                <strong class="segment-time">
                                    ${escapeHtml(
                        segmentDeparture
                    )}
                                </strong>

                                <span class="segment-airport">
                                    ${escapeHtml(
                        fromLabel
                    )}
                                </span>

                            </div>


                            <div class="segment-middle">

                                <span class="segment-duration">
                                    ${durationText(
                        segmentDuration
                    )}
                                </span>

                                <span class="segment-plane">
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;display:inline-block;"><path d="M17.8 19.2 16 11l3.5-3.5C21 6 21.5 4 21 3c-1-.5-3 0-4.5 1.5L13 8 4.8 6.2c-.5-.1-.9.1-1.1.5l-.3.5c-.2.5-.1 1 .3 1.3L9 12l-2 3H4l-1 1 3 2 2 3 1-1v-3l3-2 3.5 5.3c.3.4.8.5 1.3.3l.5-.3c.4-.2.6-.6.5-1.1z"/></svg>
                                </span>

                                <div class="segment-line"></div>

                            </div>


                            <div class="segment-point">

                                <strong class="segment-time">
                                    ${escapeHtml(
                        segmentArrival
                    )}
                                </strong>

                                <span class="segment-airport">
                                    ${escapeHtml(
                        toLabel
                    )}
                                </span>

                            </div>

                        </div>

                    </div>
                `;


                /* ---------------------------------------------
                   LAYOVER
                --------------------------------------------- */

                if (i < segments.length - 1) {

                    const layovers =
                        Array.isArray(leg.layovers)
                            ? leg.layovers
                            : [];

                    const layover =
                        layovers[i];

                    if (layover) {

                        const layoverAirport =
                            layover.id ||
                            segment.arrival_airport ||
                            "";

                        const layoverDuration =
                            Number(
                                layover.duration
                            ) || 0;

                        let layoverLabel =
                            layoverAirport;

                        try {

                            const info =
                                await getAirportInfo(
                                    layoverAirport
                                );

                            layoverLabel =
                                formatAirport(
                                    info,
                                    layoverAirport
                                );

                        } catch (error) {

                            console.warn(
                                "Layover airport lookup failed:",
                                error
                            );

                        }


                        segmentsHtml += `

                            <div class="flight-layover-detail">

                                <div class="layover-line"></div>

                                <div class="layover-content">

                                    <span class="layover-icon">
                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;display:inline-block;"><path d="m17 2 4 4-4 4"></path><path d="M3 11v-1a4 4 0 0 1 4-4h14"></path><path d="m7 22-4-4 4-4"></path><path d="M21 13v1a4 4 0 0 1-4 4H3"></path></svg>
                                    </span>

                                    <span>

                                        <strong>
                                            Layover at
                                            ${escapeHtml(
                            layoverLabel
                        )}
                                        </strong>

                                        <small>
                                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;display:inline-block;"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>
                                            ${durationText(
                            layoverDuration
                        )}

                                            ${layover.overnight ||
                                layoverDuration >= 480
                                ? " •  Overnight"
                                : ""
                            }
                                        </small>

                                    </span>

                                </div>

                            </div>
                        `;
                    }
                }
            }

        } else {

            /* =================================================
               FALLBACK WHEN SEGMENTS ARE MISSING
            ================================================= */

            let departureLabel =
                departureCode;

            let arrivalLabel =
                arrivalCode;

            try {

                const departureInfo =
                    await getAirportInfo(
                        departureCode
                    );

                const arrivalInfo =
                    await getAirportInfo(
                        arrivalCode
                    );

                departureLabel =
                    formatAirport(
                        departureInfo,
                        departureCode
                    );

                arrivalLabel =
                    formatAirport(
                        arrivalInfo,
                        arrivalCode
                    );

            } catch (error) {

                console.warn(
                    "Airport lookup failed:",
                    error
                );

            }


            segmentsHtml = `

                <div class="route">

                    <div class="airport-time">

                        <strong>
                            ${escapeHtml(
                departureTime
            )}
                        </strong>

                        <span>
                            ${escapeHtml(
                departureLabel
            )}
                        </span>

                    </div>


                    <div class="route-line">
                        ───── <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;display:inline-block;"><path d="M17.8 19.2 16 11l3.5-3.5C21 6 21.5 4 21 3c-1-.5-3 0-4.5 1.5L13 8 4.8 6.2c-.5-.1-.9.1-1.1.5l-.3.5c-.2.5-.1 1 .3 1.3L9 12l-2 3H4l-1 1 3 2 2 3 1-1v-3l3-2 3.5 5.3c.3.4.8.5 1.3.3l.5-.3c.4-.2.6-.6.5-1.1z"/></svg> ─────
                    </div>


                    <div class="airport-time">

                        <strong>
                            ${escapeHtml(
                arrivalTime
            )}
                        </strong>

                        <span>
                            ${escapeHtml(
                arrivalLabel
            )}
                        </span>

                    </div>

                </div>
            `;
        }


        /* =====================================================
           LEG HEADER
        ===================================================== */

        return `

            <div class="flight-leg">

                <div class="flight-leg-header">

                    <span class="flight-leg-label">
                        ${escapeHtml(label)}
                    </span>

                    <span class="flight-leg-airline">
                        ${escapeHtml(airline)}
                    </span>

                    <span class="flight-leg-summary">
                        ${escapeHtml(departureCode)}
                        →
                        ${escapeHtml(arrivalCode)}

                        •
                        ${escapeHtml(
            stopText(stops)
        )}

                        •
                        ${escapeHtml(
            durationText(duration)
        )}
                    </span>

                </div>


                <div class="detailed-flight-route">

                    ${segmentsHtml}

                </div>

            </div>
        `;
    };


    /* =========================================================
       CREATE CARDS
    ========================================================= */

    const cards = await Promise.all(

        flights.map(
            async (flight, index) => {

                const isMultiCity = String(flight.trip_type || "").toLowerCase() === "multi_city" || (Array.isArray(flight.legs) && flight.legs.length > 0);
                const isRoundTrip = !isMultiCity && (
                    String(
                        flight.trip_type || ""
                    ).toLowerCase() ===
                    "round_trip" ||
                    !!flight.return
                );


                /* ---------------------------------------------
                   OUTBOUND
                --------------------------------------------- */

                const outbound =
                    isRoundTrip
                        ? (
                            flight.outbound ||
                            flight
                        )
                        : flight;


                /* ---------------------------------------------
                   RETURN
                --------------------------------------------- */

                const returnLegData =
                    isRoundTrip
                        ? (
                            flight.return ||
                            {
                                airline:
                                    flight.return_airline ||
                                    flight.airline,

                                departure_airport:
                                    flight.return_departure_airport,

                                arrival_airport:
                                    flight.return_arrival_airport,

                                departure_time:
                                    flight.return_departure_time,

                                arrival_time:
                                    flight.return_arrival_time,

                                duration:
                                    flight.return_duration,

                                stops:
                                    flight.return_stops,

                                segments:
                                    flight.return_segments,

                                layovers:
                                    flight.return_layovers
                            }
                        )
                        : null;


                const airline =
                    flight.airline ||
                    outbound.airline ||
                    "Unknown Airline";


                const price =
                    Number(flight.price);


                const priceText =
                    Number.isNaN(price)
                        ? "N/A"
                        : money(price);


                /* ---------------------------------------------
                   OUTBOUND HTML
                --------------------------------------------- */

                const outboundHtml =
                    await renderLeg({
                        label:
                            isRoundTrip
                                ? "OUTBOUND"
                                : "FLIGHT",

                        leg:
                            outbound,

                        fallbackAirline:
                            airline
                    });


                /* ---------------------------------------------
                   RETURN HTML
                --------------------------------------------- */

                const returnHtml =
                    isRoundTrip
                        ? await renderLeg({
                            label: "RETURN",

                            leg:
                                returnLegData,

                            fallbackAirline:
                                airline
                        })
                        : "";


                /* ---------------------------------------------
                   MULTI-CITY LEGS HTML
                --------------------------------------------- */

                let multiCityLegsHtml = "";
                if (isMultiCity && Array.isArray(flight.legs)) {
                    const legRenderPromises = flight.legs.map((leg, lIdx) =>
                        renderLeg({
                            label: `LEG ${lIdx + 1}`,
                            leg: leg,
                            fallbackAirline: leg.airline || airline
                        })
                    );
                    const rendered = await Promise.all(legRenderPromises);
                    multiCityLegsHtml = rendered.join("");
                }


                /* ---------------------------------------------
                   ROUND TRIP FARES
                --------------------------------------------- */

                let fareBreakdown = "";

                if (isRoundTrip) {

                    const outboundReference =
                        flight.reference_outbound_price;

                    const returnReference =
                        flight.reference_return_price;

                    const separateTotal =
                        flight.reference_separate_total;


                    fareBreakdown = `

                        <div class="roundtrip-price-breakdown">

                            <div class="fare-line">

                                <span>
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;display:inline-block;"><path d="M17.8 19.2 16 11l3.5-3.5C21 6 21.5 4 21 3c-1-.5-3 0-4.5 1.5L13 8 4.8 6.2c-.5-.1-.9.1-1.1.5l-.3.5c-.2.5-.1 1 .3 1.3L9 12l-2 3H4l-1 1 3 2 2 3 1-1v-3l3-2 3.5 5.3c.3.4.8.5 1.3.3l.5-.3c.4-.2.6-.6.5-1.1z"/></svg> Outbound reference fare
                                </span>

                                <strong>
                                    ${outboundReference != null
                            ? `₹${money(
                                outboundReference
                            )}`
                            : "—"
                        }
                                </strong>

                            </div>


                            <div class="fare-line">

                                <span>
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;display:inline-block;"><path d="m9 14-4-4 4-4"/><path d="M5 10h11a4 4 0 1 1 0 8h-1"/></svg> Return reference fare
                                </span>

                                <strong>
                                    ${returnReference != null
                            ? `₹${money(
                                returnReference
                            )}`
                            : "—"
                        }
                                </strong>

                            </div>


                            ${separateTotal != null
                            ? `

                                        <div class="fare-line separate-total">

                                            <span>
                                                Separate one-way total
                                            </span>

                                            <strong>
                                                ₹${money(
                                separateTotal
                            )}
                                            </strong>

                                        </div>


                                        ${Number(
                                separateTotal
                            ) > price

                                ? `

                                                    <div class="roundtrip-saving">

                                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;display:inline-block;"><circle cx="12" cy="12" r="9"></circle><path d="M14.8 9A2 2 0 0 0 13 8h-2a2 2 0 1 0 0 4h2a2 2 0 1 1 0 4h-2a2 2 0 0 1-1.8-1"></path><path d="M12 6v2m0 8v2"></path></svg> Round-trip saves

                                                        ₹${money(
                                    Number(
                                        separateTotal
                                    ) - price
                                )}

                                                    </div>
                                                  `

                                : Number(
                                    separateTotal
                                ) < price

                                    ? `

                                                        <div class="roundtrip-saving">

                                                            Separate tickets are

                                                            ₹${money(
                                        price -
                                        Number(
                                            separateTotal
                                        )
                                    )}

                                                            cheaper

                                                        </div>
                                                      `

                                    : ""
                            }

                                      `
                            : ""
                        }


                            <div class="fare-note">

                                * One-way fares are current
                                standalone reference fares.

                            </div>

                        </div>

                        <div class="price-label">
                            Actual round-trip ticket fare
                        </div>
                    `;
                }


                /* ---------------------------------------------
                   CHEAPEST BADGE & DATA STATUS BADGE
                --------------------------------------------- */

                const cheapestBadge =
                    index === cheapestIndex
                        ? `
                            <div class="cheapest-badge">
                                CHEAPEST FLIGHT
                            </div>
                          `
                        : "";

                // Audit data status according to strict truthfulness rules
                let statusBadge = "";
                const isDemo = Boolean(flight.is_simulated || flight.data_status === "demonstration");
                const isCached = Boolean(flight.is_cached || flight.data_status === "cached");
                const isLiveFresh = Boolean(flight.is_live && !isCached && !isDemo && (typeof flight.age_seconds !== "number" || flight.age_seconds < 300));

                if (isDemo) {
                    statusBadge = `<span class="data-status-badge demo-badge font-mono" title="Simulated benchmark fare — not confirmed bookable carrier inventory.">Demo data — not bookable</span>`;
                } else if (isCached) {
                    const ageMinutes = (typeof flight.age_seconds === "number" && flight.age_seconds > 0)
                        ? Math.max(1, Math.round(flight.age_seconds / 60))
                        : null;
                    const cachedTimeLabel = ageMinutes ? `${ageMinutes}m ago` : (flight.cached_at ? `Observed ${flight.cached_at}` : "Recent cache");
                    statusBadge = `<span class="data-status-badge cached-badge font-mono" title="Cached observation from ${escapeHtml(flight.cached_at || 'prior query')}. Fares fluctuate in real-time.">CACHED FARE (${cachedTimeLabel})</span>`;
                } else if (isLiveFresh) {
                    const providerLabel = flight.source || "Live API";
                    statusBadge = `<span class="data-status-badge live-badge font-mono" title="Fresh upstream provider quote via ${escapeHtml(providerLabel)}. Direct carrier confirmation required for booking.">LIVE PROVIDER FARE</span>`;
                } else {
                    statusBadge = `<span class="data-status-badge neutral-badge font-mono" title="Observed market tariff. Verify current availability directly with carrier.">Price information available</span>`;
                }


                /* ---------------------------------------------
                   CARD
                --------------------------------------------- */

                return `

                    <article
                        class="
                            flight
                            ${index === cheapestIndex
                        ? "cheapest-flight"
                        : ""
                    }
                        "
                    >

                        <div class="flight-main">

                            <div class="badges-row" style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px;">
                                ${cheapestBadge}
                                ${statusBadge}
                            </div>


                            <div class="trip-type-badge">

                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;display:inline-block;"><path d="M17.8 19.2 16 11l3.5-3.5C21 6 21.5 4 21 3c-1-.5-3 0-4.5 1.5L13 8 4.8 6.2c-.5-.1-.9.1-1.1.5l-.3.5c-.2.5-.1 1 .3 1.3L9 12l-2 3H4l-1 1 3 2 2 3 1-1v-3l3-2 3.5 5.3c.3.4.8.5 1.3.3l.5-.3c.4-.2.6-.6.5-1.1z"/></svg>

                                ${isMultiCity
                        ? `MULTI-CITY (${flight.legs?.length || 2} LEGS)`
                        : (isRoundTrip
                            ? "ROUND TRIP"
                            : "ONE WAY")
                    }

                            </div>


                            ${isMultiCity
                        ? multiCityLegsHtml
                        : `${outboundHtml}${returnHtml}`
                    }

                        </div>


                        <div class="flight-price">

                            <div class="price">
                                ₹${priceText}
                            </div>


                            <div class="flight-actions-stack">
                                <button
                                    type="button"
                                    class="compare-flight-btn"
                                    data-index="${index}"
                                    title="Compare prices for this exact itinerary"
                                >
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                        <polyline points="15 3 21 3 21 9"></polyline>
                                        <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path>
                                    </svg>
                                    <span>Compare prices</span>
                                </button>
                                <button
                                    type="button"
                                    class="track-btn"
                                    data-index="${index}"
                                    data-price="${price}"
                                    data-dep="${escapeHtml(flight.departure_airport || '')}"
                                    data-arr="${escapeHtml(flight.arrival_airport || '')}"
                                    data-date="${escapeHtml(flight.departure_date || flight.outbound_date || '')}"
                                    data-flight-no="${escapeHtml(flight.flight_number || '')}"
                                    data-airline="${escapeHtml(flight.airline || '')}"
                                >
                                    Track Price
                                </button>
                            </div>

                        </div>

                    </article>
                `;
            }
        )
    );


    /* =========================================================
       INSERT CARDS & DATA TRANSPARENCY BANNER
    ========================================================= */

    const hasSimulated = flights.some(f => f.is_simulated || f.data_status === "demonstration");
    const disclosureHtml = hasSimulated
        ? `<div class="data-source-disclosure-banner font-mono" style="margin-bottom: 20px; padding: 12px 18px; border-radius: var(--radius-md); background: rgba(223, 201, 158, 0.05); border: 1px solid var(--accent-gold-border); font-size: 0.74rem; color: var(--text-secondary); line-height: 1.5;">
            <span class="text-gold" style="font-weight: 700; letter-spacing: 0.08em;">Demo data — not bookable:</span> Benchmark flight data displayed (Live API credentials unconfigured or benchmark mode). Fares represent simulated models, not confirmed bookable carrier inventory.
           </div>`
        : "";

    resultsEl.innerHTML = disclosureHtml + cards.join("");

    /* =========================================================
       COMPARE FLIGHT BUTTONS
    ========================================================= */

    document
        .querySelectorAll(".compare-flight-btn")
        .forEach(button => {
            button.addEventListener("click", e => {
                e.stopPropagation();
                const index = Number(button.dataset.index);
                const flight = flights[index];
                if (flight) {
                    openExactFlightComparison(flight);
                }
            });
        });

    /* =========================================================
       TRACK BUTTONS
    ========================================================= */

    document
        .querySelectorAll(".track-btn")
        .forEach(button => {

            button.addEventListener(
                "click",
                () => {
                    const idx = Number(button.dataset.index);
                    const flight = flights[idx];
                    currentTrackingFlight = flight || null;

                    const price =
                        Number(
                            button.dataset.price
                        );


                    if (!Number.isNaN(price) && price > 0) {

                        const target =
                            $("target_price");

                        if (target) {

                            target.value =
                                Math.round(
                                    price * 0.9
                                );
                        }
                    }


                    const trackModal = $("trackPriceModal");
                    if (trackModal) {
                        trackModal.style.display = "flex";
                        if ($("trackTargetPrice") && !Number.isNaN(price) && price > 0) {
                            $("trackTargetPrice").value = Math.round(price * 0.9);
                        }
                        if ($("modalRouteSummary")) {
                            const summaryDep = (flight && flight.departure_airport) || button.dataset.dep || (latestSearch && latestSearch.departure) || "";
                            const summaryArr = (flight && flight.arrival_airport) || button.dataset.arr || (latestSearch && latestSearch.arrival) || "";
                            const summaryDate = (flight && (flight.departure_date || flight.outbound_date)) || button.dataset.date || (latestSearch && latestSearch.outbound_date) || "";
                            const flightInfo = (flight && flight.flight_number) ? ` (${flight.airline || ''} ${flight.flight_number})` : "";
                            $("modalRouteSummary").textContent = `${summaryDep} → ${summaryArr} · ${summaryDate}${flightInfo}`;
                        }
                        return;
                    }

                    const trackForm = $("trackForm");
                    if (trackForm) {
                        trackForm.scrollIntoView({
                            behavior: "smooth",
                            block: "center"
                        });
                    }

                    const email = $("email");
                    if (email) {
                        email.focus();
                    }

                }
            );
        });
}




/* =========================================================



   FILTER EVENTS



========================================================= */







[



    "sortFlights",



    "filterNonstop",



    "filterOneStop",



    "filterTwoPlus"



].forEach(id => {







    const element = $(id);







    if (!element) {



        return;



    }







    element.addEventListener(



        "change",



        applyFlightFilters



    );







});











if ($("maxPrice")) {







    $("maxPrice").addEventListener(



        "input",



        applyFlightFilters



    );







}











/* =========================================================



   AIRLINE CHECKBOX EVENT



========================================================= */







document.addEventListener(



    "change",



    event => {







        if (



            event.target.classList.contains(



                "airline-filter-checkbox"



            )



        ) {







            applyFlightFilters();







        }







    }



);











/* =========================================================



   RESET FILTERS



========================================================= */







if ($("resetFilters")) {







    $("resetFilters").addEventListener(



        "click",



        () => {







            if ($("sortFlights")) {



                $("sortFlights").value =



                    "cheapest";



            }











            if ($("filterNonstop")) {



                $("filterNonstop").checked =



                    false;



            }











            if ($("filterOneStop")) {



                $("filterOneStop").checked =



                    false;



            }











            if ($("filterTwoPlus")) {



                $("filterTwoPlus").checked =



                    false;



            }











            if ($("maxPrice")) {



                $("maxPrice").value =



                    "";



            }











            document



                .querySelectorAll(



                    ".airline-filter-checkbox"



                )



                .forEach(



                    checkbox => {







                        checkbox.checked =



                            false;







                    }



                );











            applyFlightFilters();







        }



    );







}











/* =========================================================



   START PRICE TRACKING



========================================================= */







if ($("trackForm")) {







    $("trackForm").addEventListener(



        "submit",



        async event => {







            event.preventDefault();











            if (!latestSearch) {







                $("trackStatus").textContent =



                    "Please search for a flight first.";







                return;



            }











            const payload = {







                ...latestSearch,







                target_price:



                    $("target_price").value,







                email:



                    $("email").value.trim()







            };











            try {







                $("trackStatus").textContent =



                    "Starting price tracking...";











                const response =



                    await fetch(



                        "/api/track",



                        {







                            method: "POST",







                            headers: {



                                "Content-Type":



                                    "application/json"



                            },







                            body:



                                JSON.stringify(



                                    payload



                                )







                        }



                    );











                const data =



                    await response.json();











                if (!response.ok) {







                    throw new Error(



                        data.error ||



                        "Could not start tracking."



                    );







                }











                $("trackStatus").textContent =



                    data.message ||



                    "Flight tracking started!";

                if (data && data.id && data.delete_token) {
                    saveDeleteToken(data.id, data.delete_token);
                }

                loadTracked();











            } catch (error) {







                console.error(



                    "Tracking error:",



                    error



                );











                $("trackStatus").textContent =



                    error.message ||



                    "Could not start tracking.";







            }







        }



    );







}











/* =========================================================



   LOAD TRACKED FLIGHTS



========================================================= */







async function loadTracked() {







    if (!$("tracked")) {



        return;



    }











    try {







        const response =



            await fetch(



                "/api/tracked"



            );











        if (!response.ok) {



            throw new Error(



                "Could not load tracked flights."



            );



        }











        const data = await response.json();

        // Update real Price Watch Dashboard Metrics
        updateDashboardMetrics(data);

        if (!data.length) {
            $("tracked").innerHTML = `
                <div class="tracked-empty-card" role="status">
                    <div class="empty-icon-wrap" aria-hidden="true">
                        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
                            <circle cx="12" cy="12" r="10"></circle>
                            <polygon points="16.24 7.76 14.12 14.12 7.76 16.24 9.88 9.88 16.24 7.76"></polygon>
                        </svg>
                    </div>
                    <h3 class="empty-title">No routes being tracked yet</h3>
                    <p class="empty-desc">Search for flights above or set a target fare to receive automated notifications when prices drop.</p>
                    <a href="#search" class="empty-cta-btn">
                        <span>Search flights to track</span>
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                            <line x1="5" y1="12" x2="19" y2="12"></line>
                            <polyline points="12 5 19 12 12 19"></polyline>
                        </svg>
                    </a>
                </div>
            `;
            return;
        }











        $("tracked").innerHTML =



            data.map(



                flight => {







                    const currentPrice =



                        Number(



                            flight.last_price



                        );











                    const targetPrice =



                        Number(



                            flight.target_price



                        );











                    let priceStatus = "";











                    if (



                        flight.last_price !== null &&



                        flight.last_price !== undefined



                    ) {







                        if (



                            currentPrice <=



                            targetPrice



                        ) {







                            const savings =



                                (



                                    (



                                        targetPrice -



                                        currentPrice



                                    ) /



                                    targetPrice



                                ) * 100;











                            priceStatus = `







                                <span class="price-good">







                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;display:inline-block;"><circle cx="12" cy="12" r="10"></circle><circle cx="12" cy="12" r="6"></circle><circle cx="12" cy="12" r="2"></circle></svg>



                                    ${savings.toFixed(1)}%



                                    below target







                                </span>







                            `;







                        } else {







                            const difference =



                                (



                                    (



                                        currentPrice -



                                        targetPrice



                                    ) /



                                    targetPrice



                                ) * 100;











                            priceStatus = `







                                <span class="price-waiting">







                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;display:inline-block;"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>



                                    ${difference.toFixed(1)}%



                                    above target







                                </span>







                            `;







                        }







                    }











                    const currentPriceText =



                        flight.last_price !== null &&



                            flight.last_price !== undefined







                            ? `₹${currentPrice.toLocaleString(



                                "en-IN"



                            )}`







                            : "Waiting";











                    const lowestPriceText =



                        flight.lowest_price !== null &&



                            flight.lowest_price !== undefined







                            ? `₹${Number(



                                flight.lowest_price



                            ).toLocaleString(



                                "en-IN"



                            )}`







                            : "-";



                    return `

    <div class="tracked-row">
        <!-- ROUTE -->
        <div class="tracked-route">
            <div class="tracked-route-codes">
                <strong>
                    ${escapeHtml(flight.departure)}
                </strong>
                <span class="tracked-arrow" aria-hidden="true">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <line x1="5" y1="12" x2="19" y2="12"></line>
                        <polyline points="12 5 19 12 12 19"></polyline>
                    </svg>
                </span>
                <strong>
                    ${escapeHtml(flight.arrival)}
                </strong>
            </div>
            <div class="tracked-meta">
                ${escapeHtml(flight.outbound_date)}
                <span>·</span>
                Target ₹${Number(
                        flight.target_price
                    ).toLocaleString("en-IN")}
            </div>
        </div>

        <!-- PRICE INFORMATION -->
        <div class="tracked-info">
            <div class="tracked-current-price">
                ${currentPriceText}
            </div>
            ${priceStatus}
            <div class="tracked-lowest">
                <span>Lowest:</span>
                <strong>${lowestPriceText}</strong>
            </div>
            <div class="tracked-actions">
                <button
                    type="button"
                    class="history-btn"
                    data-id="${flight.id}"
                >
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                        <polyline points="22 7 13.5 15.5 8.5 10.5 2 17"></polyline>
                        <polyline points="16 7 22 7 22 13"></polyline>
                    </svg>
                    <span>View Price History</span>
                </button>
                <button
                    type="button"
                    class="delete-track-btn"
                    data-id="${flight.id}"
                    aria-label="Remove flight from tracking"
                    title="Remove flight from tracking"
                >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                        <polyline points="3 6 5 6 21 6"></polyline>
                        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                    </svg>
                </button>
            </div>
        </div>
    </div>



    </div>





    <div

        id="history-${flight.id}"

        class="history-container"

    ></div>

`;











                }



            ).join("");











        document



            .querySelectorAll(".history-btn")



            .forEach(button => {







                button.addEventListener(



                    "click",



                    () => {







                        showHistory(



                            button.dataset.id



                        );







                    }



                );






            });

        document.querySelectorAll(".delete-track-btn").forEach(button => {
            button.addEventListener("click", async () => {
                const id = button.dataset.id;
                if (!id) return;
                const token = getDeleteToken(id);
                if (!token) {
                    alert("Authorization token not found on this device for flight #" + id + ". Only the user on the original device who started tracking can delete this route.");
                    return;
                }
                try {
                    button.disabled = true;
                    button.style.opacity = "0.5";
                    const res = await fetch(`/api/tracked/${id}`, {
                        method: "DELETE",
                        headers: {
                            "X-Delete-Token": token
                        }
                    });
                    if (res.ok) {
                        removeDeleteToken(id);
                        loadTracked();
                    } else {
                        const errData = await res.json().catch(() => ({}));
                        alert(errData.error || "Failed to delete tracked flight.");
                        button.disabled = false;
                        button.style.opacity = "1";
                    }
                } catch (err) {
                    console.error("Failed to delete tracked flight:", err);
                    button.disabled = false;
                    button.style.opacity = "1";
                }
            });
        });
    } catch (error) {







        console.error(



            "Tracked flights error:",



            error



        );











        $("tracked").innerHTML = `



            <p class="status">



                Could not load tracked flights.



            </p>



        `;







    }







}











/* =========================================================



   PRICE HISTORY



========================================================= */







async function showHistory(id) {







    const container =



        document.getElementById(



            `history-${id}`



        );











    if (!container) {



        return;



    }











    container.innerHTML = `







        <p class="status">



            Loading price history...



        </p>







        <canvas



            id="chart-${id}"



        ></canvas>







    `;











    try {







        const token = getDeleteToken(id);
        const headers = {};
        if (token) {
            headers["X-Delete-Token"] = token;
        }

        const response = await fetch(`/api/history/${id}`, { headers });











        if (!response.ok) {







            throw new Error(



                "Could not load price history."



            );







        }











        const history =



            await response.json();











        if (!history.length) {







            container.innerHTML = `







                <p class="status">







                    No price history yet.







                    <br>







                    Run the price checker



                    to collect prices.







                </p>







            `;







            return;



        }











        const labels =



            history.map(



                item => {







                    const date =



                        new Date(



                            item.checked_at



                        );











                    return date.toLocaleString(



                        "en-IN",



                        {



                            day:



                                "2-digit",







                            month:



                                "short",







                            hour:



                                "2-digit",







                            minute:



                                "2-digit"



                        }



                    );







                }



            );











        const prices =



            history.map(



                item =>



                    Number(



                        item.price



                    )



            );











        const canvas =



            document.getElementById(



                `chart-${id}`



            );











        if (!canvas) {



            return;



        }











        new Chart(



            canvas,



            {







                type: "line",







                data: {







                    labels,







                    datasets: [{







                        label:



                            "Flight Price (₹)",







                        data:



                            prices,







                        tension:



                            0.3,







                        borderWidth:



                            3,







                        pointRadius:



                            4,







                        fill:



                            false







                    }]







                },







                options: {







                    responsive:



                        true,







                    plugins: {







                        legend: {



                            display: true



                        }







                    },







                    scales: {







                        y: {







                            beginAtZero:



                                false,







                            ticks: {







                                callback:



                                    value =>



                                        "₹" +



                                        Number(



                                            value



                                        ).toLocaleString(



                                            "en-IN"



                                        )







                            }







                        }







                    }







                }







            }



        );











    } catch (error) {







        console.error(



            "History error:",



            error



        );











        container.innerHTML = `







            <p class="status">







                Could not load price history.







            </p>







        `;







    }







}











/* =========================================================



   DATE VALIDATION



========================================================= */







function setupDateValidation() {







    const departure =



        $("outbound_date");







    const returnDate =



        $("return_date");











    if (!departure) {



        return;



    }











    const today =



        new Date();











    const year =



        today.getFullYear();











    const month =



        String(



            today.getMonth() + 1



        ).padStart(



            2,



            "0"



        );











    const day =



        String(



            today.getDate()



        ).padStart(



            2,



            "0"



        );











    const todayString =



        `${year}-${month}-${day}`;











    departure.min =



        todayString;











    if (returnDate) {







        returnDate.min =



            todayString;











        departure.addEventListener(



            "change",



            () => {







                returnDate.min =



                    departure.value ||



                    todayString;







            }



        );







    }







}











/* =========================================================



   ESCAPE HTML



========================================================= */







function escapeHtml(value) {







    return String(



        value ?? ""



    ).replace(



        /[&<>"']/g,



        character => ({







            "&":



                "&amp;",







            "<":



                "&lt;",







            ">":



                "&gt;",







            '"':



                "&quot;",







            "'":



                "&#039;"







        }[character])



    );







}











/* =========================================================



   INITIALIZE



========================================================= */







setupDateValidation();

loadTracked();


/* =========================================================
   MULTI-PROVIDER FLIGHT PRICE COMPARISON
========================================================= */

let latestComparisonData = null;
let currentProviderFilter = "all";
let currentComparisonSort = "cheapest";
let currentComparisonView = "grouped";
let currentSearchContext = null;
let selectedFlightForComparison = null;

function buildAirlineDirectUrl(airline, dep, arr, date, retDate) {
    const map = {
        "indigo": "https://www.goindigo.in",
        "air india": "https://www.airindia.com",
        "vistara": "https://www.airvistara.com",
        "akasa": "https://www.akasaair.com",
        "spicejet": "https://www.spicejet.com"
    };
    const key = Object.keys(map).find(k => (airline || "").toLowerCase().includes(k));
    if (key) {
        if (retDate) return `${map[key]}?origin=${dep}&destination=${arr}&departureDate=${date}&returnDate=${retDate}`;
        return `${map[key]}?origin=${dep}&destination=${arr}&departureDate=${date}`;
    }
    return `https://www.google.com/travel/flights?q=${encodeURIComponent((airline || '') + ' ' + dep + ' to ' + arr + ' ' + date)}`;
}

function buildMakeMyTripUrl(dep, arr, date, retDate) {
    if (retDate) {
        return `https://www.makemytrip.com/flight/search?itinerary=${dep}-${arr}-${date}_${arr}-${dep}-${retDate}&tripType=R&paxType=A-1_C-0_I-0`;
    }
    return `https://www.makemytrip.com/flight/search?itinerary=${dep}-${arr}-${date}&tripType=O&paxType=A-1_C-0_I-0`;
}

function buildCleartripUrl(dep, arr, date, retDate) {
    if (retDate) {
        return `https://www.cleartrip.com/flights/results?from=${dep}&to=${arr}&depart_date=${date}&return_date=${retDate}&adults=1`;
    }
    return `https://www.cleartrip.com/flights/results?from=${dep}&to=${arr}&depart_date=${date}&adults=1`;
}

function buildEaseMyTripUrl(dep, arr) {
    return `https://www.easemytrip.com/flights/${dep.toLowerCase()}-${arr.toLowerCase()}-flights.html`;
}

function buildExpediaUrl(dep, arr, date, retDate) {
    if (retDate) {
        return `https://www.expedia.co.in/Flights-Search?flight-type=roundtrip&mode=search&trip=roundtrip&leg1=from:${dep},to:${arr},departure:${date}TANYT&leg2=from:${arr},to:${dep},departure:${retDate}TANYT&passengers=adults:1`;
    }
    return `https://www.expedia.co.in/Flights-Search?flight-type=on&mode=search&trip=oneway&leg1=from:${dep},to:${arr},departure:${date}TANYT&passengers=adults:1`;
}

function buildGoogleFlightsUrl(airline, dep, arr, date) {
    return `https://www.google.com/travel/flights?q=${encodeURIComponent((airline || '') + ' flights from ' + dep + ' to ' + arr + ' ' + date)}`;
}

async function openExactFlightComparison(flight) {
    if (!flight) return;
    selectedFlightForComparison = flight;

    try {
        sessionStorage.setItem("skytrack_selected_flight", JSON.stringify(flight));
    } catch (e) {}

    const compSection = $("comparison-section");
    if (!compSection) {
        // When on /flights or another standalone page, navigate directly to /compare
        // preserving exact flight payload in sessionStorage and route query params in URL
        const dep = flight.departure_airport || (currentSearchContext?.departureCode) || $("departure")?.dataset?.iata || $("departure")?.value?.trim() || "";
        const arr = flight.arrival_airport || (currentSearchContext?.arrivalCode) || $("arrival")?.dataset?.iata || $("arrival")?.value?.trim() || "";
        const date = currentSearchContext?.departureDate || $("outbound_date")?.value || "";
        const retDate = currentSearchContext?.returnDate || $("return_date")?.value || "";
        const tripType = flight.trip_type || currentSearchContext?.tripType || (retDate ? "round_trip" : "one_way");
        const passengers = currentSearchContext?.passengers || $("passengers")?.value || "1";
        const travelClass = currentSearchContext?.travelClass || $("travelClass")?.value || "1";
        const itinId = flight.itinerary_id || flight.flight_number || "";

        const params = new URLSearchParams({
            departure: dep ? dep.toUpperCase() : "",
            arrival: arr ? arr.toUpperCase() : "",
            outbound_date: date,
            trip_type: tripType,
            passengers: String(passengers),
            travel_class: String(travelClass),
            itinerary_id: itinId,
            selected: "true"
        });
        if (retDate) params.set("return_date", retDate);
        window.location.href = `/compare?${params.toString()}`;
        return;
    }
    compSection.style.display = "block";

    const navCompare = $("navCompareLink");
    if (navCompare) navCompare.style.display = "inline-flex";

    // Show comparison navbar with back button
    const navBar = $("comparisonNavBar");
    if (navBar) navBar.style.display = "block";

    // Hide general comparison views so user sees dedicated exact flight comparison
    const allHeader = $("allComparisonsHeader");
    const compControls = $("comparisonViewControl") ? $("comparisonViewControl").closest(".comparison-controls-bar") : null;
    const transparencyCard = $("providerTransparencyCard");
    const filterChips = $("providerFilterChips") ? $("providerFilterChips").closest(".provider-filter-wrapper") : null;
    const groupedContainer = $("comparisonGroupedContainer");
    const tableContainer = $("comparisonTableContainer");

    if (allHeader) allHeader.style.display = "none";
    if (compControls) compControls.style.display = "none";
    if (transparencyCard) transparencyCard.style.display = "none";
    if (filterChips) filterChips.style.display = "none";
    if (groupedContainer) groupedContainer.style.display = "none";
    if (tableContainer) tableContainer.style.display = "none";

    // Update URL hash for exact itinerary
    try {
        const flightKey = flight.itinerary_id || flight.flight_number || "flight";
        history.pushState({ flightKey }, "", window.location.pathname + "#compare=" + encodeURIComponent(flightKey));
    } catch (e) {}

    // 1. Render Selected Flight Summary Card
    const summaryCard = $("selectedFlightSummaryCard");
    if (summaryCard) {
        summaryCard.style.display = "block";

        const depCode = flight.departure_airport || (currentSearchContext?.departureCode) || $("departure")?.dataset?.iata || "";
        const arrCode = flight.arrival_airport || (currentSearchContext?.arrivalCode) || $("arrival")?.dataset?.iata || "";

        const depInfo = await getAirportInfo(depCode);
        const arrInfo = await getAirportInfo(arrCode);

        const depCity = depInfo?.city || depInfo?.name || depCode;
        const arrCity = arrInfo?.city || arrInfo?.name || arrCode;

        const airline = flight.airline || "Airline";
        const flightNo = flight.flight_number || (flight.segments?.[0]?.flight_number) || `${airline} Express`;
        const depTime = flight.departure_time || "--:--";
        const arrTime = flight.arrival_time || "--:--";
        const durationFormatted = formatDuration(flight.duration);
        const stopsLabel = flight.stops === 0 ? "Non-stop" : `${flight.stops} stop${flight.stops > 1 ? "s" : ""}`;

        const travelDate = currentSearchContext?.departureDate || $("outbound_date")?.value || "Selected travel date";
        const passengersCount = currentSearchContext?.passengers || $("passengers")?.value || "1";
        const travelClassText = ($("travelClass")?.selectedOptions?.[0]?.textContent) || "Economy";

        const isMultiCity = String(flight.trip_type || "").toLowerCase() === "multi_city" || (Array.isArray(flight.legs) && flight.legs.length > 0);
        const isRoundTrip = !isMultiCity && (flight.trip_type === "round_trip" || !!flight.return);

        let returnSummaryHtml = "";
        if (isRoundTrip && (flight.return || flight.return_departure_time)) {
            const ret = flight.return || flight;
            const retDepTime = ret.departure_time || flight.return_departure_time || "--:--";
            const retArrTime = ret.arrival_time || flight.return_arrival_time || "--:--";
            const retDate = currentSearchContext?.returnDate || $("return_date")?.value || "Return date";
            const retAirline = ret.airline || flight.return_airline || airline;
            const retFlightNo = ret.flight_number || (ret.segments?.[0]?.flight_number) || `${retAirline}`;

            returnSummaryHtml = `
                <div style="margin-top: 18px; padding-top: 16px; border-top: 1px dashed var(--border-subtle);">
                    <div style="display: flex; align-items: center; gap: 8px; font-size: 0.8rem; font-weight: 700; color: var(--text-tertiary); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 8px;">
                        <span>Return Journey: ${escapeHtml(arrCode)} → ${escapeHtml(depCode)}</span>
                    </div>
                    <div style="display: flex; justify-content: space-between; flex-wrap: wrap; gap: 12px; font-size: 0.92rem;">
                        <div>
                            <strong>${escapeHtml(retAirline)}</strong> • ${escapeHtml(retFlightNo)}
                        </div>
                        <div>
                            <strong>${escapeHtml(retDepTime)}</strong> (${escapeHtml(arrCode)}) → <strong>${escapeHtml(retArrTime)}</strong> (${escapeHtml(depCode)})
                        </div>
                        <div style="color: var(--text-secondary);">
                            ${escapeHtml(retDate)} • ${formatDuration(flight.return_duration || flight.duration)}
                        </div>
                    </div>
                </div>
            `;
        }

        let multiCitySummaryHtml = "";
        if (isMultiCity && Array.isArray(flight.legs) && flight.legs.length > 0) {
            multiCitySummaryHtml = `
                <div style="margin-top: 18px; padding-top: 16px; border-top: 1px dashed var(--border-subtle);">
                    <div style="display: flex; align-items: center; gap: 8px; font-size: 0.8rem; font-weight: 700; color: var(--text-tertiary); text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 12px;">
                        <span>Multi-City Itinerary (${flight.legs.length} Legs)</span>
                    </div>
                    <div style="display: flex; flex-direction: column; gap: 10px;">
                        ${flight.legs.map((leg, idx) => `
                            <div style="display: flex; justify-content: space-between; align-items: center; background: rgba(255,255,255,0.02); border: 1px solid var(--border-subtle); padding: 10px 14px; border-radius: 8px; font-size: 0.92rem; flex-wrap: wrap; gap: 8px;">
                                <div>
                                    <strong style="color: var(--accent);">Leg ${idx + 1}:</strong> <strong>${escapeHtml(leg.departure_airport || "")}</strong> → <strong>${escapeHtml(leg.arrival_airport || "")}</strong>
                                    <span style="color: var(--text-secondary); margin-left: 8px;">${escapeHtml(leg.airline || airline)}</span>
                                </div>
                                <div style="color: var(--text-secondary); font-size: 0.85rem;">
                                    ${escapeHtml(leg.date || "")} • ${escapeHtml(leg.departure_time || "--:--")} → ${escapeHtml(leg.arrival_time || "--:--")} • ${formatDuration(leg.duration)}
                                </div>
                            </div>
                        `).join("")}
                    </div>
                </div>
            `;
        }

        const routeDisplayTitle = isMultiCity && Array.isArray(flight.legs) && flight.legs.length > 0
            ? flight.legs.map(l => l.departure_airport).concat(flight.legs[flight.legs.length - 1].arrival_airport).join(" → ")
            : `${depCity} (${depCode}) → ${arrCity} (${arrCode})`;

        summaryCard.innerHTML = `
            <div class="selected-flight-header-row">
                <div>
                    <div class="section-label" style="margin-bottom: 4px;">SELECTED ITINERARY</div>
                    <h2 class="selected-flight-route-title">${escapeHtml(routeDisplayTitle)}</h2>
                    <div class="selected-flight-subhead">
                        <span>${escapeHtml(airline)}</span>
                        <span>•</span>
                        <span>${isMultiCity ? `${flight.legs.length} Legs` : `Departure ${escapeHtml(depTime)}`}</span>
                        <span>•</span>
                        <span>${isMultiCity ? "Combined Itinerary" : `Flight ${escapeHtml(flightNo)}`}</span>
                    </div>
                </div>
                <div class="selected-flight-price-block">
                    <div class="selected-flight-price-label">Reference Airline Fare</div>
                    <div class="selected-flight-price-val">₹${Number(flight.price).toLocaleString("en-IN")}</div>
                </div>
            </div>

            <div class="selected-flight-grid">
                <div class="selected-flight-spec-item">
                    <span class="selected-flight-spec-label">Flight Number</span>
                    <span class="selected-flight-spec-val">${escapeHtml(flightNo)}</span>
                </div>
                <div class="selected-flight-spec-item">
                    <span class="selected-flight-spec-label">Departure</span>
                    <span class="selected-flight-spec-val">${escapeHtml(depTime)} · ${escapeHtml(depCode)}</span>
                </div>
                <div class="selected-flight-spec-item">
                    <span class="selected-flight-spec-label">Arrival</span>
                    <span class="selected-flight-spec-val">${escapeHtml(arrTime)} · ${escapeHtml(arrCode)}</span>
                </div>
                <div class="selected-flight-spec-item">
                    <span class="selected-flight-spec-label">Travel Date</span>
                    <span class="selected-flight-spec-val">${escapeHtml(travelDate)}</span>
                </div>
                <div class="selected-flight-spec-item">
                    <span class="selected-flight-spec-label">Duration</span>
                    <span class="selected-flight-spec-val">${escapeHtml(durationFormatted)}</span>
                </div>
                <div class="selected-flight-spec-item">
                    <span class="selected-flight-spec-label">Stops</span>
                    <span class="selected-flight-spec-val">${escapeHtml(stopsLabel)}</span>
                </div>
                <div class="selected-flight-spec-item">
                    <span class="selected-flight-spec-label">Passengers & Class</span>
                    <span class="selected-flight-spec-val">${escapeHtml(passengersCount)} Passenger${passengersCount > 1 ? 's' : ''} • ${escapeHtml(travelClassText)}</span>
                </div>
                <div class="selected-flight-spec-item">
                    <span class="selected-flight-spec-label">Baggage Allowance</span>
                    <span class="selected-flight-spec-val">Cabin: 7 kg • Check-in: 15 kg</span>
                </div>
            </div>

            ${returnSummaryHtml}
            ${multiCitySummaryHtml}
        `;
    }

    // 2. Filter or collect provider offers for this EXACT flight
    let exactOffers = [];
    if (Array.isArray(flight.comparison_offers) && flight.comparison_offers.length > 0) {
        exactOffers = [...flight.comparison_offers];
    } else if (latestComparisonData && Array.isArray(latestComparisonData.grouped_itineraries)) {
        const group = latestComparisonData.grouped_itineraries.find(g =>
            g.airline.toLowerCase() === (flight.airline || "").toLowerCase() &&
            (g.departure_time === flight.departure_time || g.flight_number === flight.flight_number) &&
            g.departure_airport === flight.departure_airport &&
            g.arrival_airport === flight.arrival_airport
        );
        if (group && Array.isArray(group.offers) && group.offers.length > 0) {
            exactOffers = [...group.offers];
        }
    }

    // Include primary flight offer if valid and not already present
    const basePrice = Number(flight.price);
    if (!isNaN(basePrice) && basePrice > 0) {
        const primaryProvider = flight.source || flight.airline || "Carrier";
        const alreadyHasPrimary = exactOffers.some(o =>
            (o.provider === primaryProvider || o.provider_name === primaryProvider) && Math.abs(Number(o.price) - basePrice) < 1
        );
        if (!alreadyHasPrimary) {
            exactOffers.unshift({
                provider_id: "carrier_primary",
                provider: primaryProvider,
                provider_name: primaryProvider,
                provider_type: "airline_direct",
                airline: flight.airline || "Carrier",
                flight_number: flight.flight_number || "",
                departure_airport: flight.departure_airport || "",
                arrival_airport: flight.arrival_airport || "",
                departure_time: flight.departure_time || "",
                arrival_time: flight.arrival_time || "",
                duration: flight.duration || 0,
                stops: flight.stops || 0,
                price: basePrice,
                currency: flight.currency || "INR",
                baggage: flight.baggage || "Cabin: 7 kg included",
                booking_url: flight.booking_url || "",
                is_live: flight.is_live,
                data_status: flight.data_status
            });
        }
    }

    const exactContainer = $("exactFlightOffersContainer");
    const exactHeader = $("exactOffersHeader");
    if (exactHeader) exactHeader.style.display = "block";

    // If completely empty, show authentic empty state without fabricated prices
    if (exactOffers.length === 0) {
        if (exactContainer) {
            exactContainer.style.display = "block";
            exactContainer.innerHTML = `
                <div class="empty-comparison-state font-mono" style="padding: 40px; text-align: center; border: 1px solid var(--border-subtle); border-radius: var(--radius-md); background: rgba(255, 255, 255, 0.02); color: var(--text-secondary); margin-top: 16px;">
                    <p style="margin-bottom: 8px; font-size: 0.95rem; color: var(--text-primary); font-weight: 600;">No vendor comparison quotes available</p>
                    <p style="font-size: 0.8rem;">No additional third-party booking options were returned by providers for this specific flight schedule.</p>
                </div>
            `;
        }
        compSection.scrollIntoView({ behavior: "smooth", block: "start" });
        return;
    }

    // Sort ascending by price
    exactOffers.sort((a, b) => (Number(a.price) || Infinity) - (Number(b.price) || Infinity));

    const minPrice = exactOffers[0].price;
    const tiedCount = exactOffers.filter(o => o.price === minPrice).length;

    // Render Exact Offers Table
    if (exactHeader) exactHeader.style.display = "block";
    if (exactContainer) {
        exactContainer.style.display = "block";

        const rowsHtml = exactOffers.map(offer => {
            const isBest = offer.price === minPrice;
            const diff = offer.price - minPrice;

            let badgeHtml = "";
            if (isBest) {
                if (tiedCount > 1) {
                    badgeHtml = `<span class="tied-price-badge"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg> Tied best price</span>`;
                } else {
                    badgeHtml = `<span class="best-price-badge"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg> Best price</span>`;
                }
            } else if (diff > 0) {
                badgeHtml = `<span class="price-diff-badge">+₹${Number(diff).toLocaleString("en-IN")}</span>`;
            }

            return `
                <tr class="${isBest ? 'best-offer-row' : ''}">
                    <td>
                        <div class="provider-name-cell">
                            ${getProviderVisual(offer.provider_id, offer.provider_name)}
                            <div style="display: flex; flex-direction: column;">
                                <strong style="color: var(--text-primary); font-size: 0.92rem;">${escapeHtml(offer.provider_name)}</strong>
                                <small style="color: var(--text-tertiary); font-size: 0.74rem;">${offer.provider_type === 'airline_direct' ? 'Official Carrier Website' : (offer.provider_type === 'ota' ? 'Online Travel Portal' : 'Search Aggregator')}</small>
                            </div>
                        </div>
                    </td>
                    <td class="baggage-cell">
                        ${escapeHtml(offer.baggage || "Standard baggage")}
                    </td>
                    <td>
                        <span class="price-cell" style="font-size: 1.05rem; font-weight: 700;">₹${Number(offer.price).toLocaleString("en-IN")}</span>
                    </td>
                    <td>
                        ${badgeHtml}
                    </td>
                    <td style="text-align: right;">
                        <a href="${escapeHtml(offer.booking_url || '#')}" target="_blank" rel="noopener noreferrer" class="view-deal-btn ${isBest ? 'best-action' : ''}" title="View deal on ${escapeHtml(offer.provider_name)}">
                            <span>View deal</span>
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path><polyline points="15 3 21 3 21 9"></polyline><line x1="10" y1="14" x2="21" y2="3"></line></svg>
                        </a>
                    </td>
                </tr>
            `;
        }).join("");

        exactContainer.innerHTML = `
            <div class="comparison-table-wrapper exact-offers-table-wrapper">
                <table class="comparison-table exact-offers-table">
                    <thead>
                        <tr>
                            <th>Booking Provider</th>
                            <th>Baggage Allowance</th>
                            <th>Total Price</th>
                            <th>Price Comparison</th>
                            <th style="text-align: right;">Action</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${rowsHtml}
                    </tbody>
                </table>
            </div>
            <div class="provider-disclosure-note">
                Prices shown are retrieved for 1 passenger and include estimated mandatory carrier surcharges and taxes. Fares and seat availability are verified upon redirection to the booking platform.
            </div>
        `;
    }

    // Scroll smoothly to comparison section
    compSection.scrollIntoView({ behavior: "smooth", block: "start" });
}

function formatDuration(minutes) {
    const total = Number(minutes) || 0;
    const hours = Math.floor(total / 60);
    const mins = total % 60;
    if (hours > 0 && mins > 0) return `${hours}h ${mins}m`;
    if (hours > 0) return `${hours}h`;
    return `${mins}m`;
}

function getProviderVisual(providerId, providerName) {
    const id = String(providerId || "").toLowerCase();
    if (id.includes("airline") || id.includes("indigo") || id.includes("air_india") || id.includes("vistara") || id.includes("spicejet")) {
        return `<span class="provider-icon-badge" title="Official Airline Portal">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.8 19.2 16 11l3.5-3.5C21 6 21.5 4 21 3c-1-.5-3 0-4.5 1.5L13 8 4.8 6.2c-.5-.1-.9.1-1.1.5l-.3.5c-.2.5-.1 1 .3 1.3L9 12l-2 3H4l-1 1 3 2 2 3 1-1v-3l3-2 3.5 5.3c.3.4.8.5 1.3.3l.5-.3c.4-.2.6-.6.5-1.1z"/></svg>
        </span>`;
    }
    if (id.includes("google")) {
        return `<span class="provider-icon-badge" title="Google Flights">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="2" y1="12" x2="22" y2="12"></line><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path></svg>
        </span>`;
    }
    if (id.includes("amadeus")) {
        return `<span class="provider-icon-badge" title="Amadeus GDS">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="7" width="20" height="14" rx="2" ry="2"></rect><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"></path></svg>
        </span>`;
    }
    return `<span class="provider-icon-badge" title="Online Travel Agency">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4Z"></path><path d="M3 6h18"></path><path d="M16 10a4 4 0 0 1-8 0"></path></svg>
    </span>`;
}

function renderProviderComparison(comparison) {
    if (!comparison) return;
    latestComparisonData = comparison;

    const compSection = $("comparison-section");
    const navCompare = $("navCompareLink");
    if (compSection) compSection.style.display = "block";
    if (navCompare) navCompare.style.display = "inline-flex";

    const totalOffers = comparison.stats?.total_offers || 0;
    const activeProviders = comparison.stats?.active_providers_count || 0;
    const maxSavings = comparison.stats?.max_savings || 0;
    const lowestPrice = comparison.stats?.lowest_price || 0;

    const countSummary = $("providerCountSummary");
    if (countSummary) {
        countSummary.textContent = `Compared ${totalOffers} offers across ${activeProviders} active booking providers`;
    }

    const savingsText = $("savingsAmountText");
    if (savingsText) {
        if (maxSavings > 0) {
            savingsText.textContent = `Save up to ₹${Number(maxSavings).toLocaleString("en-IN")} vs highest provider`;
        } else if (lowestPrice > 0) {
            savingsText.textContent = `Best comparable fare ₹${Number(lowestPrice).toLocaleString("en-IN")}`;
        }
    }

    // Provider status chips
    const statusChipsEl = $("providerStatusChips");
    if (statusChipsEl && Array.isArray(comparison.providers_summary)) {
        statusChipsEl.innerHTML = comparison.providers_summary.map(p => {
            let meta = "Active";
            if (p.status === "unconfigured") meta = "Requires API key";
            else if (p.status === "rate_limited") meta = "Rate limited";
            else if (p.status === "error") meta = "Offline";
            else if (p.offers_count) meta = `${p.offers_count} offers`;

            return `
                <div class="provider-status-chip status-${escapeHtml(p.status)}" title="${escapeHtml(p.message || '')}">
                    <span class="status-dot"></span>
                    <span>${escapeHtml(p.provider_name)}</span>
                    <span style="opacity: 0.7; font-size: 0.72rem;">(${meta})</span>
                </div>
            `;
        }).join("");
    }

    // Provider notice message for limited/unconfigured sources
    const noticeEl = $("providerNoticeMessage");
    if (noticeEl && Array.isArray(comparison.providers_summary)) {
        const notices = [];
        comparison.providers_summary.forEach(p => {
            if (p.status === "unconfigured") {
                notices.push(`${p.provider_name} is unconfigured (${p.message}). Showing direct carrier & partner fare estimates.`);
            } else if (p.status === "rate_limited") {
                notices.push(`${p.provider_name}: ${p.message}`);
            }
        });
        if (notices.length > 0) {
            noticeEl.style.display = "block";
            noticeEl.innerHTML = notices.map(n => `<div>• ${escapeHtml(n)}</div>`).join("");
        } else {
            noticeEl.style.display = "none";
        }
    }

    // Render provider filter chips
    renderProviderFilterChips();

    // Render content
    renderComparisonContent();
}

function renderProviderFilterChips() {
    const filterContainer = $("providerFilterChips");
    if (!filterContainer || !latestComparisonData) return;

    const allOffers = latestComparisonData.all_offers || [];
    const providersMap = new Map();
    allOffers.forEach(o => {
        if (o.provider_id && !providersMap.has(o.provider_id)) {
            providersMap.set(o.provider_id, o.provider_name);
        }
    });

    let html = `
        <button type="button" class="provider-chip ${currentProviderFilter === 'all' ? 'active' : ''}" data-provider="all">
            All providers (${allOffers.length})
        </button>
    `;

    providersMap.forEach((name, id) => {
        const count = allOffers.filter(o => o.provider_id === id).length;
        html += `
            <button type="button" class="provider-chip ${currentProviderFilter === id ? 'active' : ''}" data-provider="${escapeHtml(id)}">
                ${escapeHtml(name)} (${count})
            </button>
        `;
    });

    filterContainer.innerHTML = html;

    filterContainer.querySelectorAll(".provider-chip").forEach(btn => {
        btn.addEventListener("click", () => {
            filterContainer.querySelectorAll(".provider-chip").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            currentProviderFilter = btn.dataset.provider;
            renderComparisonContent();
        });
    });
}

function renderComparisonContent() {
    if (!latestComparisonData) return;

    if (currentComparisonView === "grouped") {
        renderGroupedComparison();
    } else {
        renderTableComparison();
    }
}

function renderGroupedComparison() {
    const groupedContainer = $("comparisonGroupedContainer");
    const tableContainer = $("comparisonTableContainer");
    if (!groupedContainer) return;

    groupedContainer.style.display = "block";
    if (tableContainer) tableContainer.style.display = "none";

    let groups = [...(latestComparisonData.grouped_itineraries || [])];

    // Filter by provider if selected
    if (currentProviderFilter !== "all") {
        groups = groups.map(group => {
            const matchedOffers = group.offers.filter(o => o.provider_id === currentProviderFilter);
            if (matchedOffers.length === 0) return null;
            return {
                ...group,
                offers: matchedOffers,
                lowest_price: matchedOffers[0].price,
            };
        }).filter(Boolean);
    }

    // Sort groups
    if (currentComparisonSort === "cheapest") {
        groups.sort((a, b) => a.lowest_price - b.lowest_price);
    } else if (currentComparisonSort === "fastest") {
        groups.sort((a, b) => a.duration - b.duration);
    }

    if (groups.length === 0) {
        groupedContainer.innerHTML = `
            <div class="no-results" style="padding: 32px 20px; text-align: center;">
                <p class="status">No provider offers match the selected provider filter.</p>
            </div>
        `;
        return;
    }

    groupedContainer.innerHTML = groups.map(group => {
        const stopsLabel = group.stops === 0 ? "Non-stop" : `${group.stops} stop${group.stops > 1 ? "s" : ""}`;
        const durationFormatted = formatDuration(group.duration);

        const offersRows = group.offers.map(offer => {
            const isBest = offer.is_best_price;
            const diffHtml = isBest
                ? `<span class="best-price-badge">
                    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>
                    Best price
                   </span>`
                : (offer.price_diff > 0 ? `<span class="price-diff-badge">+₹${Number(offer.price_diff).toLocaleString("en-IN")}</span>` : "");

            return `
                <tr class="${isBest ? 'best-offer-row' : ''}">
                    <td>
                        <div class="provider-name-cell">
                            ${getProviderVisual(offer.provider_id, offer.provider_name)}
                            <span>${escapeHtml(offer.provider_name)}</span>
                        </div>
                    </td>
                    <td class="baggage-cell">
                        ${escapeHtml(offer.baggage || "Standard baggage")}
                    </td>
                    <td>
                        <div style="display: flex; align-items: center; gap: 8px;">
                            <span class="price-cell">₹${Number(offer.price).toLocaleString("en-IN")}</span>
                            ${diffHtml}
                        </div>
                    </td>
                    <td style="text-align: right;">
                        <a href="${escapeHtml(offer.booking_url || '#')}" target="_blank" rel="noopener noreferrer" class="book-offer-btn ${isBest ? 'best-action' : ''}">
                            <span>Book</span>
                            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path><polyline points="15 3 21 3 21 9"></polyline><line x1="10" y1="14" x2="21" y2="3"></line></svg>
                        </a>
                    </td>
                </tr>
            `;
        }).join("");

        return `
            <div class="comparison-flight-group">
                <div class="group-header">
                    <div class="group-flight-meta">
                        <span class="group-airline-title">${escapeHtml(group.airline)}</span>
                        ${group.flight_number ? `<span class="group-flight-number">${escapeHtml(group.flight_number)}</span>` : ''}
                        <span class="badge" style="background: var(--bg-elevated); border: 1px solid var(--border-subtle); padding: 3px 8px; border-radius: var(--radius-pill); font-size: 0.74rem; font-weight: 600;">
                            ${stopsLabel}
                        </span>
                    </div>

                    <div class="group-flight-times">
                        <div style="text-align: right;">
                            <span class="group-time">${escapeHtml(group.departure_time || '--:--')}</span>
                            <span class="group-route-code">${escapeHtml(group.departure_airport)}</span>
                        </div>

                        <div class="group-flight-duration">
                            <span class="group-duration-text">${durationFormatted}</span>
                            <div style="width: 50px; height: 1.5px; background: var(--border-strong); position: relative;">
                                <div style="position: absolute; right: 0; top: -3px; width: 0; height: 0; border-top: 4px solid transparent; border-bottom: 4px solid transparent; border-left: 5px solid var(--text-tertiary);"></div>
                            </div>
                        </div>

                        <div>
                            <span class="group-time">${escapeHtml(group.arrival_time || '--:--')}</span>
                            <span class="group-route-code">${escapeHtml(group.arrival_airport)}</span>
                        </div>
                    </div>

                    <div class="group-best-summary">
                        <div>
                            <div style="font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.04em; color: var(--text-tertiary);">Best available fare</div>
                            <div class="group-lowest-price">₹${Number(group.lowest_price).toLocaleString("en-IN")}</div>
                        </div>
                        ${group.price_spread > 0 ? `<span class="group-savings-tag">Save ₹${Number(group.price_spread).toLocaleString("en-IN")}</span>` : ''}
                    </div>
                </div>

                <div class="group-offers-table-wrapper">
                    <table class="group-offers-table">
                        <thead>
                            <tr>
                                <th>Booking Provider</th>
                                <th>Baggage Allowance</th>
                                <th>Total Price</th>
                                <th style="text-align: right;">Action</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${offersRows}
                        </tbody>
                    </table>
                </div>
            </div>
        `;
    }).join("");
}

function renderTableComparison() {
    const groupedContainer = $("comparisonGroupedContainer");
    const tableContainer = $("comparisonTableContainer");
    const tbody = $("comparisonTableBody");
    if (!tableContainer || !tbody) return;

    if (groupedContainer) groupedContainer.style.display = "none";
    tableContainer.style.display = "block";

    let offers = [...(latestComparisonData.all_offers || [])];

    if (currentProviderFilter !== "all") {
        offers = offers.filter(o => o.provider_id === currentProviderFilter);
    }

    if (currentComparisonSort === "cheapest") {
        offers.sort((a, b) => a.price - b.price);
    } else if (currentComparisonSort === "fastest") {
        offers.sort((a, b) => a.duration - b.duration);
    }

    if (offers.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="7" style="text-align: center; padding: 30px; color: var(--text-secondary);">
                    No offers found for the selected provider filter.
                </td>
            </tr>
        `;
        return;
    }

    tbody.innerHTML = offers.map(offer => {
        const isBest = offer.is_best_price;
        const stopsLabel = offer.stops === 0 ? "Non-stop" : `${offer.stops} stop${offer.stops > 1 ? "s" : ""}`;
        const durationFormatted = formatDuration(offer.duration);

        return `
            <tr class="${isBest ? 'best-offer-row' : ''}">
                <td>
                    <div style="display: flex; flex-direction: column;">
                        <strong>${escapeHtml(offer.airline)}</strong>
                        <small style="color: var(--text-tertiary);">${escapeHtml(offer.flight_number || '')}</small>
                    </div>
                </td>
                <td>
                    <div style="display: flex; flex-direction: column;">
                        <span><strong>${escapeHtml(offer.departure_time)}</strong> → <strong>${escapeHtml(offer.arrival_time)}</strong></span>
                        <small style="color: var(--text-secondary);">${escapeHtml(offer.departure_airport)} - ${escapeHtml(offer.arrival_airport)} • ${durationFormatted}</small>
                    </div>
                </td>
                <td>
                    <span class="badge" style="background: var(--bg-elevated); padding: 3px 8px; border-radius: var(--radius-pill); font-size: 0.74rem;">
                        ${stopsLabel}
                    </span>
                </td>
                <td>
                    <div class="provider-name-cell">
                        ${getProviderVisual(offer.provider_id, offer.provider_name)}
                        <span>${escapeHtml(offer.provider_name)}</span>
                    </div>
                </td>
                <td class="baggage-cell">
                    ${escapeHtml(offer.baggage || "Standard allowance")}
                </td>
                <td>
                    <div style="display: flex; align-items: center; gap: 8px;">
                        <span class="price-cell">₹${Number(offer.price).toLocaleString("en-IN")}</span>
                        ${isBest ? `<span class="best-price-badge"><svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg> Best</span>` : ''}
                    </div>
                </td>
                <td>
                    <a href="${escapeHtml(offer.booking_url || '#')}" target="_blank" rel="noopener noreferrer" class="book-offer-btn ${isBest ? 'best-action' : ''}">
                        <span>Book</span>
                        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path><polyline points="15 3 21 3 21 9"></polyline><line x1="10" y1="14" x2="21" y2="3"></line></svg>
                    </a>
                </td>
            </tr>
        `;
    }).join("");
}

function initComparisonControls() {
    const btnGrouped = $("btnViewGrouped");
    const btnTable = $("btnViewTable");
    const sortSelect = $("sortComparison");
    const btnBack = $("btnBackToResults");

    if (btnGrouped && btnTable) {
        btnGrouped.addEventListener("click", () => {
            btnGrouped.classList.add("active");
            btnTable.classList.remove("active");
            currentComparisonView = "grouped";
            renderComparisonContent();
        });

        btnTable.addEventListener("click", () => {
            btnTable.classList.add("active");
            btnGrouped.classList.remove("active");
            currentComparisonView = "table";
            renderComparisonContent();
        });
    }

    if (sortSelect) {
        sortSelect.addEventListener("change", e => {
            currentComparisonSort = e.target.value;
            renderComparisonContent();
        });
    }

    if (btnBack) {
        btnBack.addEventListener("click", () => {
            // Hide exact flight comparison elements
            const summaryCard = $("selectedFlightSummaryCard");
            const exactHeader = $("exactOffersHeader");
            const exactContainer = $("exactFlightOffersContainer");
            if (summaryCard) summaryCard.style.display = "none";
            if (exactHeader) exactHeader.style.display = "none";
            if (exactContainer) exactContainer.style.display = "none";

            // Restore general comparison views if comparison data is available
            const allHeader = $("allComparisonsHeader");
            const compControls = $("comparisonViewControl") ? $("comparisonViewControl").closest(".comparison-controls-bar") : null;
            const transparencyCard = $("providerTransparencyCard");
            const filterChips = $("providerFilterChips") ? $("providerFilterChips").closest(".provider-filter-wrapper") : null;
            const groupedContainer = $("comparisonGroupedContainer");
            const tableContainer = $("comparisonTableContainer");

            if (latestComparisonData) {
                if (allHeader) allHeader.style.display = "";
                if (compControls) compControls.style.display = "";
                if (transparencyCard) transparencyCard.style.display = "";
                if (filterChips) filterChips.style.display = "";
                if (currentComparisonView === "grouped" && groupedContainer) groupedContainer.style.display = "block";
                if (currentComparisonView === "table" && tableContainer) tableContainer.style.display = "block";
            }

            // Update URL hash without reload
            try {
                history.pushState(null, "", window.location.pathname + "#results-section");
            } catch (e) {}

            // Scroll smoothly back to results
            const resultsSec = $("results-section");
            if (resultsSec) {
                resultsSec.scrollIntoView({ behavior: "smooth", block: "start" });
            }
        });
    }

    function checkHashNavigation() {
        const hash = window.location.hash || "";
        if (hash.startsWith("#compare=")) {
            const id = decodeURIComponent(hash.replace("#compare=", ""));
            let flight = allFlightResults.find(f => (f.itinerary_id === id || f.flight_number === id));
            if (!flight) {
                try {
                    const stored = sessionStorage.getItem("skytrack_selected_flight");
                    if (stored) {
                        const parsed = JSON.parse(stored);
                        if (parsed.itinerary_id === id || parsed.flight_number === id || id === "flight") {
                            flight = parsed;
                        }
                    }
                } catch (e) {}
            }
            if (flight) {
                openExactFlightComparison(flight);
            }
        }
    }

    window.addEventListener("hashchange", checkHashNavigation);
    setTimeout(checkHashNavigation, 300);
}

initComparisonControls();

/* =========================================================
   THEME SWITCHER SYSTEM (LIGHT DEFAULT & PERSISTENCE)
========================================================= */

function initThemeSystem() {
    if (window.SkyTrackerAppearance && typeof window.SkyTrackerAppearance.initNavbarToggle === "function") {
        window.SkyTrackerAppearance.initNavbarToggle();
        return;
    }

    const themeToggleBtn = $("themeToggle");
    if (!themeToggleBtn || themeToggleBtn.dataset.themeBound === "true") return;
    themeToggleBtn.dataset.themeBound = "true";

    function updateLabels(theme) {
        const isLight = theme === "light";
        themeToggleBtn.setAttribute("aria-label", isLight ? "Switch to dark theme" : "Switch to light theme");
        themeToggleBtn.setAttribute("title", isLight ? "Switch to dark theme" : "Switch to light theme");
    }

    const currentTheme = document.documentElement.getAttribute("data-theme") || localStorage.getItem("skytrack_theme") || "signature";
    updateLabels(currentTheme);

    themeToggleBtn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const current = document.documentElement.getAttribute("data-theme") || "signature";
        const nextTheme = (current === "light") ? "signature" : "light";
        if (window.SkyTrackerAppearance && window.SkyTrackerAppearance.applyTheme) {
            window.SkyTrackerAppearance.applyTheme(nextTheme);
        } else {
            document.documentElement.setAttribute("data-theme", nextTheme);
            try {
                localStorage.setItem("skytrack_theme", nextTheme);
            } catch (err) {}
            updateLabels(nextTheme);
        }
    });

    themeToggleBtn.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            themeToggleBtn.click();
        }
    });
}

initThemeSystem();

/* =========================================================
   PRICE WATCH DASHBOARD METRICS CALCULATOR & ANIMATOR
========================================================= */

function animateMetricValue(element, newText) {
    if (!element) return;
    const current = element.textContent.trim();
    if (current === newText.trim()) return;

    const prefersReducedMotion =
        window.matchMedia &&
        window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    if (prefersReducedMotion) {
        element.textContent = newText;
        return;
    }

    element.classList.add("updating");
    setTimeout(() => {
        element.textContent = newText;
        element.classList.remove("updating");
    }, 150);
}

async function updateDashboardMetrics(data) {
    const activeRoutesEl = $("metricActiveRoutes");
    const lowestPriceEl = $("metricLowestPrice");
    const targetStatusEl = $("metricTargetStatus");
    const radarStatusDot = $("radarStatusDot");
    const radarStatusText = $("radarStatusText");
    const radarSubtitle = $("metricRadarSubtitle");

    const count = Array.isArray(data) ? data.length : 0;

    // 1. Active routes
    if (activeRoutesEl) {
        animateMetricValue(activeRoutesEl, String(count));
    }

    // 2. Lowest tracked price across saved routes
    if (lowestPriceEl) {
        const validPrices = (data || [])
            .map(f => {
                const p = Number(
                    f.last_price !== null && f.last_price !== undefined
                        ? f.last_price
                        : f.lowest_price
                );
                return !isNaN(p) && p > 0 ? p : null;
            })
            .filter(p => p !== null);

        if (validPrices.length > 0) {
            const minPrice = Math.min(...validPrices);
            animateMetricValue(
                lowestPriceEl,
                `₹${minPrice.toLocaleString("en-IN")}`
            );
        } else {
            animateMetricValue(lowestPriceEl, "—");
        }
    }

    // 3. Target goal status
    if (targetStatusEl) {
        if (count > 0) {
            const metCount = (data || []).filter(f => {
                const curr = Number(
                    f.last_price !== null && f.last_price !== undefined
                        ? f.last_price
                        : f.lowest_price
                );
                const target = Number(f.target_price);
                return (
                    !isNaN(curr) &&
                    !isNaN(target) &&
                    curr > 0 &&
                    target > 0 &&
                    curr <= target
                );
            }).length;
            animateMetricValue(targetStatusEl, `${metCount} of ${count}`);
        } else {
            animateMetricValue(targetStatusEl, "—");
        }
    }

    // 4. Radar status (from real background monitor status API)
    try {
        const radarRes = await fetch("/api/radar/status");
        if (radarRes.ok) {
            const radarData = await radarRes.json();
            const status = radarData.status || "Active";
            if (radarStatusText) {
                radarStatusText.textContent = status;
            }
            if (radarStatusDot) {
                radarStatusDot.className = "status-dot";
                if (status === "Active") radarStatusDot.classList.add("status-active");
                else if (status === "Paused") radarStatusDot.classList.add("status-paused");
                else if (status === "Error") radarStatusDot.classList.add("status-error");
                else radarStatusDot.classList.add("status-unknown");
            }
            if (radarSubtitle && radarData.status_detail) {
                radarSubtitle.textContent = radarData.status_detail;
            }
        }
    } catch {
        if (radarStatusText) radarStatusText.textContent = "Active";
        if (radarStatusDot) {
            radarStatusDot.className = "status-dot status-active";
        }
        if (radarSubtitle) {
            radarSubtitle.textContent = "Automated background checks";
        }
    }
}

/* =========================================================
   REALISTIC SPATIAL DEPTH & 3D MOTION CONTROLLER
========================================================= */

function initSpatialCardsMotion() {
    const prefersReducedMotion =
        window.matchMedia &&
        window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (prefersReducedMotion) return;

    const cards = document.querySelectorAll('[data-tilt="true"]');
    cards.forEach(card => {
        let isHovered = false;

        card.addEventListener("mouseenter", () => {
            isHovered = true;
        });

        card.addEventListener("mousemove", e => {
            if (!isHovered) return;
            const rect = card.getBoundingClientRect();
            const x = (e.clientX - rect.left) / rect.width;
            const y = (e.clientY - rect.top) / rect.height;

            // Subtle restrained tilt (maximum ±3.5 degrees)
            const tiltX = -((y - 0.5) * 7).toFixed(2);
            const tiltY = ((x - 0.5) * 7).toFixed(2);

            card.style.transform = `perspective(900px) translateY(-3px) rotateX(${tiltX}deg) rotateY(${tiltY}deg)`;
        });

        card.addEventListener("mouseleave", () => {
            isHovered = false;
            card.style.transform = "perspective(900px) translateY(0) rotateX(0deg) rotateY(0deg)";
        });

        card.addEventListener("blur", () => {
            card.style.transform = "perspective(900px) translateY(0) rotateX(0deg) rotateY(0deg)";
        });
    });
}

initSpatialCardsMotion();


/* =========================================================
   FLIGHTS.HTML CONTROLS & RADAR TRACK MODAL
========================================================= */
function initFlightsPageExtras() {
    // Stops filter pills on flights.html
    const filterPills = document.querySelectorAll("#flightFilterBar .filter-pill");
    filterPills.forEach(pill => {
        pill.addEventListener("click", () => {
            filterPills.forEach(p => p.classList.remove("active"));
            pill.classList.add("active");
            if (typeof applyFlightFilters === "function") {
                applyFlightFilters();
            }
        });
    });

    // Sort select on flights.html
    const sortSel = $("flightSortSelect");
    if (sortSel) {
        sortSel.addEventListener("change", () => {
            if (typeof applyFlightFilters === "function") {
                applyFlightFilters();
            }
        });
    }

    // Track modal close button
    const closeBtn = $("closeTrackModalBtn");
    const trackModal = $("trackPriceModal");
    if (closeBtn && trackModal) {
        closeBtn.addEventListener("click", () => {
            trackModal.style.display = "none";
        });
        trackModal.addEventListener("click", e => {
            if (e.target === trackModal) {
                trackModal.style.display = "none";
            }
        });
    }

    // Track route form submission
    const trackRouteForm = $("trackRouteForm");
    if (trackRouteForm) {
        trackRouteForm.addEventListener("submit", async e => {
            e.preventDefault();
            const errBanner = $("trackModalError");
            const successBanner = $("trackModalSuccess");
            if (errBanner) errBanner.style.display = "none";
            if (successBanner) successBanner.style.display = "none";

            const targetPrice = $("trackTargetPrice")?.value;
            const email = $("trackEmail")?.value;

            const trackDep = (currentTrackingFlight && (currentTrackingFlight.departure_airport || currentTrackingFlight.departure || (currentTrackingFlight.legs && currentTrackingFlight.legs[0]?.departure_airport) || (currentTrackingFlight.legs && currentTrackingFlight.legs[0]?.departure))) || (latestSearch && (latestSearch.departure || (latestSearch.legs && latestSearch.legs[0]?.departure)));
            const trackArr = (currentTrackingFlight && (currentTrackingFlight.arrival_airport || currentTrackingFlight.arrival || (currentTrackingFlight.legs && currentTrackingFlight.legs[currentTrackingFlight.legs.length - 1]?.arrival_airport) || (currentTrackingFlight.legs && currentTrackingFlight.legs[currentTrackingFlight.legs.length - 1]?.arrival))) || (latestSearch && (latestSearch.arrival || (latestSearch.legs && latestSearch.legs[latestSearch.legs.length - 1]?.arrival)));
            const trackDate = (currentTrackingFlight && (currentTrackingFlight.departure_date || currentTrackingFlight.outbound_date || (currentTrackingFlight.legs && currentTrackingFlight.legs[0]?.departure_date) || (currentTrackingFlight.legs && currentTrackingFlight.legs[0]?.outbound_date))) || (latestSearch && (latestSearch.outbound_date || (latestSearch.legs && latestSearch.legs[0]?.outbound_date)));
            const trackReturn = (currentTrackingFlight && (currentTrackingFlight.return_departure_time || currentTrackingFlight.return_date)) || (latestSearch && latestSearch.return_date);
            const trackTripType = (currentTrackingFlight && currentTrackingFlight.trip_type) || (latestSearch && latestSearch.trip_type) || (trackReturn ? "round_trip" : "one_way");

            if (!trackDep || !trackArr || !trackDate) {
                if (errBanner) {
                    errBanner.textContent = "Please conduct a flight trajectory search first.";
                    errBanner.style.display = "block";
                }
                return;
            }

            try {
                const res = await fetch("/api/track", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        departure: trackDep,
                        arrival: trackArr,
                        outbound_date: trackDate,
                        return_date: trackReturn,
                        trip_type: trackTripType,
                        target_price: parseFloat(targetPrice),
                        email: email
                    })
                });
                const resData = await res.json();
                if (!res.ok) {
                    throw new Error(resData.error || "Failed to arm radar.");
                }
                if (successBanner) {
                    successBanner.textContent = "Flight price radar armed successfully!";
                    successBanner.style.display = "block";
                }
                setTimeout(() => {
                    if (trackModal) trackModal.style.display = "none";
                }, 1500);
            } catch (err) {
                if (errBanner) {
                    errBanner.textContent = err.message || "Failed to arm radar.";
                    errBanner.style.display = "block";
                }
            }
        });
    }
}

initFlightsPageExtras();
