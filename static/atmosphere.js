/**
 * SKYTRACKER — ATMOSPHERIC EXPERIENCE & LUXURY INTERACTIONS
 * 1. Web Audio Procedural Ethereal Soundscape (Ambient Harmonic Drone & Chimes)
 * 2. Real-Time Worldwide Chronometers (UTC, Tokyo, London, New York, Paris)
 * 3. Fluid Ethereal Magnetic Cursor Follower
 * 4. Curated Surreal Route Selector Bridge to Search Engine
 * 5. Gyroscopic 3D Card Tilt Physics
 */

(function () {
    'use strict';

    // =========================================================================
    // 1. WEB AUDIO ETHEREAL SOUNDSCAPE (ZERO EXTERNAL ASSETS)
    // =========================================================================
    let audioCtx = null;
    let masterGain = null;
    let droneOsc1 = null;
    let droneOsc2 = null;
    let droneOsc3 = null;
    let lfoOsc = null;
    let isAudioActive = false;

    function initAudio() {
        if (audioCtx) return;

        const AudioContextClass = window.AudioContext || window.webkitAudioContext;
        if (!AudioContextClass) return;

        audioCtx = new AudioContextClass();

        // Master Gain
        masterGain = audioCtx.createGain();
        masterGain.gain.setValueAtTime(0.0001, audioCtx.currentTime);

        // Low-pass warm filter (stratosphere acoustic dampening)
        const lowPass = audioCtx.createBiquadFilter();
        lowPass.type = 'lowpass';
        lowPass.frequency.setValueAtTime(420, audioCtx.currentTime);
        lowPass.Q.setValueAtTime(2.0, audioCtx.currentTime);

        masterGain.connect(lowPass);
        lowPass.connect(audioCtx.destination);

        // Harmonic Drone Oscillators (A1 = 55Hz, A2 = 110Hz, E3 = 165Hz)
        droneOsc1 = audioCtx.createOscillator();
        droneOsc1.type = 'sine';
        droneOsc1.frequency.setValueAtTime(55.0, audioCtx.currentTime);

        droneOsc2 = audioCtx.createOscillator();
        droneOsc2.type = 'sine';
        droneOsc2.frequency.setValueAtTime(110.0, audioCtx.currentTime);

        droneOsc3 = audioCtx.createOscillator();
        droneOsc3.type = 'triangle';
        droneOsc3.frequency.setValueAtTime(164.81, audioCtx.currentTime);

        // Slow LFO for organic breath (0.08 Hz)
        lfoOsc = audioCtx.createOscillator();
        lfoOsc.type = 'sine';
        lfoOsc.frequency.setValueAtTime(0.08, audioCtx.currentTime);

        const lfoGain = audioCtx.createGain();
        lfoGain.gain.setValueAtTime(0.025, audioCtx.currentTime);
        lfoOsc.connect(lfoGain);
        lfoGain.connect(masterGain.gain);

        droneOsc1.connect(masterGain);
        droneOsc2.connect(masterGain);
        droneOsc3.connect(masterGain);

        droneOsc1.start();
        droneOsc2.start();
        droneOsc3.start();
        lfoOsc.start();
    }

    function toggleAudio() {
        if (!audioCtx) {
            initAudio();
        }

        if (audioCtx.state === 'suspended') {
            audioCtx.resume();
        }

        isAudioActive = !isAudioActive;
        const tag = document.getElementById('audioStateTag');
        const btn = document.getElementById('audioAtmosphereToggle');

        if (isAudioActive) {
            masterGain.gain.setTargetAtTime(0.07, audioCtx.currentTime, 1.8);
            if (tag) tag.textContent = 'RESONANT';
            if (btn) btn.classList.add('active');
            playChime(659.25, 0.4); // Welcome harmonic chime
        } else {
            masterGain.gain.setTargetAtTime(0.00001, audioCtx.currentTime, 0.8);
            if (tag) tag.textContent = 'MUTE';
            if (btn) btn.classList.remove('active');
        }
    }

    function playChime(freq = 528, duration = 1.2) {
        if (!isAudioActive || !audioCtx) return;

        try {
            const now = audioCtx.currentTime;
            const osc = audioCtx.createOscillator();
            const gain = audioCtx.createGain();

            osc.type = 'sine';
            osc.frequency.setValueAtTime(freq, now);
            // Slight pitch glide up
            osc.frequency.exponentialRampToValueAtTime(freq * 1.01, now + duration);

            gain.gain.setValueAtTime(0.035, now);
            gain.gain.exponentialRampToValueAtTime(0.0001, now + duration);

            osc.connect(gain);
            gain.connect(audioCtx.destination);

            osc.start(now);
            osc.stop(now + duration);
        } catch (e) {
            // Audio context may not be ready
        }
    }

    // Attach audio toggle button listener
    const audioBtn = document.getElementById('audioAtmosphereToggle');
    if (audioBtn) {
        audioBtn.addEventListener('click', toggleAudio);
    }

    // Attach chime triggers to interactive luxury elements
    document.addEventListener('mouseover', (e) => {
        const target = e.target.closest('.destination-card, .cta-primary, .cta-secondary, .segment-btn, .metric-card');
        if (target && isAudioActive) {
            const chords = [432, 540, 648, 864];
            const randomPitch = chords[Math.floor(Math.random() * chords.length)];
            playChime(randomPitch, 0.9);
        }
    });

    // =========================================================================
    // 2. WORLDWIDE CHRONOMETERS (UTC, TOKYO, LONDON, NEW YORK, PARIS)
    // =========================================================================
    function updateWorldClocks() {
        const now = new Date();

        function formatTime(timeZone) {
            return new Intl.DateTimeFormat('en-GB', {
                timeZone,
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit',
                hour12: false
            }).format(now);
        }

        const utcStr = formatTime('UTC') + ' UTC';
        const tokyoStr = formatTime('Asia/Tokyo');
        const londonStr = formatTime('Europe/London');
        const nyStr = formatTime('America/New_York');
        const parisStr = formatTime('Europe/Paris');

        const elUtc = document.getElementById('heroUtcClock');
        if (elUtc) elUtc.textContent = utcStr;

        const clockTokyo = document.getElementById('clockTokyo');
        if (clockTokyo) clockTokyo.textContent = tokyoStr;

        const clockLondon = document.getElementById('clockLondon');
        if (clockLondon) clockLondon.textContent = londonStr;

        const clockNy = document.getElementById('clockNewYork');
        if (clockNy) clockNy.textContent = nyStr;

        const clockParis = document.getElementById('clockParis');
        if (clockParis) clockParis.textContent = parisStr;
    }

    setInterval(updateWorldClocks, 1000);
    updateWorldClocks();

    // =========================================================================
    // 3. FLUID ETHEREAL MAGNETIC CURSOR
    // =========================================================================
    const cursor = document.getElementById('luxuryCursor');
    const cursorFollower = document.getElementById('luxuryCursorFollower');

    if (cursor && cursorFollower && window.matchMedia('(pointer: fine)').matches) {
        let mouseX = -100;
        let mouseY = -100;
        let followerX = -100;
        let followerY = -100;

        window.addEventListener('mousemove', (e) => {
            mouseX = e.clientX;
            mouseY = e.clientY;
            cursor.style.transform = `translate3d(${mouseX}px, ${mouseY}px, 0)`;
        }, { passive: true });

        function renderCursor() {
            followerX += (mouseX - followerX) * 0.18;
            followerY += (mouseY - followerY) * 0.18;
            cursorFollower.style.transform = `translate3d(${followerX}px, ${followerY}px, 0)`;
            requestAnimationFrame(renderCursor);
        }
        renderCursor();

        // Magnetic hover states on clickable targets
        const hoverTargets = 'a, button, input, select, .destination-card, .metric-card, .segment-btn, .filter-pill';
        document.addEventListener('mouseover', (e) => {
            if (e.target.closest(hoverTargets)) {
                cursorFollower.classList.add('cursor-hover');
                cursor.classList.add('cursor-dot-hover');
            }
        });

        document.addEventListener('mouseout', (e) => {
            if (e.target.closest(hoverTargets)) {
                cursorFollower.classList.remove('cursor-hover');
                cursor.classList.remove('cursor-dot-hover');
            }
        });
    }

    // =========================================================================
    // 4. CURATED SURREAL DESTINATIONS ROUTE SELECTOR
    // =========================================================================
    // Connects curated editorial destination cards directly to flight search
    const destinationBtns = document.querySelectorAll('.btn-select-route');
    destinationBtns.forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            const toIata = btn.dataset.toIata;
            const toCity = btn.dataset.toCity;

            const departureInput = document.getElementById('departure');
            const arrivalInput = document.getElementById('arrival');

            // Set destination only. Origin must remain empty by default for user entry.
            if (arrivalInput && toIata) {
                arrivalInput.value = `${toCity} (${toIata})`;
                arrivalInput.dataset.iata = toIata;
                arrivalInput.dataset.city = toCity;
            }

            // Focus departure or outbound date if blank, without silently populating demo dates

            // Smooth scroll to the search console
            const searchSection = document.getElementById('search');
            if (searchSection) {
                searchSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }

            // Visual feedback pulse on inputs
            [departureInput, arrivalInput].forEach(inp => {
                if (!inp) return;
                inp.classList.add('input-highlight-pulse');
                setTimeout(() => inp.classList.remove('input-highlight-pulse'), 1200);
            });

            if (isAudioActive) {
                playChime(880, 1.4);
            }
        });
    });

    // =========================================================================
    // 5. GYROSCOPIC 3D CARD TILT PHYSICS
    // =========================================================================
    const tiltCards = document.querySelectorAll('[data-tilt="true"]');
    tiltCards.forEach(card => {
        card.addEventListener('mousemove', (e) => {
            const rect = card.getBoundingClientRect();
            const x = e.clientX - rect.left;
            const y = e.clientY - rect.top;
            const centerX = rect.width / 2;
            const centerY = rect.height / 2;
            const rotateX = ((y - centerY) / centerY) * -7;
            const rotateY = ((x - centerX) / centerX) * 7;

            card.style.transform = `perspective(1000px) rotateX(${rotateX}deg) rotateY(${rotateY}deg) translateY(-4px)`;
        });

        card.addEventListener('mouseleave', () => {
            card.style.transform = 'perspective(1000px) rotateX(0deg) rotateY(0deg) translateY(0px)';
        });
    });

    // =========================================================================
    // 6. LUXURY NAVBAR SCROLL BLUR STATE
    // =========================================================================
    const navbar = document.getElementById('navbar');
    if (navbar) {
        window.addEventListener('scroll', () => {
            if (window.scrollY > 40) {
                navbar.classList.add('scrolled');
            } else {
                navbar.classList.remove('scrolled');
            }
        }, { passive: true });
    }

    // =========================================================================
    // 7. POLISHED ACCESSIBLE MOBILE MENU DRAWER
    // =========================================================================
    const mobileMenuBtn = document.getElementById('mobileMenuToggle');
    const mobileDrawer = document.getElementById('mobileMenuDrawer');
    const mobileCloseBtn = document.getElementById('mobileMenuCloseBtn');
    const mobileLinks = document.querySelectorAll('.mobile-nav-link');

    function openMobileMenu() {
        if (!mobileDrawer || !mobileMenuBtn) return;
        mobileDrawer.classList.add('open');
        mobileDrawer.setAttribute('aria-hidden', 'false');
        mobileMenuBtn.classList.add('active');
        mobileMenuBtn.setAttribute('aria-expanded', 'true');
        document.body.style.overflow = 'hidden';

        // Focus first navigation link
        const firstLink = mobileDrawer.querySelector('.mobile-nav-link');
        if (firstLink) setTimeout(() => firstLink.focus(), 50);
    }

    function closeMobileMenu() {
        if (!mobileDrawer || !mobileMenuBtn) return;
        mobileDrawer.classList.remove('open');
        mobileDrawer.setAttribute('aria-hidden', 'true');
        mobileMenuBtn.classList.remove('active');
        mobileMenuBtn.setAttribute('aria-expanded', 'false');
        document.body.style.overflow = '';
    }

    if (mobileMenuBtn && mobileDrawer) {
        mobileMenuBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            if (mobileDrawer.classList.contains('open')) {
                closeMobileMenu();
            } else {
                openMobileMenu();
            }
        });

        if (mobileCloseBtn) {
            mobileCloseBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                closeMobileMenu();
                mobileMenuBtn.focus();
            });
        }

        // Automatic dismissal after navigation
        mobileLinks.forEach(link => {
            link.addEventListener('click', () => {
                closeMobileMenu();
            });
        });

        // Escape key dismissal with focus return
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && mobileDrawer.classList.contains('open')) {
                closeMobileMenu();
                mobileMenuBtn.focus();
            }
        });

        // Backdrop click dismissal
        mobileDrawer.addEventListener('click', (e) => {
            if (e.target === mobileDrawer) {
                closeMobileMenu();
                mobileMenuBtn.focus();
            }
        });
    }

    // =========================================================================
    // 8. LUXURY PRICE TREND PREVIEW (CHART.JS)
    // =========================================================================
    const trendCanvas = document.getElementById('priceTrendCanvas');
    if (trendCanvas && typeof Chart !== 'undefined') {
        const ctx = trendCanvas.getContext('2d');
        const gradient = ctx.createLinearGradient(0, 0, 0, 320);
        gradient.addColorStop(0, 'rgba(223, 201, 158, 0.35)');
        gradient.addColorStop(0.6, 'rgba(223, 201, 158, 0.08)');
        gradient.addColorStop(1, 'rgba(223, 201, 158, 0.0)');

        const labels = ['Nov 01', 'Nov 05', 'Nov 10', 'Nov 15', 'Nov 20', 'Nov 25', 'Nov 30', 'Dec 05', 'Dec 10', 'Dec 15', 'Dec 20', 'Dec 25'];
        const dataValues = [46200, 44800, 41200, 38900, 37500, 36200, 38100, 42900, 48500, 52100, 56800, 59400];

        new Chart(ctx, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Indicative Global Route Median (INR)',
                    data: dataValues,
                    borderColor: '#dfc99e',
                    borderWidth: 2,
                    tension: 0.42,
                    backgroundColor: gradient,
                    fill: true,
                    pointBackgroundColor: '#dfc99e',
                    pointBorderColor: '#05070d',
                    pointBorderWidth: 2,
                    pointRadius: 4,
                    pointHoverRadius: 7,
                    pointHoverBackgroundColor: '#ffffff',
                    pointHoverBorderColor: '#dfc99e'
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: 'rgba(9, 13, 24, 0.92)',
                        titleColor: '#dfc99e',
                        bodyColor: '#e5e7eb',
                        borderColor: 'rgba(223, 201, 158, 0.25)',
                        borderWidth: 1,
                        padding: 12,
                        displayColors: false,
                        callbacks: {
                            label: function (ctx) {
                                return `₹ ${ctx.parsed.y.toLocaleString('en-IN')} (Optimal Window)`;
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        grid: { color: 'rgba(255, 255, 255, 0.04)' },
                        ticks: { color: 'rgba(229, 231, 235, 0.55)', font: { family: 'Space Mono', size: 10 } }
                    },
                    y: {
                        grid: { color: 'rgba(255, 255, 255, 0.04)' },
                        ticks: {
                            color: 'rgba(229, 231, 235, 0.55)',
                            font: { family: 'Space Mono', size: 10 },
                            callback: function (val) {
                                return '₹' + (val / 1000) + 'k';
                            }
                        }
                    }
                }
            }
        });
    }

})();
