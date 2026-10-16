/**
 * SKYTRACKER — APPEARANCE & PERSONALIZATION CONTROLLER
 * Architectural state manager for themes, backdrops, custom accents, and PWA installation.
 */

(function (window, document) {
    'use strict';

    const STORAGE_KEY = 'skytracker_appearance_prefs';

    const DEFAULT_PREFS = {
        theme: 'signature',
        backdropEffect: 'default',
        accentColor: '#DFC99E',
        reducedMotion: false
    };

    const THEME_NAMES = ['signature', 'dark', 'monochrome', 'high-contrast', 'midnight', 'custom'];
    const ACCENT_PRESETS = [
        { name: 'Electric Blue', hex: '#00A8FF' },
        { name: 'Teal', hex: '#009688' },
        { name: 'Violet', hex: '#7952E9' },
        { name: 'Rose', hex: '#C23886' },
        { name: 'Amber', hex: '#C77A00' },
        { name: 'Emerald', hex: '#258B50' }
    ];

    let currentPrefs = { ...DEFAULT_PREFS };
    let deferredInstallPrompt = null;

    // =========================================================================
    // COLOR CALCULATIONS & TOKEN GENERATOR
    // =========================================================================
    function parseHex(hex) {
        if (!hex || typeof hex !== 'string') return null;
        let c = hex.trim().replace(/^#/, '');
        if (c.length === 3) c = c[0] + c[0] + c[1] + c[1] + c[2] + c[2];
        if (!/^[0-9A-Fa-f]{6}$/.test(c)) return null;
        return {
            r: parseInt(c.slice(0, 2), 16),
            g: parseInt(c.slice(2, 4), 16),
            b: parseInt(c.slice(4, 6), 16),
            hex: '#' + c.toUpperCase()
        };
    }

    function adjustLightness(rgb, factor) {
        return {
            r: Math.min(255, Math.max(0, Math.round(rgb.r * factor))),
            g: Math.min(255, Math.max(0, Math.round(rgb.g * factor))),
            b: Math.min(255, Math.max(0, Math.round(rgb.b * factor)))
        };
    }

    function rgbToHex(rgb) {
        const toHex = (n) => n.toString(16).padStart(2, '0');
        return `#${toHex(rgb.r)}${toHex(rgb.g)}${toHex(rgb.b)}`.toUpperCase();
    }

    function applyAccentTokens(hex) {
        const rgb = parseHex(hex);
        if (!rgb) return false;

        const lighter = adjustLightness(rgb, 1.15);
        const darker = adjustLightness(rgb, 0.82);
        const root = document.documentElement;

        root.style.setProperty('--accent-gold', rgb.hex);
        root.style.setProperty('--accent', rgb.hex);
        root.style.setProperty('--accent-gold-hover', rgbToHex(lighter));
        root.style.setProperty('--accent-hover', rgbToHex(lighter));
        root.style.setProperty('--accent-focus', rgb.hex);
        root.style.setProperty('--accent-gold-dim', `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.14)`);
        root.style.setProperty('--accent-soft', `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.14)`);
        root.style.setProperty('--accent-gold-border', `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.35)`);
        root.style.setProperty('--border-accent', `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.35)`);
        root.style.setProperty('--accent-gold-glow', `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.24)`);
        root.style.setProperty('--accent-subtle', `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.08)`);
        root.style.setProperty('--accent-gradient', `linear-gradient(135deg, ${rgb.hex} 0%, ${rgbToHex(darker)} 100%)`);

        return true;
    }

    function clearAccentOverrides() {
        const root = document.documentElement;
        [
            '--accent-gold', '--accent', '--accent-gold-hover', '--accent-hover',
            '--accent-focus', '--accent-gold-dim', '--accent-soft', '--accent-gold-border',
            '--border-accent', '--accent-gold-glow', '--accent-subtle', '--accent-gradient'
        ].forEach(prop => root.style.removeProperty(prop));
    }

    // =========================================================================
    // PREFERENCE PERSISTENCE
    // =========================================================================
    function loadPreferences() {
        try {
            const raw = localStorage.getItem(STORAGE_KEY);
            if (raw) {
                const parsed = JSON.parse(raw);
                currentPrefs = {
                    theme: THEME_NAMES.includes(parsed.theme) ? parsed.theme : DEFAULT_PREFS.theme,
                    backdropEffect: parsed.backdropEffect || DEFAULT_PREFS.backdropEffect,
                    accentColor: parseHex(parsed.accentColor) ? parsed.accentColor : DEFAULT_PREFS.accentColor,
                    reducedMotion: Boolean(parsed.reducedMotion)
                };
            }
        } catch (e) {
            console.warn('Could not read appearance preferences from localStorage:', e);
            currentPrefs = { ...DEFAULT_PREFS };
        }
        return currentPrefs;
    }

    function savePreferences(prefs) {
        currentPrefs = { ...currentPrefs, ...prefs };
        try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify(currentPrefs));
            // Backwards compatibility for existing legacy theme toggle
            localStorage.setItem('skytrack_theme', currentPrefs.theme === 'light' ? 'light' : 'dark');
        } catch (e) {
            console.warn('Could not persist appearance preferences to localStorage:', e);
        }

        // Asynchronously synchronize with server if user is authenticated
        if (window.fetch) {
            fetch('/api/appearance/preferences', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(currentPrefs)
            }).catch(() => {}); // Non-blocking fire-and-forget
        }
    }

    // =========================================================================
    // THEME & BACKDROP ENGINE APPLICATION
    // =========================================================================
    function applyTheme(themeName) {
        if (!THEME_NAMES.includes(themeName)) themeName = 'signature';

        document.documentElement.setAttribute('data-theme', themeName);

        if (themeName === 'custom') {
            applyAccentTokens(currentPrefs.accentColor || DEFAULT_PREFS.accentColor);
        } else {
            clearAccentOverrides();
        }

        savePreferences({ theme: themeName });
        updateSettingsPageUI();
    }

    function applyAccentColor(hex) {
        const valid = parseHex(hex);
        if (!valid) return false;

        currentPrefs.accentColor = valid.hex;
        currentPrefs.theme = 'custom';

        document.documentElement.setAttribute('data-theme', 'custom');
        applyAccentTokens(valid.hex);
        savePreferences({ theme: 'custom', accentColor: valid.hex });
        updateSettingsPageUI();
        return true;
    }

    function applyBackdrop(effectId) {
        currentPrefs.backdropEffect = effectId;
        if (window.SkyTrackerBackdrops && window.SkyTrackerBackdrops.setBackdropEffect) {
            window.SkyTrackerBackdrops.setBackdropEffect(effectId);
        } else {
            document.documentElement.setAttribute('data-backdrop', effectId);
        }
        savePreferences({ backdropEffect: effectId });
        updateSettingsPageUI();
    }

    function restoreDefaults() {
        clearAccentOverrides();
        currentPrefs = { ...DEFAULT_PREFS };
        document.documentElement.setAttribute('data-theme', DEFAULT_PREFS.theme);

        if (window.SkyTrackerBackdrops && window.SkyTrackerBackdrops.setBackdropEffect) {
            window.SkyTrackerBackdrops.setBackdropEffect(DEFAULT_PREFS.backdropEffect);
        } else {
            document.documentElement.setAttribute('data-backdrop', DEFAULT_PREFS.backdropEffect);
        }

        savePreferences(DEFAULT_PREFS);
        updateSettingsPageUI();
        showNotification('Default appearance restored successfully.');
    }

    // =========================================================================
    // SETTINGS PAGE UI SYNCHRONIZER
    // =========================================================================
    function updateSettingsPageUI() {
        // 1. Theme preview cards
        document.querySelectorAll('.theme-card').forEach(card => {
            const theme = card.dataset.theme;
            const isSelected = theme === currentPrefs.theme;
            card.classList.toggle('active', isSelected);
            card.setAttribute('aria-checked', isSelected ? 'true' : 'false');
            const indicator = card.querySelector('.theme-card-indicator');
            if (indicator) {
                indicator.innerHTML = isSelected
                    ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"></polyline></svg>'
                    : '';
            }
        });

        // 2. Backdrop effect cards
        document.querySelectorAll('.backdrop-card').forEach(card => {
            const effect = card.dataset.effect;
            const isSelected = effect === currentPrefs.backdropEffect;
            card.classList.toggle('active', isSelected);
            card.setAttribute('aria-checked', isSelected ? 'true' : 'false');
            const indicator = card.querySelector('.backdrop-card-indicator');
            if (indicator) {
                indicator.innerHTML = isSelected
                    ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"></polyline></svg>'
                    : '';
            }
        });

        // 3. Preset color swatches
        document.querySelectorAll('.preset-swatch').forEach(swatch => {
            const hex = swatch.dataset.hex;
            const isSelected = currentPrefs.theme === 'custom' && hex && hex.toUpperCase() === (currentPrefs.accentColor || '').toUpperCase();
            swatch.classList.toggle('active', isSelected);
            swatch.setAttribute('aria-checked', isSelected ? 'true' : 'false');
        });

        // 4. Hex input & native picker
        const hexInput = document.getElementById('customHexInput');
        if (hexInput && document.activeElement !== hexInput) {
            hexInput.value = currentPrefs.accentColor || DEFAULT_PREFS.accentColor;
        }

        const nativePicker = document.getElementById('nativeColorPicker');
        if (nativePicker) {
            nativePicker.value = currentPrefs.accentColor || DEFAULT_PREFS.accentColor;
        }

        const previewSwatch = document.getElementById('customColorPreview');
        if (previewSwatch) {
            previewSwatch.style.backgroundColor = currentPrefs.accentColor || DEFAULT_PREFS.accentColor;
        }
    }

    function showNotification(message) {
        let toast = document.getElementById('appearanceToast');
        if (!toast) {
            toast = document.createElement('div');
            toast.id = 'appearanceToast';
            toast.className = 'appearance-toast';
            document.body.appendChild(toast);
        }
        toast.textContent = message;
        toast.classList.add('visible');
        clearTimeout(toast._timer);
        toast._timer = setTimeout(() => {
            toast.classList.remove('visible');
        }, 3200);
    }

    // =========================================================================
    // PWA INSTALLATION MANAGER
    // =========================================================================
    function initPWAManager() {
        // Register Service Worker if supported
        if ('serviceWorker' in navigator) {
            window.addEventListener('load', () => {
                navigator.serviceWorker.register('/sw.js').catch(err => {
                    console.info('Service Worker registration note:', err);
                });
            });
        }

        // Intercept beforeinstallprompt
        window.addEventListener('beforeinstallprompt', (e) => {
            e.preventDefault();
            deferredInstallPrompt = e;
            updatePWAUI('available');
        });

        window.addEventListener('appinstalled', () => {
            deferredInstallPrompt = null;
            updatePWAUI('installed');
            showNotification('SkyTracker installed to your device.');
        });

        // Check if running in standalone PWA mode
        const isStandalone = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone;
        if (isStandalone) {
            updatePWAUI('installed');
        } else {
            // Check iOS Safari
            const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream;
            if (isIOS) {
                updatePWAUI('ios');
            }
        }
    }

    function updatePWAUI(status) {
        const installBtn = document.getElementById('pwaInstallBtn');
        const installStatus = document.getElementById('pwaInstallStatus');
        const iosGuide = document.getElementById('pwaIosGuide');

        if (!installBtn) return;

        if (status === 'installed') {
            installBtn.style.display = 'none';
            if (installStatus) {
                installStatus.textContent = 'SkyTracker App Installed';
                installStatus.style.display = 'inline-flex';
            }
            if (iosGuide) iosGuide.style.display = 'none';
        } else if (status === 'available') {
            installBtn.style.display = 'inline-flex';
            if (installStatus) installStatus.style.display = 'none';
            if (iosGuide) iosGuide.style.display = 'none';
        } else if (status === 'ios') {
            installBtn.style.display = 'none';
            if (iosGuide) iosGuide.style.display = 'block';
        }
    }

    function triggerPWAInstall() {
        if (!deferredInstallPrompt) {
            showNotification('Direct install is supported in Chromium browsers or via Safari "Add to Home Screen".');
            return;
        }
        deferredInstallPrompt.prompt();
        deferredInstallPrompt.userChoice.then(choiceResult => {
            if (choiceResult.outcome === 'accepted') {
                updatePWAUI('installed');
            }
            deferredInstallPrompt = null;
        });
    }

    // =========================================================================
    // MOBILE BOTTOM NAVIGATION & MORE DRAWER CONTROLLER
    // =========================================================================
    function initMobileNav() {
        const moreBtn = document.getElementById('mobileNavMoreBtn');
        const moreDrawer = document.getElementById('mobileMoreSheet');
        const moreBackdrop = document.getElementById('mobileMoreBackdrop');
        const closeBtn = document.getElementById('mobileMoreCloseBtn');

        if (!moreBtn || !moreDrawer) return;

        function openDrawer() {
            moreDrawer.classList.add('open');
            moreDrawer.setAttribute('aria-hidden', 'false');
            if (moreBackdrop) moreBackdrop.classList.add('visible');
            moreBtn.classList.add('active');
            document.body.style.overflow = 'hidden';
        }

        function closeDrawer() {
            moreDrawer.classList.remove('open');
            moreDrawer.setAttribute('aria-hidden', 'true');
            if (moreBackdrop) moreBackdrop.classList.remove('visible');
            moreBtn.classList.remove('active');
            document.body.style.overflow = '';
        }

        moreBtn.addEventListener('click', (e) => {
            e.preventDefault();
            if (moreDrawer.classList.contains('open')) closeDrawer();
            else openDrawer();
        });

        if (closeBtn) closeBtn.addEventListener('click', closeDrawer);
        if (moreBackdrop) moreBackdrop.addEventListener('click', closeDrawer);

        window.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && moreDrawer.classList.contains('open')) {
                closeDrawer();
            }
        });
    }

    // =========================================================================
    // INITIALIZATION & EVENT DELEGATION
    // =========================================================================
    function init() {
        loadPreferences();

        // 1. Apply initial theme & accent
        document.documentElement.setAttribute('data-theme', currentPrefs.theme);
        if (currentPrefs.theme === 'custom' && currentPrefs.accentColor) {
            applyAccentTokens(currentPrefs.accentColor);
        }

        // 2. Setup DOM event handlers if settings page is loaded
        const container = document.getElementById('appearanceSettingsContainer');
        if (container) {
            // Theme selection cards
            container.querySelectorAll('.theme-card').forEach(card => {
                card.addEventListener('click', () => {
                    const theme = card.dataset.theme;
                    if (theme) applyTheme(theme);
                });
                card.addEventListener('keydown', (e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        const theme = card.dataset.theme;
                        if (theme) applyTheme(theme);
                    }
                });
            });

            // Backdrop selection cards
            container.querySelectorAll('.backdrop-card').forEach(card => {
                card.addEventListener('click', () => {
                    const effect = card.dataset.effect;
                    if (effect) applyBackdrop(effect);
                });
                card.addEventListener('keydown', (e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        const effect = card.dataset.effect;
                        if (effect) applyBackdrop(effect);
                    }
                });
            });

            // Preset swatches
            container.querySelectorAll('.preset-swatch').forEach(swatch => {
                swatch.addEventListener('click', () => {
                    const hex = swatch.dataset.hex;
                    if (hex) applyAccentColor(hex);
                });
            });

            // Native color picker
            const nativePicker = document.getElementById('nativeColorPicker');
            if (nativePicker) {
                nativePicker.addEventListener('input', (e) => {
                    applyAccentColor(e.target.value);
                });
            }

            // Hex input
            const hexInput = document.getElementById('customHexInput');
            if (hexInput) {
                hexInput.addEventListener('input', (e) => {
                    const val = e.target.value.trim();
                    if (/^#?[0-9A-Fa-f]{6}$/.test(val)) {
                        applyAccentColor(val.startsWith('#') ? val : '#' + val);
                    }
                });
                hexInput.addEventListener('blur', (e) => {
                    const val = e.target.value.trim();
                    if (!parseHex(val)) {
                        hexInput.value = currentPrefs.accentColor || DEFAULT_PREFS.accentColor;
                    }
                });
            }

            // Reset accent button
            const resetAccentBtn = document.getElementById('btnResetAccent');
            if (resetAccentBtn) {
                resetAccentBtn.addEventListener('click', () => {
                    applyAccentColor(DEFAULT_PREFS.accentColor);
                    showNotification('Accent color reset to champagne gold.');
                });
            }

            // Restore all defaults action
            const restoreBtn = document.getElementById('btnRestoreDefaults');
            if (restoreBtn) {
                restoreBtn.addEventListener('click', restoreDefaults);
            }

            // PWA Install button
            const pwaBtn = document.getElementById('pwaInstallBtn');
            if (pwaBtn) {
                pwaBtn.addEventListener('click', triggerPWAInstall);
            }

            updateSettingsPageUI();
        }

        initPWAManager();
        initMobileNav();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // Export API
    window.SkyTrackerAppearance = {
        applyTheme,
        applyAccentColor,
        applyBackdrop,
        restoreDefaults,
        getPreferences: () => ({ ...currentPrefs }),
        THEME_NAMES,
        ACCENT_PRESETS
    };

})(window, document);
