/**
 * SKYTRACKER — MINIMALIST SURREAL 3D WEBGL AMBIENT SCENE
 * Luxury architectural centerpiece engineered as a non-obtrusive, decorative background layer.
 * Strictly background layer (pointer-events: none), responsive scaling & positioning per page.
 */

(function () {
    'use strict';

    // 1. Verify Three.js availability
    if (typeof THREE === 'undefined') {
        console.info('Three.js not loaded. Skipping 3D initialization.');
        return;
    }

    const canvas = document.getElementById('heroScene3D');
    if (!canvas) return;

    // Strict pointer-events safety
    canvas.style.pointerEvents = 'none';

    // 2. Page & Media Queries Detection
    const currentPath = (window.location.pathname || '').toLowerCase();
    const isHome = (currentPath === '/' || currentPath === '/index' || currentPath === '/index.html' || currentPath === '');
    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // 3. SCENE, CAMERA, RENDERER
    const scene = new THREE.Scene();
    scene.fog = new THREE.FogExp2(0x04060b, 0.022);

    const camera = new THREE.PerspectiveCamera(
        38,
        window.innerWidth / window.innerHeight,
        0.1,
        1000
    );
    camera.position.set(0, 0, 11);

    const renderer = new THREE.WebGLRenderer({
        canvas: canvas,
        alpha: true,
        antialias: true,
        powerPreference: 'high-performance'
    });

    const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
    renderer.setPixelRatio(pixelRatio);
    renderer.setSize(window.innerWidth, window.innerHeight);
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.0;

    // 4. MASTER CENTERPIECE GROUP
    const sculptureGroup = new THREE.Group();
    scene.add(sculptureGroup);

    // Dynamic layout adaptation helper
    function getResponsiveConfig() {
        const width = window.innerWidth;
        const isMobile = width < 768;
        const isTablet = width >= 768 && width < 1100;

        if (isHome) {
            if (isMobile) {
                return { x: 0.0, y: -2.8, z: -1.5, scale: 0.45, opacityMult: 0.5 };
            } else if (isTablet) {
                return { x: 1.2, y: 0.2, z: -0.5, scale: 0.65, opacityMult: 0.75 };
            } else {
                return { x: 2.3, y: 0.2, z: 0.0, scale: 0.82, opacityMult: 0.85 };
            }
        } else {
            // Functional pages: Flights, Compare, Tracking, Contact, etc.
            // Move away from central forms/tables, reduce scale and opacity
            if (isMobile) {
                return { x: 0.0, y: -4.2, z: -3.0, scale: 0.30, opacityMult: 0.35 };
            } else if (isTablet) {
                return { x: 3.2, y: 1.6, z: -2.0, scale: 0.42, opacityMult: 0.45 };
            } else {
                return { x: 4.2, y: 1.2, z: -1.5, scale: 0.50, opacityMult: 0.55 };
            }
        }
    }

    const initialConfig = getResponsiveConfig();
    sculptureGroup.position.set(initialConfig.x, initialConfig.y, initialConfig.z);
    sculptureGroup.scale.set(initialConfig.scale, initialConfig.scale, initialConfig.scale);

    // 5. TRANSLUCENT FROSTED SPHERE (OUTER SHELL)
    const outerRadius = 2.4;
    const outerGeometry = new THREE.SphereGeometry(outerRadius, 48, 48);
    const glassMaterial = new THREE.MeshPhysicalMaterial({
        color: 0x98b8e8,
        emissive: 0x050a16,
        emissiveIntensity: 0.15,
        roughness: isHome ? 0.12 : 0.28,
        metalness: 0.1,
        transmission: isHome ? 0.88 : 0.95,
        opacity: isHome ? 0.60 : 0.25,
        transparent: true,
        reflectivity: 0.8,
        clearcoat: 0.8,
        clearcoatRoughness: 0.08,
        ior: 1.48
    });
    const outerSphere = new THREE.Mesh(outerGeometry, glassMaterial);
    sculptureGroup.add(outerSphere);

    // 6. INNER METALLIC CORE (FLUID LIQUID CHROME / CHAMPAGNE)
    const innerGeometry = new THREE.TorusKnotGeometry(1.05, 0.32, 120, 28, 2, 3);
    const chromeMaterial = new THREE.MeshStandardMaterial({
        color: 0xeae2d2, // Champagne platinum
        metalness: 0.96,
        roughness: 0.14,
        transparent: true,
        opacity: isHome ? 0.85 : 0.40
    });
    const innerCore = new THREE.Mesh(innerGeometry, chromeMaterial);
    sculptureGroup.add(innerCore);

    // Subtle celestial gyroscope wireframe
    const gyroGeometry = new THREE.IcosahedronGeometry(1.7, 1);
    const gyroMaterial = new THREE.MeshBasicMaterial({
        color: 0xdfc99e,
        wireframe: true,
        transparent: true,
        opacity: isHome ? 0.14 : 0.06
    });
    const gyroMesh = new THREE.Mesh(gyroGeometry, gyroMaterial);
    sculptureGroup.add(gyroMesh);

    // 7. RESTRAINED ORBITAL FLIGHT TRAJECTORY RINGS (MAX 1-2 DELIBERATE THIN RINGS)
    const orbitalGroup = new THREE.Group();
    sculptureGroup.add(orbitalGroup);

    function createOrbitalRing(radius, tube, tiltX, tiltY, tiltZ, colorHex, opacityVal) {
        const ringGeo = new THREE.TorusGeometry(radius, tube, 16, 120);
        const ringMat = new THREE.MeshStandardMaterial({
            color: colorHex,
            metalness: 0.9,
            roughness: 0.25,
            transparent: true,
            opacity: opacityVal
        });
        const ring = new THREE.Mesh(ringGeo, ringMat);
        ring.rotation.set(tiltX, tiltY, tiltZ);
        orbitalGroup.add(ring);
        return ring;
    }

    // Single deliberate primary equatorial gold trajectory
    const ring1 = createOrbitalRing(
        3.2,
        0.007,
        Math.PI * 0.16,
        Math.PI * 0.10,
        0,
        0xdfc99e,
        isHome ? 0.55 : 0.30
    );

    // Secondary subtle polar ring on homepage only
    let ring2 = null;
    if (isHome) {
        ring2 = createOrbitalRing(
            3.7,
            0.005,
            -Math.PI * 0.30,
            Math.PI * 0.20,
            Math.PI * 0.08,
            0xb0c4de,
            0.35
        );
    }

    // 8. LUMINOUS ORBITAL FLIGHT NODES (BEACONS)
    const beacons = [];
    function createFlightBeacon(ringRadius, speed, colorHex, initialAngle, tiltEuler) {
        const beaconGeo = new THREE.SphereGeometry(0.045, 12, 12);
        const beaconMat = new THREE.MeshBasicMaterial({ color: colorHex });
        const mesh = new THREE.Mesh(beaconGeo, beaconMat);

        const light = new THREE.PointLight(colorHex, isHome ? 0.8 : 0.3, 2.0);
        mesh.add(light);
        scene.add(mesh);

        return {
            mesh,
            radius: ringRadius,
            speed,
            angle: initialAngle,
            tilt: tiltEuler
        };
    }

    beacons.push(createFlightBeacon(3.2, 0.012, 0xffebc2, 0, ring1.rotation));
    if (ring2) {
        beacons.push(createFlightBeacon(3.7, -0.009, 0xaad4ff, Math.PI * 0.7, ring2.rotation));
    }

    // 9. ATMOSPHERIC VOLUMETRIC STARDUST PARTICLES (SUBTLE & RESTRAINED)
    const particleCount = isHome ? 280 : 120;
    const particlePositions = new Float32Array(particleCount * 3);

    for (let i = 0; i < particleCount; i++) {
        const u = Math.random();
        const v = Math.random();
        const theta = u * 2.0 * Math.PI;
        const phi = Math.acos(2.0 * v - 1.0);
        const r = 5.0 + Math.random() * 20.0;
        const sinPhi = Math.sin(phi);

        particlePositions[i * 3] = r * sinPhi * Math.cos(theta);
        particlePositions[i * 3 + 1] = r * sinPhi * Math.sin(theta);
        particlePositions[i * 3 + 2] = r * Math.cos(phi) - 2;
    }

    const particleGeometry = new THREE.BufferGeometry();
    particleGeometry.setAttribute('position', new THREE.BufferAttribute(particlePositions, 3));

    function createCircleTexture() {
        const c = document.createElement('canvas');
        c.width = 32;
        c.height = 32;
        const ctx = c.getContext('2d');
        const grad = ctx.createRadialGradient(16, 16, 0, 16, 16, 15);
        grad.addColorStop(0, 'rgba(255, 255, 255, 0.9)');
        grad.addColorStop(0.4, 'rgba(223, 201, 158, 0.5)');
        grad.addColorStop(1, 'rgba(0, 0, 0, 0)');
        ctx.fillStyle = grad;
        ctx.beginPath();
        ctx.arc(16, 16, 15, 0, Math.PI * 2);
        ctx.fill();
        return new THREE.CanvasTexture(c);
    }

    const particleMaterial = new THREE.PointsMaterial({
        size: 0.14,
        map: createCircleTexture(),
        transparent: true,
        opacity: isHome ? 0.35 : 0.18,
        blending: THREE.AdditiveBlending,
        depthWrite: false
    });

    const particles = new THREE.Points(particleGeometry, particleMaterial);
    scene.add(particles);

    // 10. REFINED STUDIO LIGHTING (RESTRAINED & SOFT)
    const ambientLight = new THREE.AmbientLight(0x0c1220, isHome ? 1.2 : 0.8);
    scene.add(ambientLight);

    const keyLight = new THREE.DirectionalLight(0xffeedb, isHome ? 1.8 : 1.0);
    keyLight.position.set(6, 8, 7);
    scene.add(keyLight);

    const rimLight = new THREE.DirectionalLight(0x406ab3, isHome ? 2.0 : 0.9);
    rimLight.position.set(-7, -5, -6);
    scene.add(rimLight);

    const corePointLight = new THREE.PointLight(0xe8d098, isHome ? 1.2 : 0.4, 4.5);
    innerCore.add(corePointLight);

    // 11. PASSIVE CURSOR INTERPOLATION & RESPONSIVE RESIZE
    let targetRotationX = 0;
    let targetRotationY = 0;
    let targetCamX = 0;
    let targetCamY = 0;

    window.addEventListener('mousemove', (e) => {
        if (prefersReducedMotion) return;
        const normX = (e.clientX / window.innerWidth) * 2 - 1;
        const normY = -(e.clientY / window.innerHeight) * 2 + 1;
        targetRotationY = normX * 0.28;
        targetRotationX = -normY * 0.20;
        targetCamX = normX * 0.45;
        targetCamY = normY * 0.30;
    }, { passive: true });

    function onWindowResize() {
        camera.aspect = window.innerWidth / window.innerHeight;
        camera.updateProjectionMatrix();
        renderer.setSize(window.innerWidth, window.innerHeight);

        const cfg = getResponsiveConfig();
        sculptureGroup.position.set(cfg.x, cfg.y, cfg.z);
        sculptureGroup.scale.set(cfg.scale, cfg.scale, cfg.scale);
    }
    window.addEventListener('resize', onWindowResize, { passive: true });

    // 12. TAB VISIBILITY MANAGEMENT (PAUSE LOOP WHEN HIDDEN)
    let isTabVisible = true;
    document.addEventListener('visibilitychange', () => {
        isTabVisible = !document.hidden;
    });

    // 13. MASTER ANIMATION LOOP
    let clock = new THREE.Clock();

    function animate() {
        requestAnimationFrame(animate);

        if (!isTabVisible) return;

        const delta = clock.getDelta();
        const time = clock.getElapsedTime();

        if (!prefersReducedMotion) {
            innerCore.rotation.x += 0.0022;
            innerCore.rotation.y += 0.0035;
            innerCore.rotation.z += 0.0015;

            gyroMesh.rotation.x -= 0.0008;
            gyroMesh.rotation.y += 0.0012;

            ring1.rotation.z += 0.0010;
            if (ring2) ring2.rotation.z -= 0.0008;

            // Gentle breathing oscillation
            const breath = Math.sin(time * 0.6) * 0.08;
            const basePos = getResponsiveConfig();
            sculptureGroup.position.y = basePos.y + breath;

            // Smooth rotation damping
            sculptureGroup.rotation.y += (targetRotationY - sculptureGroup.rotation.y) * 0.03;
            sculptureGroup.rotation.x += (targetRotationX - sculptureGroup.rotation.x) * 0.03;

            // Camera subtle parallax
            camera.position.x += (targetCamX - camera.position.x) * 0.025;
            camera.position.y += (targetCamY - camera.position.y) * 0.025;

            // Update flight beacons
            beacons.forEach(b => {
                b.angle += b.speed;
                const pos = new THREE.Vector3(
                    Math.cos(b.angle) * b.radius,
                    Math.sin(b.angle) * b.radius,
                    0
                );
                pos.applyEuler(b.tilt);
                pos.add(sculptureGroup.position);
                b.mesh.position.copy(pos);
            });

            // Particles slow drift
            particles.rotation.y = time * 0.008;
        }

        // Scroll responsive camera choreography
        const scrollFactor = window.scrollY / (window.innerHeight || 800);
        const targetZ = 11 + Math.min(scrollFactor * 3.0, 6.0);
        camera.position.z += (targetZ - camera.position.z) * 0.04;

        renderer.render(scene, camera);
    }

    animate();
})();
