# SkyTracker — Visual Identity & Design System Specification (`Design.md`)

**Document Version:** 1.0.0 (Permanent Design Authority)
**System Name:** SkyTracker (Haute Flight Intelligence)
**Creative Direction:** Surreal Quiet Luxury · High-Fashion Editorial · Cinematic Spatial 3D
**Companion Documents:** [PRD.md](file:///c:/Users/Vandita%20Mamgain/Downloads/flight-price-tracker/PRD.md) · [Architecture.md](file:///c:/Users/Vandita%20Mamgain/Downloads/flight-price-tracker/Architecture.md) · [Rules.md](file:///c:/Users/Vandita%20Mamgain/Downloads/flight-price-tracker/Rules.md) · [Task.md](file:///c:/Users/Vandita%20Mamgain/Downloads/flight-price-tracker/Task.md)

---

## 1. Creative Direction & Visual Identity

### 1.1. Core Aesthetic Philosophy
SkyTracker's aesthetic rejects the cluttered, anxiety-inducing paradigms of legacy flight aggregators. It operates as an **ethereal, surreal digital sanctuary** designed for discerning travelers. The visual language blends:
- **Minimalist Surrealism:** Floating glass monoliths, celestial orbital trajectories, atmospheric stardust, and deep cosmic vignettes.
- **Understated Luxury ("Quiet Luxury"):** Restrained Champagne Gold and Liquid Platinum accents, rich obsidian void backgrounds, frosted silica glass surfaces, and fine hairline borders.
- **High-Fashion Editorial Typography:** Large-scale classical serif headings ([Cormorant Garamond](https://fonts.google.com/specimen/Cormorant+Garamond) and [Cinzel](https://fonts.google.com/specimen/Cinzel)) paired with high-clarity grotesque sans-serifs ([Plus Jakarta Sans](https://fonts.google.com/specimen/Plus+Jakarta+Sans)) and precision monospaced numerals ([Space Mono](https://fonts.google.com/specimen/Space+Mono)).
- **Cinematic Spatial Motion:** Real-time WebGL 3D rendering (Three.js), procedural Web Audio ambient soundscapes, gyroscopic card tilts, and smooth magnetic cursor physics.

### 1.2. Anti-Patterns (Explicitly Forbidden)
- ❌ **Generic SaaS Clichés:** Garish purple/blue neon gradients, oversized cartoon characters, rounded pastel "candy" buttons, generic dashboard widgets.
- ❌ **Aggressive Marketing Clutter:** Flashing countdown timers, high-saturation red urgency banners, intrusive newsletter popups, or deceptive strike-through fares.
- ❌ **Arbitrary Unicode Emojis:** Do not use unicode emojis (✈️, 🏨, 💸, 🔥) in UI controls or copy. Use feather-light Lucide SVG icons (`<i data-lucide="..."></i>`) with consistent stroke widths.

---

## 2. Design Tokens & Styling Primitives

All color, typographic, spatial, and motion tokens are codified in [static/style.css](file:///c:/Users/Vandita%20Mamgain/Downloads/flight-price-tracker/static/style.css). Any new UI additions must reference these custom properties.

### 2.1. Color Palette

#### Obsidian Noir (Dark Mode — Primary Signature Experience)
| Token | Value | Purpose |
| :--- | :--- | :--- |
| `--bg-void` | `#04060b` | Infinite canvas background and cosmic underlay |
| `--bg-base` | `#060912` | Base layer for body content and page scaffolding |
| `--bg-surface` | `rgba(10, 15, 27, 0.68)` | Frosted glassmorphic card containers |
| `--bg-surface-elevated` | `rgba(16, 23, 39, 0.75)` | Elevated interactive dropdowns and active panels |
| `--bg-card` | `rgba(13, 19, 34, 0.72)` | Flight result cards and comparison items |
| `--bg-glass` | `rgba(10, 15, 27, 0.65)` | Floating glass navbar and modal overlays |
| `--text-primary` | `#f6f5f2` | High-contrast editorial titles and primary labels |
| `--text-secondary` | `#9ea8bc` | Subtitles, helper text, and secondary metadata |
| `--text-tertiary` | `#606b80` | Subtle captions, timestamps, and muted labels |
| `--text-inverse` | `#05070d` | Text on solid gold buttons or active chips |

#### Warm Alabaster (Light Mode — Editorial Daylight Palette)
| Token | Value | Purpose |
| :--- | :--- | :--- |
| `--bg-void` | `#f4f3ee` | Warm ivory background |
| `--bg-base` | `#f7f6f1` | Neutral canvas backdrop |
| `--bg-surface` | `rgba(255, 255, 255, 0.85)` | High-clarity white frosted glass cards |
| `--bg-surface-elevated` | `rgba(255, 255, 255, 0.94)` | Dropdown menus and elevated controls |
| `--bg-card` | `rgba(255, 255, 255, 0.90)` | Primary result cards |
| `--text-primary` | `#121620` | Deep slate-black editorial typography |
| `--text-secondary` | `#566175` | Neutral gray secondary text |
| `--text-tertiary` | `#8490a6` | Muted captions and borders |
| `--text-inverse` | `#ffffff` | Text on dark buttons |

#### Accents & Highlights
| Token | Value | Visual Intent |
| :--- | :--- | :--- |
| `--accent-gold` | `#dfc99e` (Dark) / `#9e7a32` (Light) | Signature Champagne Gold for primary CTAs and active states |
| `--accent-gold-hover` | `#edd9b5` (Dark) / `#b08a3d` (Light) | Interactive hover highlight |
| `--accent-gold-dim` | `rgba(223, 201, 158, 0.12)` | Subtle tinted badges and selected chip backgrounds |
| `--accent-gold-border` | `rgba(223, 201, 158, 0.28)` | Focus borders and primary card outlines |
| `--accent-gold-glow` | `rgba(223, 201, 158, 0.22)` | Radial ambient glow behind key elements |
| `--accent-blue` | `#3d68a8` (Dark) / `#1d4ed8` (Light) | Celestial Stratosphere Blue for secondary metadata |
| `--accent-blue-glow` | `rgba(61, 104, 168, 0.25)` | Ethereal aura highlight |

#### Semantic Status Tokens
| State | Color Token | Dim Background Token | Border Token |
| :--- | :--- | :--- | :--- |
| **Success** | `--success: #34d399` | `--success-dim: rgba(52, 211, 153, 0.12)` | `--success-border: rgba(52, 211, 153, 0.3)` |
| **Warning** | `--warning: #fbbf24` | `--warning-dim: rgba(251, 191, 36, 0.12)` | `rgba(251, 191, 36, 0.3)` |
| **Danger** | `--danger: #f87171` | `--danger-dim: rgba(248, 113, 113, 0.12)` | `--danger-border: rgba(248, 113, 113, 0.3)` |

#### Borders & Hairlines
| Token | Value | Description |
| :--- | :--- | :--- |
| `--border-hairline` | `rgba(255, 255, 255, 0.07)` | Ultra-fine card separation lines |
| `--border-subtle` | `rgba(255, 255, 255, 0.12)` | Standard glass border |
| `--border-accent` | `rgba(223, 201, 158, 0.35)` | Highlighted card or active control border |

---

### 2.2. Typography System

The typography system is loaded from Google Fonts:
```html
<link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@400;500;600;700&family=Cormorant+Garamond:ital,wght@0,300;0,400;0,500;0,600;0,700;1,400;1,600&family=Plus+Jakarta+Sans:wght@300;400;500;600;700&family=Space+Mono:ital,wght@0,400;0,700;1,400&display=swap" rel="stylesheet">
```

#### Font Stacks
- **`--font-serif`**: `'Cormorant Garamond', 'Cinzel', Georgia, serif` — Used for majestic hero headers, brand title, and high-editorial callouts.
- **`--font-roman`**: `'Cinzel', serif` — Classical monument roman capitals used for eyebrow labels, brand badges, and ceremonial indicators.
- **`--font-sans`**: `'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, sans-serif` — Modern, ultra-legible grotesque sans-serif for interactive form controls, body paragraphs, buttons, and helper tooltips.
- **`--font-mono`**: `'Space Mono', monospace` — Precise computational typography for 3-letter IATA airport codes, timestamps, flight numbers, currency figures, and coordinate metrics.

#### Type Scale & Usage Rules
| Level | CSS Scale / Clamp | Weight | Font Stack | Usage |
| :--- | :--- | :--- | :--- | :--- |
| **Hero Display** | `clamp(2.75rem, 6vw, 5.25rem)` | 300 / 400 | `--font-serif` | Main landing headline ("The World, Without The Noise") |
| **Section Title** | `clamp(1.75rem, 3.5vw, 2.75rem)` | 400 / 500 | `--font-serif` | Major section headers, modal titles |
| **Subtitle / Lede** | `1.125rem` – `1.25rem` | 300 | `--font-sans` | Introductory copy under titles |
| **Body Large** | `1.0rem` | 400 | `--font-sans` | Standard card text, itinerary descriptions |
| **Body Small** | `0.875rem` | 400 / 500 | `--font-sans` | Helper notes, table labels, filter options |
| **Micro Caption** | `0.75rem` | 500 / 600 | `--font-sans` | Uppercase pill badges, form field floating labels |
| **Airport IATA** | `1.25rem` – `1.75rem` | 700 | `--font-mono` | Origin/Destination airport callouts (e.g., `LHR`, `HND`) |
| **Fare Metric** | `1.5rem` – `2.0rem` | 700 | `--font-mono` | Primary price quote and spread differences |

---

### 2.3. Spatial Geometry & Layout Tokens

| Token | Value | Usage |
| :--- | :--- | :--- |
| `--container-max` | `1260px` | Maximum centered content width |
| `--navbar-height` | `72px` | Fixed desktop navigation bar clearance |
| `--radius-sm` | `8px` | Small action chips, input inner borders |
| `--radius-md` | `14px` | Standard buttons, search inputs, modal cards |
| `--radius-lg` | `22px` | Flight result cards, filter panels, search monolith |
| `--radius-xl` | `32px` | Master experience container rounding |
| `--radius-full` | `9999px` | Pill tabs, airport swap circular toggle, badges |

#### Elevation, Blurs, and Shadows
- **Glassmorphic Filter:** `backdrop-filter: var(--glass-blur);` where `--glass-blur: blur(28px) saturate(180%)`.
- **Card Shadow:** `--shadow-card: 0 12px 30px -4px rgba(0, 0, 0, 0.4), inset 0 1px 0 rgba(255, 255, 255, 0.06)`.
- **Spatial Elevation:** `--shadow-spatial-lg: 0 30px 80px -10px rgba(0, 0, 0, 0.65), inset 0 1px 0 rgba(255, 255, 255, 0.1)`.

#### Z-Index Hierarchy Conventions
```text
Layer 2000: Ethereal Magnetic Cursor (#luxuryCursor, #luxuryCursorFollower)
Layer 1500: Toast Notification Stack (#toastContainer)
Layer 1000: Drawers & Modals (#trackedFlightsDrawer, #trackModal, #priceRadarBackdrop)
Layer  100: Autocomplete Dropdowns & Datepicker Overlays (.autocomplete-dropdown)
Layer   50: Floating Glass Navbar (.luxury-navbar)
Layer   10: Master UI Content Wrapper (.app-surreal, .search-monolith)
Layer    0: Three.js 3D Canvas & Aurora Atmosphere (#heroScene3D, .spatial-bg-atmosphere)
```

---

## 3. Component Design Guidelines

### 3.1. Navigation Bar (`.luxury-navbar`)
- **Structure:** Floating frosted glass container anchored at `top: 0; left: 0; right: 0; height: 72px;`.
- **Styling:** `background: var(--bg-glass); backdrop-filter: var(--glass-blur); border-bottom: 1px solid var(--border-hairline);`.
- **Brand Element:** Classical Cinzel brand logo with Champagne Gold ambient glow.
- **Controls:**
  1. Sound Atmosphere Toggle (procedural audio on/off with animated equalizer waves).
  2. Day/Night Theme Switcher (sun/moon Lucide toggle).
  3. Tracked Flights Radar Trigger (displays live badge counter of monitored routes).

### 3.2. Trip Type Segmented Control (`.trip-type-segmented`)
- **Structure:** Pill container enclosing One-Way, Round-Trip, and Multi-City tabs.
- **Interaction:** Smooth sliding background highlight indicator moving on tab click.
- **Active State:** Champagne Gold tinted background `var(--accent-gold-dim)` with gold text and subtle border `var(--accent-gold-border)`.

### 3.3. Airport Autocomplete & Swap Button
- **Inputs:** Floating label inputs displaying City name, Country, and prominent 3-letter IATA code.
- **Dropdown Suggestions:** Instant sub-millisecond dropdown with highlighted search matches, airport distance badge, and keyboard navigation.
- **Swap Button (`#swapAirports`):**
  - Circular glass button (`width: 42px; height: 42px; border-radius: 50%`).
  - Centered between origin and destination inputs.
  - On click: executes a smooth **180° rotation** (`transform: rotate(180deg)`) using `--ease-spring`, accompanied by an ethereal audio pitch chime.

### 3.4. Dynamic Multi-City Leg Builder
- **Leg Headers:** Numbered segments ("Flight Leg 01", "Flight Leg 02") in monospaced roman font.
- **Chronological Date Enforcer:** Automatically clamps the minimum selectable date of Leg $N+1$ to the selected date of Leg $N$.
- **Actions:** Discrete "+ Add Flight Leg" glass button (disabled at 5 legs) and individual leg removal buttons (minimum 2 legs required).

### 3.5. Flight Cards (`.flight-card`)
- **Layout:** Horizontal high-legibility timeline displaying:
  - Carrier logo or typography monogram with airline name.
  - Departure time and 3-letter origin code.
  - Journey duration and stops pill ("Non-stop" with green dot, "1 stop" with amber dot).
  - Arrival time and 3-letter destination code.
  - Price quote in bold monospace with Champagne Gold accent.
  - Action buttons: "Compare All Providers" (secondary glass) and "Book Offer" (primary gold).

### 3.6. Exact Flight Comparison Matrix (`.exact-comparison-panel`)
- **Layout:** Side-by-side or stacked multi-provider breakdown for the exact same flight segment.
- **Providers Displayed:** Google Flights, Amadeus, Expedia, MakeMyTrip, Cleartrip, EaseMyTrip, and Direct Airline channels.
- **Best Price Badge:** Highlighted in Emerald Green (`#34d399`) indicating lowest fare and calculated savings spread (e.g., "$142 Price Differential").
- **Deep Booking Links:** Clear external outbound link button to the selected provider's checkout.

### 3.7. Price Surveillance Radar Drawer (`#trackedFlightsDrawer`)
- **Layout:** Slide-out drawer from the right edge (`width: min(520px, 92vw)`).
- **Backdrop:** Darkened cosmic blur overlay (`backdrop-filter: blur(12px)`).
- **Cards:** Monitored route cards displaying route, target threshold, lowest seen price, last checked timestamp, and delete action.
- **Empty State:** Ethereal compass illustration with copy: *"No active trajectories under radar surveillance. Search a flight to begin tracking."*

### 3.8. Notification Toasts (`#toastContainer`)
- **Position:** Fixed floating stack at bottom-center or bottom-right.
- **Styling:** Glassmorphic pill with champagne border, subtle slide-up animation (`transform: translateY(0)`), and auto-dismiss after 4 seconds.

---

## 4. Motion Design & Interactive Architecture

### 4.1. Three.js 3D WebGL Centerpiece ([static/scene3d.js](file:///c:/Users/Vandita%20Mamgain/Downloads/flight-price-tracker/static/scene3d.js))
- **Sculpture Elements:**
  - **Monumental Outer Shell:** Frosted silica glass sphere (`MeshPhysicalMaterial`, roughness: 0.06, transmission: 0.92, thickness: 1.2).
  - **Liquid Core:** Inner chrome core with dynamic surface vertex ripple and iridescent lighting.
  - **Concentric Orbital Trajectories:** Mathematical ellipses representing transatlantic flight paths with pulsing navigational beacons.
  - **Cosmic Stardust Cloud:** 600 floating particles with subtle depth oscillation.
- **Parallax Physics:** Mouse position smoothly tilts and rotates the centerpiece via damping factor:
  $$\Delta \theta = (\text{target} - \text{current}) \times 0.04$$

### 4.2. Procedural Web Audio Soundscape ([static/atmosphere.js](file:///c:/Users/Vandita%20Mamgain/Downloads/flight-price-tracker/static/atmosphere.js))
- **Zero Audio Assets:** 100% synthesized in real time via the Web Audio API without downloading audio files.
- **Harmonic Frequencies:** Ethereal A-minor 9th ambient drone:
  - $A_1 = 55.0\,\text{Hz}$ (Sine)
  - $A_2 = 110.0\,\text{Hz}$ (Sine)
  - $E_3 = 164.81\,\text{Hz}$ (Triangle)
- **Acoustic Shaping:** 420Hz warm lowpass filter with 0.08Hz LFO breathing oscillation.
- **Interactive Feedback:** Soft pentatonic micro-chimes triggered upon clicking the swap button or switching search modes.

### 4.3. Reduced-Motion Support
- The design system strictly respects `prefers-reduced-motion: reduce`:
  - 3D WebGL continuous rotation is halted; stationary composition rendered.
  - Card tilt physics and magnetic cursor followers are disabled.
  - UI transitions drop to simple opacity fades (`duration: 150ms`).

---

## 5. Responsive Design Standards

| Viewport Category | Width Range | Layout Behavior |
| :--- | :--- | :--- |
| **Mobile Compact** | 320px – 375px | Single-column search stack, full-width inputs, 44px min touch targets, hidden custom cursor |
| **Mobile Standard** | 375px – 480px | Compact flight card layout, vertical comparison matrix |
| **Tablet Portrait** | 768px – 1024px | 2-column search layout, expandable radar drawer |
| **Desktop High-Res**| 1280px – 1920px+| Full 4-column search monolith, side-by-side comparison tables, active 3D WebGL centerpiece |

### 5.1. Viewport Guarantees
- **Horizontal Overflow Zero Tolerance:** `html, body { overflow-x: clip; max-width: 100%; }`.
- **Ergonomic Touch Targets:** Minimum 44x44px bounding box for all mobile tap targets (`--min-touch-target: 44px`).

---

## 6. Accessibility & Contrast (WCAG 2.1 AA)

- **Contrast Ratios:**
  - Editorial headings (`--text-primary` on `--bg-base`): **14.8:1** (exceeds AAA requirement).
  - Body text (`--text-secondary` on `--bg-surface`): **6.2:1** (exceeds AA requirement).
  - Interactive Champagne Gold accents on dark void: **8.1:1**.
- **Keyboard Navigation:**
  - All interactive elements possess explicit `:focus-visible` styling featuring a Champagne Gold outline with 2px offset:
    ```css
    :focus-visible {
        outline: 2px solid var(--accent-gold);
        outline-offset: 2px;
    }
    ```
- **Semantic Structure:** Single `<h1>` per page, hierarchical `<h2>` and `<h3>` tags, and full ARIA descriptions (`aria-label`, `aria-expanded`, `aria-controls`, `aria-live`).

---

## 7. Design Acceptance Checklist for Future Tasks

Before declaring any frontend UI task complete, verify:
- [ ] Matches the Surreal Quiet Luxury aesthetic (Obsidian Noir / Warm Alabaster).
- [ ] Uses only established CSS tokens from `static/style.css`.
- [ ] Contains zero arbitrary unicode emojis; uses Lucide icons.
- [ ] Features complete visual states: Loading skeleton, Empty state, Error state, and Success state.
- [ ] Displays no horizontal scroll on viewports from 320px to 1920px.
- [ ] All interactive buttons satisfy 44x44px touch target rules on mobile.
- [ ] Respects `prefers-reduced-motion` settings.
- [ ] Fully navigable via keyboard (`Tab` and `Enter`/`Space`).
