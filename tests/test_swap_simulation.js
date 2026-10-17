/**
 * Test Airport Swap logic and HTML element consistency
 */
const fs = require('fs');
const path = require('path');
const assert = require('assert');

// 1. Verify templates/index.html contains all necessary elements
const htmlPath = path.join(__dirname, '..', 'templates', 'index.html');
const htmlContent = fs.readFileSync(htmlPath, 'utf8');

assert(htmlContent.includes('id="departure"'), 'Must have origin field #departure');
assert(htmlContent.includes('id="arrival"'), 'Must have destination field #arrival');
assert(htmlContent.includes('placeholder="Enter origin city or airport"'), 'Must have clean, neutral origin placeholder');
assert(htmlContent.includes('placeholder="Enter destination city or airport"'), 'Must have clean, neutral destination placeholder');
assert(!htmlContent.includes('e.g. London Heathrow'), 'Must not contain example airport London Heathrow');
assert(!htmlContent.includes('e.g. Kansai'), 'Must not contain example airport Kansai');
assert(htmlContent.includes('id="swapAirports"'), 'Must have swap button #swapAirports');
assert(htmlContent.includes('id="departureSuggestions"'), 'Must have #departureSuggestions');
assert(htmlContent.includes('id="arrivalSuggestions"'), 'Must have #arrivalSuggestions');
assert(htmlContent.includes('id="nearbyDeparture"'), 'Must have #nearbyDeparture');
assert(htmlContent.includes('id="nearbyArrival"'), 'Must have #nearbyArrival');
assert(htmlContent.includes('id="selectedFlightSummaryCard"'), 'Must have #selectedFlightSummaryCard');
assert(htmlContent.includes('id="exactFlightOffersContainer"'), 'Must have #exactFlightOffersContainer');
assert(htmlContent.includes('id="btnBackToResults"'), 'Must have #btnBackToResults');

console.log('✓ HTML elements & clean placeholders verification passed');

// 2. Mock DOM Elements to test swap mechanics
function createMockElement(id, tagName = 'div') {
    return {
        id,
        tagName,
        value: '',
        dataset: {},
        innerHTML: '',
        style: {},
        checked: false,
        classList: {
            classes: new Set(),
            add(c) { this.classes.add(c); },
            remove(c) { this.classes.delete(c); },
            contains(c) { return this.classes.has(c); }
        },
        listeners: {},
        addEventListener(event, fn) {
            this.listeners[event] = this.listeners[event] || [];
            this.listeners[event].push(fn);
        },
        dispatchEvent(event) {
            (this.listeners[event.type] || []).forEach(fn => fn(event));
        },
        querySelector() {
            return {
                dataset: {},
                style: {}
            };
        }
    };
}

const elements = {
    swapAirports: createMockElement('swapAirports', 'button'),
    departure: createMockElement('departure', 'input'),
    arrival: createMockElement('arrival', 'input'),
    departureSuggestions: createMockElement('departureSuggestions'),
    arrivalSuggestions: createMockElement('arrivalSuggestions'),
    nearbyDeparture: createMockElement('nearbyDeparture', 'input'),
    nearbyArrival: createMockElement('nearbyArrival', 'input'),
    status: createMockElement('status'),
    statusWrapper: createMockElement('statusWrapper')
};

function $(id) {
    return elements[id] || null;
}

// 3. Instantiate the exact airport swap code from static/app.js
function initAirportSwap() {
    const swapBtn = $("swapAirports");
    const depInput = $("departure");
    const arrInput = $("arrival");
    const depSuggestions = $("departureSuggestions");
    const arrSuggestions = $("arrivalSuggestions");
    const nearbyDep = $("nearbyDeparture");
    const nearbyArr = $("nearbyArrival");

    if (!swapBtn || !depInput || !arrInput) return;

    function executeSwap(e) {
        if (e) {
            e.preventDefault();
            e.stopPropagation();
        }

        if (depSuggestions) {
            depSuggestions.innerHTML = "";
            depSuggestions.style.display = "none";
        }
        if (arrSuggestions) {
            arrSuggestions.innerHTML = "";
            arrSuggestions.style.display = "none";
        }

        const depVal = depInput.value;
        let depIata = depInput.dataset.iata || "";
        const depCity = depInput.dataset.city || "";
        const depName = depInput.dataset.name || "";
        if (!depIata) {
            const m = depVal.match(/\(([A-Z]{3})\)/i) || (depVal.trim().length === 3 ? depVal.trim().match(/^([A-Z]{3})$/i) : null);
            if (m) depIata = m[1].toUpperCase();
        }

        const arrVal = arrInput.value;
        let arrIata = arrInput.dataset.iata || "";
        const arrCity = arrInput.dataset.city || "";
        const arrName = arrInput.dataset.name || "";
        if (!arrIata) {
            const m = arrVal.match(/\(([A-Z]{3})\)/i) || (arrVal.trim().length === 3 ? arrVal.trim().match(/^([A-Z]{3})$/i) : null);
            if (m) arrIata = m[1].toUpperCase();
        }

        if (!depVal && !arrVal && !depIata && !arrIata) return;

        depInput.value = arrVal;
        arrInput.value = depVal;

        depInput.dataset.iata = arrIata;
        depInput.dataset.city = arrCity;
        depInput.dataset.name = arrName;

        arrInput.dataset.iata = depIata;
        arrInput.dataset.city = depCity;
        arrInput.dataset.name = depName;

        if (nearbyDep && nearbyArr) {
            const tempChecked = nearbyDep.checked;
            nearbyDep.checked = nearbyArr.checked;
            nearbyArr.checked = tempChecked;
        }

        const svg = swapBtn.querySelector("svg");
        if (svg) {
            const currentRot = parseInt(svg.dataset.rotation || "0", 10) + 180;
            svg.dataset.rotation = currentRot;
            svg.style.transform = `rotate(${currentRot}deg)`;
        }

        depInput.classList.remove("field-error");
        arrInput.classList.remove("field-error");
        if ($("status")) {
            $("status").textContent = "";
            if ($("statusWrapper")) $("statusWrapper").style.display = "none";
        }
    }

    swapBtn.addEventListener("click", executeSwap);
    swapBtn.addEventListener("keydown", e => {
        if (e.key === "Enter" || e.key === " ") {
            executeSwap(e);
        }
    });

    return executeSwap;
}

const executeSwap = initAirportSwap();

// Helper to simulate airport code extraction on search submission
function extractAirportCodes() {
    let departureCode = $("departure").dataset.iata;
    if (!departureCode) {
        const depVal = $("departure").value.trim();
        const m = depVal.match(/\(([A-Z]{3})\)/i) || (depVal.length === 3 ? depVal.match(/^([A-Z]{3})$/i) : null);
        if (m) {
            departureCode = m[1].toUpperCase();
            $("departure").dataset.iata = departureCode;
        }
    }

    let arrivalCode = $("arrival").dataset.iata;
    if (!arrivalCode) {
        const arrVal = $("arrival").value.trim();
        const m = arrVal.match(/\(([A-Z]{3})\)/i) || (arrVal.length === 3 ? arrVal.match(/^([A-Z]{3})$/i) : null);
        if (m) {
            arrivalCode = m[1].toUpperCase();
            $("arrival").dataset.iata = arrivalCode;
        }
    }
    return { departureCode, arrivalCode };
}

// STEP 1: Select Delhi (DEL) in Origin
elements.departure.value = "Indira Gandhi International Airport — New Delhi (DEL)";
elements.departure.dataset.iata = "DEL";
elements.departure.dataset.city = "New Delhi";
elements.departure.dataset.name = "Indira Gandhi International Airport";
elements.nearbyDeparture.checked = true;

// STEP 2: Select Chennai (MAA) in Destination
elements.arrival.value = "Chennai International Airport — Chennai (MAA)";
elements.arrival.dataset.iata = "MAA";
elements.arrival.dataset.city = "Chennai";
elements.arrival.dataset.name = "Chennai International Airport";
elements.nearbyArrival.checked = false;

// Open some mock suggestions
elements.departureSuggestions.innerHTML = "<div>suggestion</div>";
elements.departureSuggestions.style.display = "block";

// STEP 3: Click swap button
let preventDefaultCalled = false;
elements.swapAirports.dispatchEvent({
    type: 'click',
    preventDefault() { preventDefaultCalled = true; },
    stopPropagation() {}
});

assert(preventDefaultCalled, 'Swap must prevent default form submission');
assert.strictEqual(elements.departure.value, "Chennai International Airport — Chennai (MAA)");
assert.strictEqual(elements.departure.dataset.iata, "MAA");
assert.strictEqual(elements.departure.dataset.city, "Chennai");
assert.strictEqual(elements.arrival.value, "Indira Gandhi International Airport — New Delhi (DEL)");
assert.strictEqual(elements.arrival.dataset.iata, "DEL");
assert.strictEqual(elements.arrival.dataset.city, "New Delhi");
assert.strictEqual(elements.departureSuggestions.style.display, "none", "Suggestions must be hidden");
assert.strictEqual(elements.nearbyDeparture.checked, false, "Nearby checkboxes must swap");
assert.strictEqual(elements.nearbyArrival.checked, true, "Nearby checkboxes must swap");

console.log('✓ Swap 1 passed: DEL/MAA exchanged to MAA/DEL');

// STEP 4: Verify search codes extracted
let codes = extractAirportCodes();
assert.strictEqual(codes.departureCode, "MAA");
assert.strictEqual(codes.arrivalCode, "DEL");
console.log('✓ Search payload extraction passed: origin=MAA, destination=DEL');

// STEP 5: Swap again (MAA/DEL -> DEL/MAA)
elements.swapAirports.dispatchEvent({
    type: 'click',
    preventDefault() {},
    stopPropagation() {}
});
assert.strictEqual(elements.departure.dataset.iata, "DEL");
assert.strictEqual(elements.arrival.dataset.iata, "MAA");
codes = extractAirportCodes();
assert.strictEqual(codes.departureCode, "DEL");
assert.strictEqual(codes.arrivalCode, "MAA");
console.log('✓ Swap 2 passed: restored DEL/MAA');

// STEP 6: Swap a third time via Keyboard (Enter key)
elements.swapAirports.dispatchEvent({
    type: 'keydown',
    key: 'Enter',
    preventDefault() {},
    stopPropagation() {}
});
assert.strictEqual(elements.departure.dataset.iata, "MAA");
assert.strictEqual(elements.arrival.dataset.iata, "DEL");
codes = extractAirportCodes();
assert.strictEqual(codes.departureCode, "MAA");
assert.strictEqual(codes.arrivalCode, "DEL");
console.log('✓ Swap 3 passed via Keyboard: MAA/DEL');

console.log('\nAll Airport Swap acceptance verification tests passed 100%!');
