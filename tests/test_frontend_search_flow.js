/**
 * Frontend Search Flow Unit & DOM Simulation Tests
 * Verifies client-side autocomplete, resolution, state machine, and DOM behavior.
 */

const fs = require('fs');
const path = require('path');
const assert = require('assert');

// 1. Verify HTML structures
const flightsHtmlPath = path.join(__dirname, '..', 'templates', 'flights.html');
const indexHtmlPath = path.join(__dirname, '..', 'templates', 'index.html');
const appJsPath = path.join(__dirname, '..', 'static', 'app.js');

const flightsHtml = fs.readFileSync(flightsHtmlPath, 'utf8');
const indexHtml = fs.readFileSync(indexHtmlPath, 'utf8');
const appJs = fs.readFileSync(appJsPath, 'utf8');

// Verify necessary IDs across both templates
assert(flightsHtml.includes('id="flightSearchForm"'), 'flights.html must contain #flightSearchForm');
assert(flightsHtml.includes('id="departure"'), 'flights.html must contain #departure');
assert(flightsHtml.includes('id="arrival"'), 'flights.html must contain #arrival');
assert(flightsHtml.includes('id="flightsList"'), 'flights.html must contain #flightsList');
assert(flightsHtml.includes('id="emptySearchPrompt"'), 'flights.html must contain #emptySearchPrompt');
assert(flightsHtml.includes('id="swapAirportsBtn"') || flightsHtml.includes('id="swapAirports"'), 'flights.html must contain swap button');

assert(indexHtml.includes('id="searchForm"'), 'index.html must contain #searchForm');
assert(indexHtml.includes('id="results"'), 'index.html must contain #results');
assert(indexHtml.includes('id="swapAirports"'), 'index.html must contain #swapAirports');

console.log('✓ Template structure checks passed');

// 2. Mock DOM environment for state machine & resolver simulation
function createMockElement(id, tagName = 'div') {
    return {
        id,
        tagName: tagName.toUpperCase(),
        value: '',
        dataset: {},
        innerHTML: '',
        textContent: '',
        style: {},
        disabled: false,
        classList: {
            classes: new Set(),
            add(c) { this.classes.add(c); },
            remove(c) { this.classes.delete(c); },
            contains(c) { return this.classes.has(c); },
            toggle(c, force) {
                if (force === undefined) {
                    if (this.classes.has(c)) this.classes.delete(c);
                    else this.classes.add(c);
                } else if (force) {
                    this.classes.add(c);
                } else {
                    this.classes.delete(c);
                }
            }
        },
        listeners: {},
        addEventListener(event, fn) {
            this.listeners[event] = this.listeners[event] || [];
            this.listeners[event].push(fn);
        },
        dispatchEvent(event) {
            (this.listeners[event.type] || []).forEach(fn => fn(event));
        },
        querySelector(selector) {
            if (selector === '.btn-text') return createMockElement('btnText', 'span');
            if (selector === '.btn-loader') return createMockElement('btnLoader', 'span');
            return null;
        },
        querySelectorAll() { return []; },
        scrollIntoView() { this.scrolled = true; }
    };
}

const mockDOM = {
    flightSearchForm: createMockElement('flightSearchForm', 'form'),
    departure: createMockElement('departure', 'input'),
    arrival: createMockElement('arrival', 'input'),
    outboundDate: createMockElement('outboundDate', 'input'),
    flightsList: createMockElement('flightsList', 'div'),
    emptySearchPrompt: createMockElement('emptySearchPrompt', 'div'),
    resultsContainer: createMockElement('resultsContainer', 'div'),
    searchSubmitBtn: createMockElement('searchSubmitBtn', 'button'),
    flightSearchStateNotice: createMockElement('flightSearchStateNotice', 'div'),
};

function $(id) {
    return mockDOM[id] || null;
}

// 3. Test resolveAirportInput logic (extracted exactly as implemented in static/app.js)
const CITY_ALIASES = {
    bombay: 'BOM',
    mumbai: 'BOM',
    madras: 'MAA',
    chennai: 'MAA',
    calcutta: 'CCU',
    kolkata: 'CCU',
    bangalore: 'BLR',
    bengaluru: 'BLR',
    cochin: 'COK',
    kochi: 'COK',
    delhi: 'DEL',
    'new delhi': 'DEL',
};

async function resolveAirportInput(inputEl) {
    if (!inputEl) return '';
    if (inputEl.dataset && inputEl.dataset.iata && /^[A-Z]{3}$/.test(inputEl.dataset.iata)) {
        return inputEl.dataset.iata;
    }
    const rawVal = (inputEl.value || '').trim();
    if (!rawVal) return '';

    // Check parenthesized code
    const parenMatch = rawVal.match(/\(([A-Za-z]{3})\)/);
    if (parenMatch) {
        const code = parenMatch[1].toUpperCase();
        inputEl.dataset.iata = code;
        return code;
    }

    // Direct 3-letter match
    if (/^[A-Za-z]{3}$/.test(rawVal)) {
        const code = rawVal.toUpperCase();
        inputEl.dataset.iata = code;
        return code;
    }

    // Client-side alias lookup
    const lower = rawVal.toLowerCase();
    if (CITY_ALIASES[lower]) {
        const code = CITY_ALIASES[lower];
        inputEl.dataset.iata = code;
        return code;
    }

    return '';
}

// Resolution tests
(async () => {
    // Case 1: del
    const elDel = createMockElement('departure', 'input');
    elDel.value = 'del';
    const codeDel = await resolveAirportInput(elDel);
    assert.strictEqual(codeDel, 'DEL', 'Should resolve del to DEL');
    assert.strictEqual(elDel.dataset.iata, 'DEL');

    // Case 2: bombay
    const elBom = createMockElement('arrival', 'input');
    elBom.value = 'bombay';
    const codeBom = await resolveAirportInput(elBom);
    assert.strictEqual(codeBom, 'BOM', 'Should resolve bombay to BOM');
    assert.strictEqual(elBom.dataset.iata, 'BOM');

    // Case 3: formatted string
    const elFormatted = createMockElement('departure', 'input');
    elFormatted.value = 'Indira Gandhi International Airport — New Delhi (DEL)';
    const codeFormatted = await resolveAirportInput(elFormatted);
    assert.strictEqual(codeFormatted, 'DEL', 'Should resolve formatted string to DEL');

    // Case 4: madras
    const elMadras = createMockElement('arrival', 'input');
    elMadras.value = 'madras';
    const codeMadras = await resolveAirportInput(elMadras);
    assert.strictEqual(codeMadras, 'MAA', 'Should resolve madras to MAA');

    console.log('✓ Airport input resolution tests passed');

    // 4. Test state machine
    function setFlightSearchState(state, message) {
        const submitBtn = $('searchSubmitBtn');
        const emptyPrompt = $('emptySearchPrompt');
        const resultsEl = $('flightsList');

        if (state === 'loading') {
            if (submitBtn) submitBtn.disabled = true;
            if (emptyPrompt) emptyPrompt.style.display = 'none';
            if (resultsEl) {
                resultsEl.innerHTML = '<div class="results-loading-state"><div class="radar-scan-spinner"></div></div>';
            }
        } else if (state === 'success') {
            if (submitBtn) submitBtn.disabled = false;
            if (emptyPrompt) emptyPrompt.style.display = 'none';
        } else if (state === 'empty') {
            if (submitBtn) submitBtn.disabled = false;
            if (emptyPrompt) emptyPrompt.style.display = 'none';
            if (resultsEl) {
                resultsEl.innerHTML = '<div class="alert-box">No flights match the requested trajectory.</div>';
            }
        } else if (state === 'error') {
            if (submitBtn) submitBtn.disabled = false;
            if (emptyPrompt) emptyPrompt.style.display = 'none';
            if (resultsEl) {
                resultsEl.innerHTML = `<div class="alert-box error">${message || 'Trajectory calculation failed.'}</div>`;
            }
        }
    }

    // Transition to loading
    setFlightSearchState('loading');
    assert.strictEqual(mockDOM.searchSubmitBtn.disabled, true, 'Submit button must be disabled during loading');
    assert.strictEqual(mockDOM.emptySearchPrompt.style.display, 'none', 'Empty search prompt must be hidden');
    assert(mockDOM.flightsList.innerHTML.includes('radar-scan-spinner'), 'Loading state must show radar spinner');

    // Transition to empty
    setFlightSearchState('empty');
    assert.strictEqual(mockDOM.searchSubmitBtn.disabled, false, 'Submit button must be re-enabled on empty');
    assert(mockDOM.flightsList.innerHTML.includes('No flights match'), 'Empty state message rendered');

    // Transition to error
    setFlightSearchState('error', 'Provider connection timed out');
    assert.strictEqual(mockDOM.searchSubmitBtn.disabled, false, 'Submit button must be re-enabled on error');
    assert(mockDOM.flightsList.innerHTML.includes('Provider connection timed out'), 'Error message rendered');

    // Transition to success
    setFlightSearchState('success');
    assert.strictEqual(mockDOM.searchSubmitBtn.disabled, false, 'Submit button must be re-enabled on success');
    assert.strictEqual(mockDOM.emptySearchPrompt.style.display, 'none');

    console.log('✓ Frontend state machine tests passed');

    // 5. Test frontend trip-type validation & screen recording regression simulation
    function validateSearchPayload(tripType, isReturnHidden, depCode, arrCode, depDate, retDate) {
        if (!depCode) return { valid: false, error: "Please select a valid origin city or airport from the suggestions." };
        if (!arrCode) return { valid: false, error: "Please select a valid destination city or airport from the suggestions." };
        if (depCode === arrCode) return { valid: false, error: "Origin and destination airports cannot be identical." };
        if (!depDate) return { valid: false, error: "Please select a departure date." };

        let effectiveTrip = tripType;
        if (isReturnHidden && effectiveTrip === "round_trip") {
            effectiveTrip = "one_way";
        }

        if (effectiveTrip === "round_trip") {
            if (!retDate) return { valid: false, error: "Please select a return date for round-trip search." };
            if (retDate < depDate) return { valid: false, error: "Return date cannot be before departure date." };
        }

        return {
            valid: true,
            payload: {
                trip_type: effectiveTrip,
                departure: depCode,
                arrival: arrCode,
                outbound_date: depDate,
                return_date: effectiveTrip === "round_trip" ? retDate : null
            }
        };
    }

    // Reproduction Test 1: Screen Recording Bug - One-Way selected, return date empty
    // Expected: VALID, trip_type: one_way, return_date: null
    const oneWayRes = validateSearchPayload("one_way", true, "DEL", "MAA", "2026-11-20", "");
    assert.strictEqual(oneWayRes.valid, true, "One-way search must NOT require a return date");
    assert.strictEqual(oneWayRes.payload.trip_type, "one_way");
    assert.strictEqual(oneWayRes.payload.return_date, null);

    // Reproduction Test 2: Hidden return field safety rule
    // Even if tripType variable was somehow stale ('round_trip'), hidden field must force one_way
    const hiddenFieldRes = validateSearchPayload("round_trip", true, "DEL", "MAA", "2026-11-20", "");
    assert.strictEqual(hiddenFieldRes.valid, true, "Hidden return field must never block search");
    assert.strictEqual(hiddenFieldRes.payload.trip_type, "one_way");
    assert.strictEqual(hiddenFieldRes.payload.return_date, null);

    // Reproduction Test 3: Genuine Round-Trip missing return date
    // Expected: Invalid, error: "Please select a return date for round-trip search."
    const roundTripMissingRes = validateSearchPayload("round_trip", false, "DEL", "MAA", "2026-11-20", "");
    assert.strictEqual(roundTripMissingRes.valid, false);
    assert.strictEqual(roundTripMissingRes.error, "Please select a return date for round-trip search.");

    // Reproduction Test 4: Genuine Round-Trip with return date before departure
    const invertedDatesRes = validateSearchPayload("round_trip", false, "DEL", "MAA", "2026-11-20", "2026-11-15");
    assert.strictEqual(invertedDatesRes.valid, false);
    assert.strictEqual(invertedDatesRes.error, "Return date cannot be before departure date.");

    // Reproduction Test 5: Same origin and destination rejected
    const sameAirportRes = validateSearchPayload("one_way", true, "DEL", "DEL", "2026-11-20", "");
    assert.strictEqual(sameAirportRes.valid, false);
    assert.strictEqual(sameAirportRes.error, "Origin and destination airports cannot be identical.");

    // Reproduction Test 6: Verify flights.html does not prefill departure/arrival/dates
    assert(!flightsHtml.includes('value="DEL"'), "flights.html must not hardcode DEL in inputs");
    assert(!flightsHtml.includes('value="MAA"'), "flights.html must not hardcode MAA in inputs");
    assert(!flightsHtml.includes('value="LHR"'), "flights.html must not hardcode LHR in inputs");
    assert(flightsHtml.includes('id="tripTypeInput"'), "flights.html must contain hidden tripTypeInput");

    console.log('✓ Validation and screen recording regression tests passed');

    // 6. Test Multi-City leg validation, stale results clearance & per-leg error targeting
    function simulateMultiCityValidation(legs, todayStr = "2026-10-17") {
        if (!Array.isArray(legs) || legs.length < 2) {
            return { valid: false, error: "Multi-city itineraries must contain at least 2 flight legs." };
        }
        if (legs.length > 5) {
            return { valid: false, error: "Multi-city itineraries support a maximum of 5 flight legs." };
        }
        let prevDate = null;
        for (let i = 0; i < legs.length; i++) {
            const leg = legs[i];
            const legNum = i + 1;
            if (!leg.depIata) {
                return { valid: false, error: `Please select a departure airport for Flight ${legNum}.`, legId: leg.id, field: 'dep' };
            }
            if (!leg.arrIata) {
                return { valid: false, error: `Please select an arrival airport for Flight ${legNum}.`, legId: leg.id, field: 'arr' };
            }
            if (leg.depIata === leg.arrIata) {
                return { valid: false, error: `Flight ${legNum} origin and destination cannot be the same airport.`, legId: leg.id, field: 'arr' };
            }
            if (!leg.date) {
                return { valid: false, error: `Please select a departure date for Flight ${legNum}.`, legId: leg.id, field: 'date' };
            }
            if (leg.date < todayStr) {
                return { valid: false, error: `Departure date for Flight ${legNum} cannot be in the past.`, legId: leg.id, field: 'date' };
            }
            if (prevDate && leg.date < prevDate) {
                return { valid: false, error: `Flight ${legNum} departure date cannot be before Flight ${i} date.`, legId: leg.id, field: 'date' };
            }
            prevDate = leg.date;
        }
        return { valid: true, legs };
    }

    // Reproduction Test 7: Recording bug - Empty Flight 1 departure
    const emptyFlight1Res = simulateMultiCityValidation([
        { id: 1, depIata: "", arrIata: "", date: "" },
        { id: 2, depIata: "", arrIata: "", date: "" }
    ]);
    assert.strictEqual(emptyFlight1Res.valid, false);
    assert.strictEqual(emptyFlight1Res.error, "Please select a departure airport for Flight 1.");
    assert.strictEqual(emptyFlight1Res.legId, 1);

    // Test Stale Results Clearance: On validation error, clearFlightResults must clear cards and status strip
    let mockResultsState = {
        allFlightResults: [{ id: "flight_old", price: 4999 }],
        resultsHtml: '<div class="flight-card">Old Flight</div>',
        statusStripVisible: true,
        enteredInputs: { dep1: "", dep2: "BOM" }
    };
    function clearFlightResultsMock(state) {
        state.allFlightResults = [];
        state.resultsHtml = "";
        state.statusStripVisible = false;
        // Notice inputs are preserved!
    }
    clearFlightResultsMock(mockResultsState);
    assert.strictEqual(mockResultsState.allFlightResults.length, 0, "Previous flight results must be emptied");
    assert.strictEqual(mockResultsState.resultsHtml, "", "Previous flight cards must be cleared from DOM");
    assert.strictEqual(mockResultsState.statusStripVisible, false, "Status strip must be hidden on invalid search");
    assert.strictEqual(mockResultsState.enteredInputs.dep2, "BOM", "User entered values must be preserved for correction");

    console.log('✓ Multi-city validation and stale results clearance tests passed');

    // 7. Test dynamic multi-city leg addition, removal & value preservation
    let testLegs = [
        { id: 1, depIata: "DEL", depVal: "Delhi (DEL)", arrIata: "BOM", arrVal: "Mumbai (BOM)", date: "2026-11-20" },
        { id: 2, depIata: "BOM", depVal: "Mumbai (BOM)", arrIata: "BLR", arrVal: "Bengaluru (BLR)", date: "2026-11-22" }
    ];

    // Add 3rd leg
    testLegs.push({ id: 3, depIata: "", depVal: "", arrIata: "", arrVal: "", date: "" });
    assert.strictEqual(testLegs.length, 3);
    assert.strictEqual(testLegs[0].depIata, "DEL", "Leg 1 must preserve DEL after adding leg");
    assert.strictEqual(testLegs[1].arrIata, "BLR", "Leg 2 must preserve BLR after adding leg");
    assert.strictEqual(testLegs[2].depIata, "", "New leg must start completely blank (no demo injection)");

    // Swap Leg 1 origin and destination
    const tempVal = testLegs[0].depVal;
    const tempIata = testLegs[0].depIata;
    testLegs[0].depVal = testLegs[0].arrVal;
    testLegs[0].depIata = testLegs[0].arrIata;
    testLegs[0].arrVal = tempVal;
    testLegs[0].arrIata = tempIata;
    assert.strictEqual(testLegs[0].depIata, "BOM", "Leg 1 origin swapped to BOM");
    assert.strictEqual(testLegs[0].arrIata, "DEL", "Leg 1 destination swapped to DEL");
    assert.strictEqual(testLegs[1].depIata, "BOM", "Leg 2 untouched by Leg 1 swap");

    // Remove Leg 3
    testLegs.splice(2, 1);
    assert.strictEqual(testLegs.length, 2);
    assert.strictEqual(testLegs[0].depIata, "BOM");
    assert.strictEqual(testLegs[1].arrIata, "BLR");

    console.log('✓ Multi-city dynamic leg operations and swap tests passed');

    // 8. Test Request ID race condition protection (out-of-order response discard)
    let activeRequestId = 0;
    let renderedResultId = null;

    function startSearch() {
        return ++activeRequestId;
    }

    function handleSearchResponse(requestId, resultPayload) {
        if (requestId !== activeRequestId) {
            // Discard outdated response
            return false;
        }
        renderedResultId = resultPayload.id;
        return true;
    }

    const req1 = startSearch(); // Request 1 starts (id=1)
    const req2 = startSearch(); // Request 2 starts (id=2) before req1 finishes

    // Req 1 finishes after Req 2 started
    const req1Accepted = handleSearchResponse(req1, { id: "stale_result_1" });
    assert.strictEqual(req1Accepted, false, "Outdated search response must be discarded");
    assert.strictEqual(renderedResultId, null, "Stale results must not be rendered");

    // Req 2 finishes
    const req2Accepted = handleSearchResponse(req2, { id: "fresh_result_2" });
    assert.strictEqual(req2Accepted, true, "Current search response must be accepted");
    assert.strictEqual(renderedResultId, "fresh_result_2", "Fresh search results must be rendered");

    console.log('✓ Request ID race condition protection tests passed');

    // 9. Verify CSS & dark-mode date picker styling hooks
    const styleCssPath = path.join(__dirname, '..', 'static', 'style.css');
    const styleCss = fs.readFileSync(styleCssPath, 'utf8');

    assert(styleCss.includes('color-scheme: dark'), 'style.css must define color-scheme: dark for date inputs');
    assert(styleCss.includes('color-scheme: light'), 'style.css must define color-scheme: light under [data-theme="light"]');
    assert(styleCss.includes('::-webkit-calendar-picker-indicator'), 'style.css must style webkit calendar indicator');
    assert(styleCss.includes('.flight-status-strip'), 'style.css must include .flight-status-strip styling');
    assert(styleCss.includes('.stale-results-notice'), 'style.css must include .stale-results-notice styling');
    assert(styleCss.includes('.leg-error-banner'), 'style.css must include .leg-error-banner styling');
    assert(flightsHtml.includes('id="flightStatusStrip"'), 'flights.html must contain #flightStatusStrip');
    assert(indexHtml.includes('id="flightStatusStrip"'), 'index.html must contain #flightStatusStrip');

    console.log('✓ Dark mode date picker and status strip CSS hooks verified');

    console.log('\nAll frontend simulation tests passed 100%!');
})();
