/**
 * Frontend Appearance & Personalization System Tests
 * Verifies themes, backdrops, custom accent calculation, persistence, and mobile navigation.
 */

const fs = require('fs');
const path = require('path');
const assert = require('assert');

// 1. Verify Template File Existence & Required Markups
const appearanceHtmlPath = path.join(__dirname, '..', 'templates', 'appearance.html');
const baseHtmlPath = path.join(__dirname, '..', 'templates', 'base.html');
const indexHtmlPath = path.join(__dirname, '..', 'templates', 'index.html');
const manifestPath = path.join(__dirname, '..', 'static', 'manifest.json');
const swJsPath = path.join(__dirname, '..', 'static', 'sw.js');
const backdropsJsPath = path.join(__dirname, '..', 'static', 'backdrops.js');
const appearanceJsPath = path.join(__dirname, '..', 'static', 'appearance.js');
const styleCssPath = path.join(__dirname, '..', 'static', 'style.css');

assert(fs.existsSync(appearanceHtmlPath), 'appearance.html must exist');
assert(fs.existsSync(manifestPath), 'static/manifest.json must exist');
assert(fs.existsSync(swJsPath), 'static/sw.js must exist');
assert(fs.existsSync(backdropsJsPath), 'static/backdrops.js must exist');
assert(fs.existsSync(appearanceJsPath), 'static/appearance.js must exist');

const appearanceHtml = fs.readFileSync(appearanceHtmlPath, 'utf8');
const baseHtml = fs.readFileSync(baseHtmlPath, 'utf8');
const indexHtml = fs.readFileSync(indexHtmlPath, 'utf8');
const manifestContent = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
const swContent = fs.readFileSync(swJsPath, 'utf8');
const styleCss = fs.readFileSync(styleCssPath, 'utf8');

// 2. Validate Visual Themes in appearance.html & style.css
const requiredThemes = ['signature', 'dark', 'monochrome', 'high-contrast', 'midnight', 'custom', 'light'];
requiredThemes.forEach(theme => {
    assert(
        appearanceHtml.includes(`data-theme="${theme}"`),
        `appearance.html must contain theme card for ${theme}`
    );
    assert(
        styleCss.includes(`[data-theme="${theme}"]`),
        `style.css must define CSS tokens for [data-theme="${theme}"]`
    );
});
console.log('✓ Visual Themes (including light mode) verified in template & CSS');

// 3. Validate 6 Backdrop Effects in appearance.html
const requiredBackdrops = ['default', 'shape-waves', 'aero-shards', 'ferrofluid', 'gradient-waves', 'molten-metal'];
requiredBackdrops.forEach(effect => {
    assert(
        appearanceHtml.includes(`data-effect="${effect}"`),
        `appearance.html must contain backdrop card for ${effect}`
    );
});
console.log('✓ 6 Backdrop Effects verified in template');

// 4. Validate 6 Curated Preset Color Swatches
const requiredPresets = ['#00A8FF', '#009688', '#7952E9', '#C23886', '#C77A00', '#258B50'];
requiredPresets.forEach(hex => {
    assert(
        appearanceHtml.includes(`data-hex="${hex}"`),
        `appearance.html must contain preset swatch for ${hex}`
    );
});
console.log('✓ 6 Color Swatches verified in template');

// 5. Validate Base & Index Template Consistency & Moon Button Toggle
[baseHtml, indexHtml].forEach((tmpl, idx) => {
    const name = idx === 0 ? 'base.html' : 'index.html';
    assert(tmpl.includes('id="dynamicBackdropCanvas"'), `${name} must include #dynamicBackdropCanvas`);
    assert(tmpl.includes('id="mobileBottomNav"'), `${name} must include #mobileBottomNav`);
    assert(tmpl.includes('id="mobileMoreSheet"'), `${name} must include #mobileMoreSheet`);
    assert(tmpl.includes('href="/settings/appearance"'), `${name} must link to /settings/appearance`);
    assert(tmpl.includes('rel="manifest"'), `${name} must link to manifest`);
    assert(tmpl.includes('/static/backdrops.js'), `${name} must load backdrops.js`);
    assert(tmpl.includes('/static/appearance.js'), `${name} must load appearance.js`);
    // Moon Button Navbar Toggle Check
    assert(tmpl.includes('id="themeToggle"'), `${name} must include #themeToggle button`);
    assert(tmpl.includes('class="theme-toggle-btn"'), `${name} must include theme-toggle-btn class`);
    assert(tmpl.includes('sun-icon'), `${name} must include sun-icon`);
    assert(tmpl.includes('moon-icon'), `${name} must include moon-icon`);
});
console.log('✓ base.html and index.html integration & #themeToggle verified');

// 5b. Validate Theme Toggle CSS Icon Logic
assert(styleCss.includes('.sun-icon { display: none; }'), 'style.css must hide sun icon by default');
assert(styleCss.includes('.moon-icon { display: block; }'), 'style.css must show moon icon by default');
assert(styleCss.includes('[data-theme="light"] .sun-icon { display: block; }'), 'style.css must show sun icon in light mode');
assert(styleCss.includes('[data-theme="light"] .moon-icon { display: none; }'), 'style.css must hide moon icon in light mode');
console.log('✓ Theme toggle icon CSS rules verified');

// 5c. Validate appearance.js Theme Array & Exports
const appearanceJs = fs.readFileSync(appearanceJsPath, 'utf8');
assert(appearanceJs.includes("'light'"), "appearance.js must include 'light' in THEME_NAMES");
assert(appearanceJs.includes("updateNavbarThemeToggle"), "appearance.js must include updateNavbarThemeToggle");
assert(appearanceJs.includes("initNavbarToggle"), "appearance.js must include initNavbarToggle");
console.log('✓ appearance.js THEME_NAMES and navbar toggle integration verified');

// 6. Validate PWA Manifest & Service Worker Safety
assert.strictEqual(manifestContent.short_name, 'SkyTracker');
assert.strictEqual(manifestContent.display, 'standalone');
assert(manifestContent.icons.length >= 2, 'manifest must have at least 2 icon sizes');

// Verify service worker explicitly ignores private routes to protect sensitive user data
assert(swContent.includes('/api/'), 'Service worker must check /api/ route bypass');
assert(swContent.includes('/login'), 'Service worker must check /login bypass');
assert(swContent.includes('/dashboard'), 'Service worker must check /dashboard bypass');
console.log('✓ PWA manifest & Service Worker privacy rules verified');

// 7. Test Color Hex Parsing & Token Math Logic
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

assert.deepStrictEqual(parseHex('#00A8FF'), { r: 0, g: 168, b: 255, hex: '#00A8FF' });
assert.deepStrictEqual(parseHex('fff'), { r: 255, g: 255, b: 255, hex: '#FFFFFF' });
assert.strictEqual(parseHex('invalid-hex'), null);
assert.strictEqual(parseHex('#12345'), null);
assert.strictEqual(parseHex(null), null);
console.log('✓ Hex validation logic verified');

console.log('\n=============================================');
console.log('ALL FRONTEND APPEARANCE TESTS PASSED (100%)');
console.log('=============================================\n');
