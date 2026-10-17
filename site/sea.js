// The hero: a lifebuoy riding a faceted night sea, a searchlight sweeping the water.
// setStatus("passed" | "failed"): a failed drill turns the flare red and lets the buoy ride lower.

import * as THREE from "./vendor/three.module.min.js";

const SEA = 0x15646e, SKY = 0x05181d, YELLOW = 0xffd23f, FOAM = 0xe9f3ef, CORAL = 0xff6f59;

export function createSea(canvas, { reducedMotion = false } = {}) {
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  } catch {
    return null;
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(SKY);
  scene.fog = new THREE.FogExp2(SKY, 0.045);
  const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 120);
  const camBase = new THREE.Vector3(0, 3.1, 9.5);

  // ---------------------------------------------------------------- water (faceted, CPU waves)
  const W = 90, SEG = 110;
  const waterGeo = new THREE.PlaneGeometry(W, W, SEG, SEG);
  waterGeo.rotateX(-Math.PI / 2);
  const base = waterGeo.attributes.position.array.slice();
  const water = new THREE.Mesh(waterGeo, new THREE.MeshStandardMaterial({ color: SEA, roughness: 0.22, metalness: 0.25, flatShading: true }));
  scene.add(water);

  const amp = reducedMotion ? 0.35 : 1;
  function height(x, z, t) {
    return amp * (
      0.32 * Math.sin(x * 0.35 + t * 0.9) +
      0.22 * Math.sin(z * 0.42 - t * 1.1 + x * 0.12) +
      0.12 * Math.sin((x + z) * 0.9 + t * 1.7) +
      0.05 * Math.sin(x * 2.1 - z * 1.7 + t * 2.4));
  }

  // ---------------------------------------------------------------- the lifebuoy
  const buoy = new THREE.Group();
  scene.add(buoy);
  const ringGeo = new THREE.TorusGeometry(1.15, 0.38, 32, 96);
  const colors = [];
  const pos = ringGeo.attributes.position;
  const yellow = new THREE.Color(YELLOW), foam = new THREE.Color(FOAM);
  for (let i = 0; i < pos.count; i++) {
    const a = Math.atan2(pos.getY(i), pos.getX(i));
    const seg = Math.floor(((a + Math.PI) / (Math.PI * 2)) * 8);
    const c = seg % 2 ? foam : yellow;
    colors.push(c.r, c.g, c.b);
  }
  ringGeo.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
  const ring = new THREE.Mesh(ringGeo, new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.55 }));
  ring.rotation.x = -Math.PI / 2;
  buoy.add(ring);
  // grab line around the outside, held by four bands
  const ropeMat = new THREE.MeshStandardMaterial({ color: 0xd8d2c0, roughness: 0.9 });
  const rope = new THREE.Mesh(new THREE.TorusGeometry(1.58, 0.035, 8, 120), ropeMat);
  rope.rotation.x = -Math.PI / 2;
  buoy.add(rope);
  for (let i = 0; i < 4; i++) {
    const a = (i / 4) * Math.PI * 2 + Math.PI / 8;
    const band = new THREE.Mesh(new THREE.TorusGeometry(0.4, 0.04, 8, 32), ropeMat);
    band.position.set(Math.cos(a) * 1.15, 0, Math.sin(a) * 1.15);
    band.rotation.y = -a;
    buoy.add(band);
  }
  // a small strobe on the buoy
  const strobe = new THREE.Mesh(new THREE.CylinderGeometry(0.09, 0.11, 0.3, 16), new THREE.MeshBasicMaterial({ color: YELLOW }));
  strobe.position.set(1.15, 0.45, 0);
  buoy.add(strobe);
  const flare = new THREE.PointLight(YELLOW, 6, 9, 2);
  flare.position.set(1.15, 0.9, 0);
  buoy.add(flare);

  // ---------------------------------------------------------------- light
  scene.add(new THREE.HemisphereLight(0x7fb3bb, 0x02080a, 1.4));
  const moon = new THREE.DirectionalLight(0xcfe8ff, 2.2);
  moon.position.set(-6, 9, -4);
  scene.add(moon);
  // soft fill from the viewer, so the buoy's colours read at night
  const fill = new THREE.DirectionalLight(0xfff2d6, 1.3);
  fill.position.set(2, 4, 10);
  scene.add(fill);
  const search = new THREE.SpotLight(0xfff1c2, 90, 60, 0.22, 0.6, 1.2);
  search.position.set(-14, 9, -10);
  scene.add(search);
  scene.add(search.target);

  // a few stars
  const starGeo = new THREE.BufferGeometry();
  const stars = [];
  for (let i = 0; i < 240; i++) stars.push((Math.random() - 0.5) * 120, 8 + Math.random() * 30, -30 - Math.random() * 40);
  starGeo.setAttribute("position", new THREE.Float32BufferAttribute(stars, 3));
  scene.add(new THREE.Points(starGeo, new THREE.PointsMaterial({ color: 0xe9f3ef, size: 0.12, sizeAttenuation: true, fog: false })));

  // ---------------------------------------------------------------- loop
  let status = "passed", px = 0, py = 0, aspect = 1.6;
  window.addEventListener("pointermove", (e) => { px = e.clientX / innerWidth - 0.5; py = e.clientY / innerHeight - 0.5; });

  function resize() {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    aspect = w / h;
    camera.fov = aspect > 1.15 ? 38 : 64; // tall phone screens get a wider lens
    camera.updateProjectionMatrix();
  }

  let raf = 0, visible = true;
  const start = performance.now();
  function frame(now) {
    raf = requestAnimationFrame(frame);
    if (!visible || document.hidden) return;
    const t = reducedMotion ? 4 + (now - start) / 6000 : (now - start) / 1000;

    const a = water.geometry.attributes.position.array;
    for (let i = 0; i < a.length; i += 3) a[i + 1] = height(base[i], base[i + 2], t);
    water.geometry.attributes.position.needsUpdate = true;

    // the buoy follows the surface and tilts with it
    // right third on wide screens, centred on phones (the text sits below there)
    const bx = aspect > 1.15 ? 4.2 : 0.6, bz = aspect > 1.15 ? -0.5 : -5;
    const y0 = height(bx, bz, t);
    const dx = height(bx + 0.6, bz, t) - height(bx - 0.6, bz, t);
    const dz = height(bx, bz + 0.6, t) - height(bx, bz - 0.6, t);
    buoy.position.set(bx, y0 + (status === "failed" ? -0.1 : 0.04), bz);
    buoy.rotation.set(dz * 0.35, t * 0.08, -dx * 0.35);

    const blink = (Math.floor(t * 1.4) % 2 === 0) ? 1 : 0.15;
    strobe.material.color.setHex(status === "failed" ? CORAL : YELLOW);
    flare.color.setHex(status === "failed" ? CORAL : YELLOW);
    flare.intensity = (status === "failed" ? 9 : 5) * blink;

    search.target.position.set(Math.sin(t * 0.25) * 9 + 1, 0, Math.cos(t * 0.25) * 3);

    camera.position.set(camBase.x + px * 1.2, camBase.y - py * 0.6 + Math.sin(t * 0.6) * 0.05, camBase.z);
    camera.lookAt(aspect > 1.15 ? 1.2 : 0.4, aspect > 1.15 ? 0 : -3.2, 0);
    renderer.render(scene, camera);
  }

  const ro = new ResizeObserver(resize);
  ro.observe(canvas);
  const io = new IntersectionObserver(([e]) => { visible = e.isIntersecting; });
  io.observe(canvas);
  resize();
  raf = requestAnimationFrame(frame);

  return {
    setStatus(s) { status = s; },
    dispose() { cancelAnimationFrame(raf); ro.disconnect(); io.disconnect(); renderer.dispose(); },
  };
}
