/**
 * SKYTRACKER — DYNAMIC BACKDROP ENGINE
 * High-performance, modular procedural backdrops engineered with quiet luxury aesthetics.
 * 
 * Supports 6 Atmospheric Backdrops:
 * 1. Default (Active Three.js Celestial Sphere & Ambient Grid)
 * 2. Shape Waves (Interactive undulating geometric node lattice)
 * 3. Aero Shards (Floating crystalline atmospheric shards with specular facets)
 * 4. Ferrofluid (Liquid metallic magnetic fluid morphing organically)
 * 5. Gradient Waves (Harmonic liquid silk / aurora ribbon waves)
 * 6. Molten Metal (Specular reflective liquid mercury / champagne ripples)
 * 
 * Architectural Guarantees:
 * - Only ONE active effect loop runs at any time.
 * - Previous loops, listeners, and frame requests are completely destroyed upon switching.
 * - Pauses execution when tab is hidden (visibilitychange) or when reduced motion is preferred.
 * - Canvas is strictly non-interactive (pointer-events: none) with zero interference on UI.
 */

(function (window, document) {
    'use strict';

    let activeEffectId = 'default';
    let activeController = null;
    let canvas = null;
    let ctx = null;
    let animFrameId = null;
    let isTabVisible = !document.hidden;
    let pointerX = window.innerWidth / 2;
    let pointerY = window.innerHeight / 2;
    let targetPointerX = pointerX;
    let targetPointerY = pointerY;

    // Track mouse coordinates with low overhead
    window.addEventListener('pointermove', function (e) {
        targetPointerX = e.clientX;
        targetPointerY = e.clientY;
    }, { passive: true });

    document.addEventListener('visibilitychange', function () {
        isTabVisible = !document.hidden;
        if (!isTabVisible && animFrameId) {
            cancelAnimationFrame(animFrameId);
            animFrameId = null;
        } else if (isTabVisible && activeController && activeController.step) {
            runLoop();
        }
    });

    function getCanvas() {
        if (!canvas) {
            canvas = document.getElementById('dynamicBackdropCanvas');
            if (!canvas) {
                const atmosphere = document.querySelector('.spatial-bg-atmosphere') || document.body;
                canvas = document.createElement('canvas');
                canvas.id = 'dynamicBackdropCanvas';
                canvas.className = 'dynamic-backdrop-canvas';
                canvas.setAttribute('aria-hidden', 'true');
                atmosphere.appendChild(canvas);
            }
            canvas.style.position = 'fixed';
            canvas.style.top = '0';
            canvas.style.left = '0';
            canvas.style.width = '100%';
            canvas.style.height = '100%';
            canvas.style.pointerEvents = 'none';
            canvas.style.zIndex = '0';
            ctx = canvas.getContext('2d', { alpha: true });
            resizeCanvas();
            window.addEventListener('resize', resizeCanvas, { passive: true });
        }
        return canvas;
    }

    function resizeCanvas() {
        if (!canvas) return;
        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        canvas.width = window.innerWidth * dpr;
        canvas.height = window.innerHeight * dpr;
        if (ctx) ctx.scale(dpr, dpr);
    }

    function getThemeAccent() {
        const computed = getComputedStyle(document.documentElement);
        return {
            accent: computed.getPropertyValue('--accent-gold').trim() || '#dfc99e',
            base: computed.getPropertyValue('--bg-base').trim() || '#060912',
            surface: computed.getPropertyValue('--bg-surface').trim() || 'rgba(10, 15, 27, 0.7)'
        };
    }

    function hexToRgba(hex, alpha) {
        let c = hex.replace('#', '');
        if (c.length === 3) c = c[0] + c[0] + c[1] + c[1] + c[2] + c[2];
        const num = parseInt(c, 16);
        if (isNaN(num)) return `rgba(223, 201, 158, ${alpha})`;
        return `rgba(${(num >> 16) & 255}, ${(num >> 8) & 255}, ${num & 255}, ${alpha})`;
    }

    // =========================================================================
    // EFFECT 1: DEFAULT
    // =========================================================================
    const DefaultEffect = {
        init() {
            if (canvas) {
                ctx && ctx.clearRect(0, 0, window.innerWidth, window.innerHeight);
                canvas.style.display = 'none';
            }
            const hero3D = document.getElementById('heroScene3D');
            if (hero3D) hero3D.style.opacity = '1';
        },
        step() {},
        destroy() {}
    };

    // =========================================================================
    // EFFECT 2: SHAPE WAVES (Undulating Geometric Lattice)
    // =========================================================================
    const ShapeWavesEffect = {
        init() {
            getCanvas();
            canvas.style.display = 'block';
            this.time = 0;
            this.gridSpacing = window.innerWidth < 768 ? 44 : 56;
        },
        step(dt) {
            this.time += dt * 0.0012;
            const w = window.innerWidth;
            const h = window.innerHeight;
            ctx.clearRect(0, 0, w, h);

            pointerX += (targetPointerX - pointerX) * 0.05;
            pointerY += (targetPointerY - pointerY) * 0.05;

            const { accent } = getThemeAccent();
            const cols = Math.ceil(w / this.gridSpacing) + 2;
            const rows = Math.ceil(h / this.gridSpacing) + 2;

            ctx.lineWidth = 1;

            for (let r = 0; r < rows; r++) {
                for (let c = 0; c < cols; c++) {
                    const baseX = c * this.gridSpacing - this.gridSpacing;
                    const baseY = r * this.gridSpacing - this.gridSpacing;

                    const dx = baseX - pointerX;
                    const dy = baseY - pointerY;
                    const dist = Math.sqrt(dx * dx + dy * dy);
                    const pointerWave = Math.sin(dist * 0.015 - this.time * 3) * Math.max(0, 1 - dist / 600);

                    const wave = Math.sin(c * 0.35 + this.time * 2) * Math.cos(r * 0.35 + this.time * 1.5);
                    const totalOffset = (wave + pointerWave * 1.8) * 12;

                    const size = Math.max(1.2, 2.5 + wave * 1.4 + pointerWave * 2.2);
                    const alpha = Math.min(0.45, Math.max(0.06, 0.14 + (wave + 1) * 0.1 + pointerWave * 0.25));

                    ctx.fillStyle = hexToRgba(accent, alpha);
                    ctx.beginPath();
                    ctx.arc(baseX, baseY + totalOffset, size, 0, Math.PI * 2);
                    ctx.fill();

                    // Geometric connection lines for nearby nodes
                    if (c < cols - 1 && r % 2 === 0) {
                        const nextWave = Math.sin((c + 1) * 0.35 + this.time * 2) * Math.cos(r * 0.35 + this.time * 1.5);
                        ctx.strokeStyle = hexToRgba(accent, alpha * 0.35);
                        ctx.beginPath();
                        ctx.moveTo(baseX, baseY + totalOffset);
                        ctx.lineTo(baseX + this.gridSpacing, baseY + (nextWave * 12));
                        ctx.stroke();
                    }
                }
            }
        },
        destroy() {}
    };

    // =========================================================================
    // EFFECT 3: AERO SHARDS (Atmospheric Crystalline Polygons)
    // =========================================================================
    const AeroShardsEffect = {
        init() {
            getCanvas();
            canvas.style.display = 'block';
            this.shards = [];
            const count = window.innerWidth < 768 ? 22 : 42;
            for (let i = 0; i < count; i++) {
                this.shards.push(this.createShard(true));
            }
        },
        createShard(randomY) {
            const w = window.innerWidth;
            const h = window.innerHeight;
            return {
                x: Math.random() * w,
                y: randomY ? Math.random() * h : h + 60,
                size: 16 + Math.random() * 42,
                speedY: 0.25 + Math.random() * 0.65,
                speedX: (Math.random() - 0.5) * 0.3,
                angle: Math.random() * Math.PI * 2,
                rotSpeed: (Math.random() - 0.5) * 0.008,
                points: 3 + Math.floor(Math.random() * 2), // Triangles and quadrilaterals
                opacity: 0.08 + Math.random() * 0.18,
                phase: Math.random() * Math.PI * 2
            };
        },
        step(dt) {
            const w = window.innerWidth;
            const h = window.innerHeight;
            ctx.clearRect(0, 0, w, h);

            pointerX += (targetPointerX - pointerX) * 0.04;
            pointerY += (targetPointerY - pointerY) * 0.04;

            const { accent } = getThemeAccent();

            for (let i = 0; i < this.shards.length; i++) {
                const s = this.shards[i];
                s.y -= s.speedY;
                s.x += s.speedX;
                s.angle += s.rotSpeed;
                s.phase += dt * 0.001;

                if (s.y < -80) {
                    this.shards[i] = this.createShard(false);
                    continue;
                }

                const dx = s.x - pointerX;
                const dy = s.y - pointerY;
                const dist = Math.sqrt(dx * dx + dy * dy);
                const push = Math.max(0, 1 - dist / 350) * 20;
                const drawX = s.x + (dx / (dist || 1)) * push;
                const drawY = s.y + (dy / (dist || 1)) * push;

                ctx.save();
                ctx.translate(drawX, drawY);
                ctx.rotate(s.angle);

                // Polished crystalline shard
                ctx.beginPath();
                for (let p = 0; p < s.points; p++) {
                    const theta = (p / s.points) * Math.PI * 2;
                    const r = s.size * (0.6 + Math.sin(p * 1.5) * 0.4);
                    const px = Math.cos(theta) * r;
                    const py = Math.sin(theta) * r;
                    if (p === 0) ctx.moveTo(px, py);
                    else ctx.lineTo(px, py);
                }
                ctx.closePath();

                const grad = ctx.createLinearGradient(-s.size, -s.size, s.size, s.size);
                grad.addColorStop(0, hexToRgba(accent, s.opacity * 1.4));
                grad.addColorStop(0.5, hexToRgba(accent, s.opacity * 0.4));
                grad.addColorStop(1, hexToRgba(accent, s.opacity * 0.05));

                ctx.fillStyle = grad;
                ctx.fill();

                ctx.strokeStyle = hexToRgba(accent, s.opacity * 1.8);
                ctx.lineWidth = 1;
                ctx.stroke();

                ctx.restore();
            }
        },
        destroy() {
            this.shards = [];
        }
    };

    // =========================================================================
    // EFFECT 4: FERROFLUID (Liquid Magnetic Metallic Droplets)
    // =========================================================================
    const FerrofluidEffect = {
        init() {
            getCanvas();
            canvas.style.display = 'block';
            this.time = 0;
            this.blobs = [];
            const count = 7;
            for (let i = 0; i < count; i++) {
                this.blobs.push({
                    x: Math.random() * window.innerWidth,
                    y: Math.random() * window.innerHeight,
                    vx: (Math.random() - 0.5) * 0.8,
                    vy: (Math.random() - 0.5) * 0.8,
                    baseR: 80 + Math.random() * 120,
                    seed: Math.random() * 100
                });
            }
        },
        step(dt) {
            this.time += dt * 0.001;
            const w = window.innerWidth;
            const h = window.innerHeight;
            ctx.clearRect(0, 0, w, h);

            pointerX += (targetPointerX - pointerX) * 0.04;
            pointerY += (targetPointerY - pointerY) * 0.04;

            const { accent } = getThemeAccent();

            for (let b of this.blobs) {
                b.x += b.vx;
                b.y += b.vy;

                if (b.x < -100) b.x = w + 100;
                if (b.x > w + 100) b.x = -100;
                if (b.y < -100) b.y = h + 100;
                if (b.y > h + 100) b.y = -100;

                // Gravitate gently toward pointer
                const gdx = pointerX - b.x;
                const gdy = pointerY - b.y;
                b.x += gdx * 0.005;
                b.y += gdy * 0.005;

                // Draw organic contour fluid
                const radius = b.baseR + Math.sin(this.time * 2 + b.seed) * 22;
                const grad = ctx.createRadialGradient(
                    b.x - radius * 0.2, b.y - radius * 0.2, radius * 0.1,
                    b.x, b.y, radius
                );
                grad.addColorStop(0, hexToRgba(accent, 0.18));
                grad.addColorStop(0.4, hexToRgba(accent, 0.09));
                grad.addColorStop(0.8, hexToRgba(accent, 0.02));
                grad.addColorStop(1, 'rgba(0,0,0,0)');

                ctx.fillStyle = grad;
                ctx.beginPath();
                ctx.arc(b.x, b.y, radius, 0, Math.PI * 2);
                ctx.fill();

                // Specular liquid ring
                ctx.strokeStyle = hexToRgba(accent, 0.12);
                ctx.lineWidth = 1.2;
                ctx.beginPath();
                for (let a = 0; a <= Math.PI * 2; a += 0.2) {
                    const spike = Math.sin(a * 6 + this.time * 3 + b.seed) * 8;
                    const rSpike = radius * 0.82 + spike;
                    const px = b.x + Math.cos(a) * rSpike;
                    const py = b.y + Math.sin(a) * rSpike;
                    if (a === 0) ctx.moveTo(px, py);
                    else ctx.lineTo(px, py);
                }
                ctx.closePath();
                ctx.stroke();
            }
        },
        destroy() {
            this.blobs = [];
        }
    };

    // =========================================================================
    // EFFECT 5: GRADIENT WAVES (Harmonic Silk Aurora Ribbons)
    // =========================================================================
    const GradientWavesEffect = {
        init() {
            getCanvas();
            canvas.style.display = 'block';
            this.time = 0;
        },
        step(dt) {
            this.time += dt * 0.0008;
            const w = window.innerWidth;
            const h = window.innerHeight;
            ctx.clearRect(0, 0, w, h);

            pointerX += (targetPointerX - pointerX) * 0.03;
            pointerY += (targetPointerY - pointerY) * 0.03;

            const { accent } = getThemeAccent();
            const wavesCount = 5;

            for (let i = 0; i < wavesCount; i++) {
                const waveProgress = i / wavesCount;
                const baseY = h * (0.35 + waveProgress * 0.45);
                const amp = 35 + i * 18;
                const freq = 0.0018 + i * 0.0006;
                const speed = (i + 1) * 0.9;

                ctx.beginPath();
                ctx.moveTo(0, h);
                ctx.lineTo(0, baseY);

                for (let x = 0; x <= w; x += 14) {
                    const pDist = Math.abs(x - pointerX);
                    const pFactor = Math.max(0, 1 - pDist / 400) * 28;
                    const y = baseY + Math.sin(x * freq + this.time * speed) * amp
                                    + Math.cos(x * 0.003 - this.time * 1.2) * (amp * 0.5)
                                    - pFactor;
                    ctx.lineTo(x, y);
                }

                ctx.lineTo(w, h);
                ctx.closePath();

                const grad = ctx.createLinearGradient(0, baseY - amp, 0, h);
                const alpha = 0.04 + (i / wavesCount) * 0.07;
                grad.addColorStop(0, hexToRgba(accent, alpha * 1.5));
                grad.addColorStop(0.5, hexToRgba(accent, alpha * 0.6));
                grad.addColorStop(1, 'rgba(0,0,0,0)');

                ctx.fillStyle = grad;
                ctx.fill();

                ctx.strokeStyle = hexToRgba(accent, alpha * 1.2);
                ctx.lineWidth = 1;
                ctx.stroke();
            }
        },
        destroy() {}
    };

    // =========================================================================
    // EFFECT 6: MOLTEN METAL (Reflective Viscous Liquid Specular Waves)
    // =========================================================================
    const MoltenMetalEffect = {
        init() {
            getCanvas();
            canvas.style.display = 'block';
            this.time = 0;
            this.ripples = [];
        },
        step(dt) {
            this.time += dt * 0.001;
            const w = window.innerWidth;
            const h = window.innerHeight;
            ctx.clearRect(0, 0, w, h);

            pointerX += (targetPointerX - pointerX) * 0.05;
            pointerY += (targetPointerY - pointerY) * 0.05;

            const { accent } = getThemeAccent();

            // Background specular mercury pool
            const cx = w * 0.5 + (pointerX - w * 0.5) * 0.15;
            const cy = h * 0.5 + (pointerY - h * 0.5) * 0.15;

            const ringCount = 8;
            for (let r = 0; r < ringCount; r++) {
                const baseRad = 60 + r * 75;
                const pulse = Math.sin(this.time * 1.8 - r * 0.7) * 14;
                const rad = baseRad + pulse;

                ctx.save();
                ctx.translate(cx, cy);

                ctx.beginPath();
                for (let a = 0; a <= Math.PI * 2; a += 0.15) {
                    const noise = Math.sin(a * 4 + this.time * 2 + r) * 12
                                + Math.cos(a * 2 - this.time * 1.5) * 8;
                    const curRad = rad + noise;
                    const px = Math.cos(a) * curRad;
                    const py = Math.sin(a) * curRad * 0.58; // Isometric tilt
                    if (a === 0) ctx.moveTo(px, py);
                    else ctx.lineTo(px, py);
                }
                ctx.closePath();

                const alpha = Math.max(0.02, 0.18 - (r / ringCount) * 0.15);
                ctx.strokeStyle = hexToRgba(accent, alpha * 1.6);
                ctx.lineWidth = 1.4;
                ctx.stroke();

                // Specular liquid highlight
                const grad = ctx.createRadialGradient(0, 0, rad * 0.2, 0, 0, rad);
                grad.addColorStop(0, hexToRgba('#ffffff', alpha * 0.18));
                grad.addColorStop(0.5, hexToRgba(accent, alpha * 0.4));
                grad.addColorStop(1, 'rgba(0,0,0,0)');
                ctx.fillStyle = grad;
                ctx.fill();

                ctx.restore();
            }
        },
        destroy() {
            this.ripples = [];
        }
    };

    // =========================================================================
    // EFFECT REGISTRY & LIFECYCLE CONTROLLER
    // =========================================================================
    const EFFECTS = {
        'default': DefaultEffect,
        'shape-waves': ShapeWavesEffect,
        'aero-shards': AeroShardsEffect,
        'ferrofluid': FerrofluidEffect,
        'gradient-waves': GradientWavesEffect,
        'molten-metal': MoltenMetalEffect
    };

    let lastTimestamp = performance.now();

    function runLoop() {
        if (!isTabVisible || !activeController) return;
        const now = performance.now();
        const dt = Math.min(now - lastTimestamp, 100);
        lastTimestamp = now;

        const prefersReducedMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        if (!prefersReducedMotion && activeController.step) {
            activeController.step(dt);
            animFrameId = requestAnimationFrame(runLoop);
        } else if (activeController.step) {
            // Render a single graceful static frame for reduced-motion
            activeController.step(16);
        }
    }

    function setBackdropEffect(effectId) {
        if (!EFFECTS[effectId]) effectId = 'default';

        // Destroy previous active loop
        if (animFrameId) {
            cancelAnimationFrame(animFrameId);
            animFrameId = null;
        }
        if (activeController && activeController.destroy) {
            activeController.destroy();
        }

        activeEffectId = effectId;
        activeController = EFFECTS[effectId];

        // Notify 3D hero scene to adjust prominence
        const hero3D = document.getElementById('heroScene3D');
        if (hero3D) {
            hero3D.style.opacity = (effectId === 'default') ? '1' : '0.22';
            hero3D.style.transition = 'opacity 0.6s ease';
        }

        activeController.init();
        lastTimestamp = performance.now();
        runLoop();

        document.documentElement.setAttribute('data-backdrop', effectId);
    }

    // Auto-initialize from stored preferences
    document.addEventListener('DOMContentLoaded', function () {
        try {
            const raw = localStorage.getItem('skytracker_appearance_prefs');
            const prefs = raw ? JSON.parse(raw) : {};
            const initial = prefs.backdropEffect || 'default';
            setBackdropEffect(initial);
        } catch (e) {
            setBackdropEffect('default');
        }
    });

    // Public API
    window.SkyTrackerBackdrops = {
        setBackdropEffect,
        getActiveEffect: () => activeEffectId,
        availableEffects: Object.keys(EFFECTS)
    };

})(window, document);
