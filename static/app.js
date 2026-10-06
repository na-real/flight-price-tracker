/* =========================================================



   SKYTRACK - FLIGHT PRICE TRACKER



========================================================= */



const $ = id => document.getElementById(id);





let latestSearch = null;



let allFlightResults = [];



let airportCache = {};



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







function setupAirportAutocomplete(



    inputId,



    suggestionsId



) {







    const input = $(inputId);



    const suggestions = $(suggestionsId);







    if (!input || !suggestions) {



        return;



    }







    let timer = null;







    input.addEventListener("input", () => {







        const query = input.value.trim();







        input.dataset.iata = "";







        clearTimeout(timer);

        if (query.length < 2) {
        suggestions.innerHTML = "";
        suggestions.style.display = "none";
        return;
    }

        timer = setTimeout(async () => {







            try {







                const response = await fetch(



                    `/api/airports?q=${encodeURIComponent(query)}`



                );







                if (!response.ok) {



                    throw new Error("Airport search failed");



                }







                const airports = await response.json();







                if (!airports.length) {







                    suggestions.innerHTML = `



                        <div class="airport-no-result">



                            No airports found



                        </div>



                    `;







                    suggestions.style.display = "block";







                    return;



                }

suggestions.innerHTML = airports.map(airport => {

    const airportName =
        airport.name ||
        airport.airport_name ||
        airport.municipality ||
        "Airport";

    const city =
        airport.city ||
        airport.municipality ||
        airport.city_name ||
        "";

    const country =
        airport.country ||
        airport.iso_country ||
        "";

    const iata =
        airport.iata ||
        airport.iata_code ||
        "";

    return `
        <div
            class="airport-option"
            data-iata="${escapeHtml(iata)}"
            data-city="${escapeHtml(city)}"
        >

            <div class="airport-option-main">

                <span class="airport-icon">
                    ✈️
                </span>

                <div>

                    <strong>
                        ${escapeHtml(airportName)}
                    </strong>

                    <small>
                        ${escapeHtml(city)}
                        ${
                            country
                                ? ` • ${escapeHtml(country)}`
                                : ""
                        }
                    </small>

                </div>

            </div>

            <strong class="airport-code">
                ${escapeHtml(iata)}
            </strong>

        </div>
    `;

}).join("");







                suggestions.style.display = "block";











                suggestions



                    .querySelectorAll(".airport-option")



                    .forEach(option => {

option.addEventListener(

    "click",

    () => {



        const iata =

            option.dataset.iata || "";



        const city =

            option.dataset.city || "";



        if (!iata) {

            return;

        }



const name =
            option.querySelector(".airport-details strong")?.textContent?.trim() ||
            option.querySelector(".airport-option-main strong")?.textContent?.trim() ||
            "";

        input.value =
            name && city
                ? `${name} — ${city} (${iata})`
                : name
                    ? `${name} (${iata})`
                    : city
                        ? `${city} (${iata})`
                        : iata;



        input.dataset.iata =

            iata;



        suggestions.innerHTML = "";



        suggestions.style.display =

            "none";

    }

);





                    });







            } catch (error) {







                console.error(



                    "Airport autocomplete error:",



                    error



                );







                suggestions.innerHTML = `



                    <div class="airport-no-result">



                        Could not load airports



                    </div>



                `;







                suggestions.style.display = "block";



            }







        }, 250);







    });











    document.addEventListener("click", event => {







        if (



            !input.contains(event.target) &&



            !suggestions.contains(event.target)



        ) {







            suggestions.innerHTML = "";



            suggestions.style.display = "none";



        }







    });



}











/* =========================================================



   ENABLE AUTOCOMPLETE



========================================================= */







setupAirportAutocomplete(



    "departure",



    "departureSuggestions"



);







setupAirportAutocomplete(



    "arrival",



    "arrivalSuggestions"



);











/* =========================================================



   SEARCH FLIGHTS



========================================================= */







if ($("searchForm")) {







    $("searchForm").addEventListener(



        "submit",



        async event => {







            event.preventDefault();











            /* -----------------------------------------



               AIRPORT VALIDATION



            ----------------------------------------- */







            const departureCode =



                $("departure").dataset.iata;







            const arrivalCode =



                $("arrival").dataset.iata;











            if (!departureCode) {







                $("status").textContent =



                    "Please select a departure airport from the suggestions.";







                $("departure").focus();







                return;



            }











            if (!arrivalCode) {







                $("status").textContent =



                    "Please select an arrival airport from the suggestions.";







                $("arrival").focus();







                return;



            }











            /* -----------------------------------------



               DATE VALIDATION



            ----------------------------------------- */







            const departureDate =



                $("outbound_date").value;







            const returnDate =



                $("return_date").value;











            if (!departureDate) {







                $("status").textContent =



                    "Please select a departure date.";







                $("outbound_date").focus();







                return;



            }











            const today = new Date();







            today.setHours(



                0,



                0,



                0,



                0



            );











            const selectedDeparture =



                new Date(



                    departureDate + "T00:00:00"



                );











            if (



                selectedDeparture < today



            ) {







                $("status").textContent =



                    "Departure date cannot be in the past.";







                $("outbound_date").focus();







                return;



            }











            if (returnDate) {







                const selectedReturn =



                    new Date(



                        returnDate + "T00:00:00"



                    );











                if (



                    selectedReturn <



                    selectedDeparture



                ) {







                    $("status").textContent =



                        "Return date cannot be before departure.";







                    $("return_date").focus();







                    return;



                }







            }











            /* -----------------------------------------



               NEARBY AIRPORTS



            ----------------------------------------- */







            const nearbyDeparture =



                $("nearbyDeparture")



                    ? $("nearbyDeparture").checked



                    : false;











            const nearbyArrival =



                $("nearbyArrival")



                    ? $("nearbyArrival").checked



                    : false;











            /* -----------------------------------------



               SEARCH START



            ----------------------------------------- */







            $("status").textContent =



                "Searching for flights...";







            $("results").innerHTML = "";











            const payload = {







                departure:



                    departureCode,







                arrival:



                    arrivalCode,







                outbound_date:



                    departureDate,







                return_date:



                    returnDate || null,







                nearby_departure:



                    nearbyDeparture,







                nearby_arrival:



                    nearbyArrival



            };











            latestSearch = payload;











            try {







                const response =



                    await fetch(



                        "/api/search",



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



                        "Flight search failed."



                    );



                }











                if (



                    !data.results ||



                    !data.results.length



                ) {







                    $("status").textContent =



                        "No flights found.";







                    $("results").innerHTML = `



                        <p class="status">



                            No flights were found for this route.



                        </p>



                    `;







                    if ($("flightFilters")) {



                        $("flightFilters").style.display =



                            "none";



                    }







                    return;



                }











                /* -----------------------------------------



                   SAVE RESULTS



                ----------------------------------------- */







                allFlightResults =



                    [...data.results];











                /* -----------------------------------------



                   SHOW FILTER BAR



                ----------------------------------------- */







                if ($("flightFilters")) {







                    $("flightFilters").style.display =



                        "flex";







                }











                /* -----------------------------------------



                   SEARCH SUMMARY



                ----------------------------------------- */







                let summary =



                    `${allFlightResults.length} flights found`;











                if (data.search_airports) {







                    const departureAirports =



                        data.search_airports.departure



                            ?.join(", ") ||



                        departureCode;











                    const arrivalAirports =



                        data.search_airports.arrival



                            ?.join(", ") ||



                        arrivalCode;











                    if (



                        nearbyDeparture ||



                        nearbyArrival



                    ) {







                        summary +=



                            ` • Searching ${departureAirports} → ${arrivalAirports}`;







                    }







                }











                $("status").textContent =



                    summary;











                if ($("resultSummary")) {







                    $("resultSummary").textContent =



                        `${allFlightResults.length} flights found`;







                }











                /* -----------------------------------------



                   CREATE AIRLINE FILTERS



                ----------------------------------------- */







                createAirlineFilters();











                /* -----------------------------------------



                   APPLY FILTERS



                ----------------------------------------- */







                applyFlightFilters();







            } catch (error) {







                console.error(



                    "Flight search error:",



                    error



                );







                $("status").textContent =



                    error.message ||



                    "Could not search flights.";



            }







        }



    );







}











/* =========================================================



   CREATE AIRLINE FILTERS



========================================================= */







function createAirlineFilters() {







    const container =



        $("airlineFilters");







    if (!container) {



        return;



    }











    const airlineCounts = {};











    allFlightResults.forEach(



        flight => {







            const airline =



                String(



                    flight.airline ||



                    "Unknown Airline"



                ).trim();











            if (!airline) {



                return;



            }











            airlineCounts[airline] =



                (airlineCounts[airline] || 0) + 1;







        }



    );











    const airlines =



        Object.entries(



            airlineCounts



        ).sort(



            (a, b) =>



                b[1] - a[1]



        );











    container.innerHTML =



        airlines.map(



            ([airline, count], index) => {







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







                        <span>



                            ${escapeHtml(airline)}



                        </span>







                        <span class="airline-count">



                            (${count})



                        </span>







                    </label>



                `;







            }



        ).join("");







}











/* =========================================================



   APPLY FILTERS



========================================================= */







function applyFlightFilters() {







    if (!allFlightResults.length) {



        return;



    }











    let filteredFlights =



        [...allFlightResults];











    /* -----------------------------------------



       STOPS



    ----------------------------------------- */







    const nonstop =



        $("filterNonstop")?.checked || false;







    const oneStop =



        $("filterOneStop")?.checked || false;







    const twoPlus =



        $("filterTwoPlus")?.checked || false;











    const stopFilterActive =



        nonstop ||



        oneStop ||



        twoPlus;











    if (stopFilterActive) {







        filteredFlights =



            filteredFlights.filter(



                flight => {







                    const stops =



                        Number(



                            flight.stops



                        ) || 0;











                    if (



                        stops === 0 &&



                        nonstop



                    ) {



                        return true;



                    }











                    if (



                        stops === 1 &&



                        oneStop



                    ) {



                        return true;



                    }











                    if (



                        stops >= 2 &&



                        twoPlus



                    ) {



                        return true;



                    }











                    return false;



                }



            );







    }











    /* -----------------------------------------



       MAX PRICE



    ----------------------------------------- */







    const maxPrice =



        Number(



            $("maxPrice")?.value



        );











    if (



        maxPrice > 0



    ) {







        filteredFlights =



            filteredFlights.filter(



                flight =>



                    Number(



                        flight.price



                    ) <= maxPrice



            );







    }











    /* -----------------------------------------



       AIRLINE



    ----------------------------------------- */







    const selectedAirlines =



        Array.from(



            document.querySelectorAll(



                ".airline-filter-checkbox:checked"



            )



        ).map(



            checkbox =>



                checkbox.value



        );











    if (



        selectedAirlines.length > 0



    ) {







        filteredFlights =



            filteredFlights.filter(



                flight => {







                    const airline =



                        String(



                            flight.airline ||



                            "Unknown Airline"



                        ).trim();











                    return selectedAirlines.includes(



                        airline



                    );







                }



            );







    }











    /* -----------------------------------------



       SORT



    ----------------------------------------- */







    const sortType =



        $("sortFlights")?.value ||



        "cheapest";











    if (



        sortType === "fastest"



    ) {







        filteredFlights.sort(



            (a, b) =>



                Number(a.duration || 0) -



                Number(b.duration || 0)



        );







    } else {







        filteredFlights.sort(



            (a, b) =>



                Number(a.price || 0) -



                Number(b.price || 0)



        );







    }











    /* -----------------------------------------



       RESULT COUNT



    ----------------------------------------- */







    if ($("resultSummary")) {







        $("resultSummary").textContent =



            `${filteredFlights.length} of ${



                allFlightResults.length



            } flights shown`;







    }











    /* -----------------------------------------



       RENDER



    ----------------------------------------- */







    renderFlightResults(



        filteredFlights



    );







}











/* =========================================================



   RENDER FLIGHT RESULTS



========================================================= */







/* =========================================================



   RENDER FLIGHT RESULTS



========================================================= */







async function renderFlightResults(flights) {

    const resultsEl = $("results");

    if (!resultsEl) {
        console.error("Results container not found.");
        return;
    }

    if (!Array.isArray(flights) || flights.length === 0) {

        resultsEl.innerHTML = `
            <div class="no-results">
                <div class="no-results-icon">✈️</div>

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

                            ${
                                flightNumber
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
                                    ✈
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
                                        🔄
                                    </span>

                                    <span>

                                        <strong>
                                            Layover at
                                            ${escapeHtml(
                                                layoverLabel
                                            )}
                                        </strong>

                                        <small>
                                            ⏱
                                            ${durationText(
                                                layoverDuration
                                            )}

                                            ${
                                                layover.overnight ||
                                                layoverDuration >= 480
                                                    ? " • 🌙 Overnight"
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
                        ───── ✈ ─────
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

                const isRoundTrip =
                    String(
                        flight.trip_type || ""
                    ).toLowerCase() ===
                    "round_trip" ||
                    !!flight.return;


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
                                    ✈️ Outbound reference fare
                                </span>

                                <strong>
                                    ${
                                        outboundReference != null
                                            ? `₹${money(
                                                outboundReference
                                            )}`
                                            : "—"
                                    }
                                </strong>

                            </div>


                            <div class="fare-line">

                                <span>
                                    ↩️ Return reference fare
                                </span>

                                <strong>
                                    ${
                                        returnReference != null
                                            ? `₹${money(
                                                returnReference
                                            )}`
                                            : "—"
                                    }
                                </strong>

                            </div>


                            ${
                                separateTotal != null
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


                                        ${
                                            Number(
                                                separateTotal
                                            ) > price

                                                ? `

                                                    <div class="roundtrip-saving">

                                                        💰 Round-trip saves

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
                   CHEAPEST BADGE
                --------------------------------------------- */

                const cheapestBadge =
                    index === cheapestIndex
                        ? `
                            <div class="cheapest-badge">
                                🏆 CHEAPEST FLIGHT
                            </div>
                          `
                        : "";


                /* ---------------------------------------------
                   CARD
                --------------------------------------------- */

                return `

                    <article
                        class="
                            flight
                            ${
                                index === cheapestIndex
                                    ? "cheapest-flight"
                                    : ""
                            }
                        "
                    >

                        <div class="flight-main">

                            ${cheapestBadge}


                            <div class="trip-type-badge">

                                ✈️

                                ${
                                    isRoundTrip
                                        ? "ROUND TRIP"
                                        : "ONE WAY"
                                }

                            </div>


                            ${outboundHtml}


                            ${returnHtml}

                        </div>


                        <div class="flight-price">

                            <div class="price">
                                ₹${priceText}
                            </div>


                            ${fareBreakdown}


                            <button
                                type="button"
                                class="track-btn"
                                data-price="${price}"
                            >
                                🔔 Track Price
                            </button>

                        </div>

                    </article>
                `;
            }
        )
    );


    /* =========================================================
       INSERT CARDS
    ========================================================= */

    resultsEl.innerHTML =
        cards.join("");


    /* =========================================================
       TRACK BUTTONS
    ========================================================= */

    document
        .querySelectorAll(".track-btn")
        .forEach(button => {

            button.addEventListener(
                "click",
                () => {

                    const price =
                        Number(
                            button.dataset.price
                        );


                    if (!Number.isNaN(price)) {

                        const target =
                            $("target_price");

                        if (target) {

                            target.value =
                                Math.round(
                                    price * 0.9
                                );
                        }
                    }


                    const trackForm =
                        $("trackForm");


                    if (trackForm) {

                        trackForm.scrollIntoView({
                            behavior: "smooth",
                            block: "center"
                        });
                    }


                    const email =
                        $("email");

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











        const data =



            await response.json();











        if (!data.length) {







            $("tracked").innerHTML =



                "No tracked flights yet.";







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







                                    🎯



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







                                    ⏳



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



                <span class="tracked-arrow">→</span>



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



            <button

                type="button"

                class="history-btn"

                data-id="${flight.id}"

            >

                📈 View Price History

            </button>



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







        const response =



            await fetch(



                `/api/history/${id}`



            );











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
