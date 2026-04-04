// Waves — interactive SVG line background
// Vanilla JS port — chrome & purple edition

(function() {
  function createNoise2D() {
    const perm = new Uint8Array(512);
    const grad = [[1,1],[-1,1],[1,-1],[-1,-1],[1,0],[-1,0],[0,1],[0,-1]];
    for (let i = 0; i < 256; i++) perm[i] = i;
    for (let i = 255; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [perm[i], perm[j]] = [perm[j], perm[i]];
    }
    for (let i = 0; i < 256; i++) perm[256 + i] = perm[i];
    function dot(g, x, y) { return g[0] * x + g[1] * y; }
    function fade(t) { return t * t * t * (t * (t * 6 - 15) + 10); }
    function lerp(a, b, t) { return a + t * (b - a); }
    return function(x, y) {
      const X = Math.floor(x) & 255, Y = Math.floor(y) & 255;
      const xf = x - Math.floor(x), yf = y - Math.floor(y);
      const u = fade(xf), v = fade(yf);
      const g00 = grad[perm[perm[X] + Y] & 7];
      const g10 = grad[perm[perm[X + 1] + Y] & 7];
      const g01 = grad[perm[perm[X] + Y + 1] & 7];
      const g11 = grad[perm[perm[X + 1] + Y + 1] & 7];
      return lerp(
        lerp(dot(g00, xf, yf), dot(g10, xf - 1, yf), u),
        lerp(dot(g01, xf, yf - 1), dot(g11, xf - 1, yf - 1), u), v
      );
    };
  }

  function initWaves(container, options) {
    // Chrome + purple color palette — subtle/thin
    const colors = options.colors || [
      'rgba(167, 139, 250, 0.14)',
      'rgba(196, 181, 253, 0.08)',
      'rgba(200, 200, 210, 0.10)',
      'rgba(139, 92, 246, 0.12)',
      'rgba(220, 220, 230, 0.06)',
      'rgba(167, 139, 250, 0.11)',
      'rgba(180, 180, 195, 0.08)',
      'rgba(124, 58, 237, 0.10)',
    ];
    const strokeWidth = options.strokeWidth || '0.8';

    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.style.cssText = 'display:block;width:100%;height:100%;position:absolute;top:0;left:0;pointer-events:none;';
    container.style.position = container.style.position || 'relative';
    container.style.overflow = 'hidden';
    container.style.pointerEvents = 'none';
    container.appendChild(svg);

    // Pointer dot
    const dot = document.createElement('div');
    dot.style.cssText = 'position:absolute;top:0;left:0;width:0.5rem;height:0.5rem;background:rgba(167,139,250,0.6);border-radius:50%;transform:translate3d(calc(var(--wx,-100px) - 50%),calc(var(--wy,-100px) - 50%),0);will-change:transform;pointer-events:none;filter:blur(1px);';
    container.appendChild(dot);

    const noise = createNoise2D();
    const mouse = { x: -200, y: -200, sx: -200, sy: -200, lx: -200, ly: -200, v: 0, vs: 0, a: 0, set: false };
    let paths = [], lines = [], bounding = null, raf = null;

    function setSize() {
      bounding = container.getBoundingClientRect();
      svg.setAttribute('width', bounding.width);
      svg.setAttribute('height', bounding.height);
      svg.setAttribute('viewBox', `0 0 ${bounding.width} ${bounding.height}`);
    }

    function setLines() {
      lines = [];
      paths.forEach(p => p.remove());
      paths = [];
      if (!bounding) return;

      const xGap = 8, yGap = 8;
      const w = bounding.width + 200, h = bounding.height + 30;
      const totalLines = Math.ceil(w / xGap);
      const totalPoints = Math.ceil(h / yGap);
      const xStart = (bounding.width - xGap * totalLines) / 2;
      const yStart = (bounding.height - yGap * totalPoints) / 2;

      for (let i = 0; i < totalLines; i++) {
        const points = [];
        for (let j = 0; j < totalPoints; j++) {
          points.push({
            x: xStart + xGap * i,
            y: yStart + yGap * j,
            wave: { x: 0, y: 0 },
            cursor: { x: 0, y: 0, vx: 0, vy: 0 }
          });
        }
        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        path.setAttribute('fill', 'none');
        path.setAttribute('stroke', colors[i % colors.length]);
        path.setAttribute('stroke-width', strokeWidth);
        svg.appendChild(path);
        paths.push(path);
        lines.push(points);
      }
    }

    function movePoints(time) {
      lines.forEach(points => {
        points.forEach(p => {
          const move = noise((p.x + time * 0.008) * 0.003, (p.y + time * 0.003) * 0.002) * 8;
          p.wave.x = Math.cos(move) * 12;
          p.wave.y = Math.sin(move) * 6;

          const dx = p.x - mouse.sx, dy = p.y - mouse.sy;
          const d = Math.hypot(dx, dy);
          const l = Math.max(175, mouse.vs);
          if (d < l) {
            const s = 1 - d / l;
            const f = Math.cos(d * 0.001) * s;
            p.cursor.vx += Math.cos(mouse.a) * f * l * mouse.vs * 0.00035;
            p.cursor.vy += Math.sin(mouse.a) * f * l * mouse.vs * 0.00035;
          }
          p.cursor.vx += (0 - p.cursor.x) * 0.01;
          p.cursor.vy += (0 - p.cursor.y) * 0.01;
          p.cursor.vx *= 0.95;
          p.cursor.vy *= 0.95;
          p.cursor.x = Math.min(50, Math.max(-50, p.cursor.x + p.cursor.vx));
          p.cursor.y = Math.min(50, Math.max(-50, p.cursor.y + p.cursor.vy));
        });
      });
    }

    function drawLines() {
      lines.forEach((points, i) => {
        if (points.length < 2 || !paths[i]) return;
        const p0 = points[0];
        let d = `M ${p0.x + p0.wave.x} ${p0.y + p0.wave.y}`;
        for (let j = 1; j < points.length; j++) {
          const p = points[j];
          d += `L ${p.x + p.wave.x + p.cursor.x} ${p.y + p.wave.y + p.cursor.y}`;
        }
        paths[i].setAttribute('d', d);
      });
    }

    function tick(time) {
      mouse.sx += (mouse.x - mouse.sx) * 0.1;
      mouse.sy += (mouse.y - mouse.sy) * 0.1;
      const dx = mouse.x - mouse.lx, dy = mouse.y - mouse.ly;
      mouse.v = Math.hypot(dx, dy);
      mouse.vs += (mouse.v - mouse.vs) * 0.1;
      mouse.vs = Math.min(100, mouse.vs);
      mouse.lx = mouse.x;
      mouse.ly = mouse.y;
      mouse.a = Math.atan2(dy, dx);

      container.style.setProperty('--wx', mouse.sx + 'px');
      container.style.setProperty('--wy', mouse.sy + 'px');

      movePoints(time);
      drawLines();
      raf = requestAnimationFrame(tick);
    }

    function onMouseMove(e) {
      if (!bounding) return;
      // For fixed containers, use clientX/Y directly (no scroll offset)
      const isFixed = getComputedStyle(container).position === 'fixed';
      if (isFixed) {
        mouse.x = e.clientX;
        mouse.y = e.clientY;
      } else {
        mouse.x = e.pageX - bounding.left;
        mouse.y = e.pageY - bounding.top + window.scrollY;
      }
      if (!mouse.set) { mouse.sx = mouse.x; mouse.sy = mouse.y; mouse.lx = mouse.x; mouse.ly = mouse.y; mouse.set = true; }
    }

    function onTouchMove(e) {
      e.preventDefault();
      const t = e.touches[0];
      if (!bounding) return;
      const isFixed = getComputedStyle(container).position === 'fixed';
      if (isFixed) {
        mouse.x = t.clientX;
        mouse.y = t.clientY;
      } else {
        mouse.x = t.clientX - bounding.left;
        mouse.y = t.clientY - bounding.top + window.scrollY;
      }
      if (!mouse.set) { mouse.sx = mouse.x; mouse.sy = mouse.y; mouse.lx = mouse.x; mouse.ly = mouse.y; mouse.set = true; }
    }

    function onResize() { setSize(); setLines(); }

    setSize();
    setLines();
    window.addEventListener('resize', onResize);
    window.addEventListener('mousemove', onMouseMove);
    container.addEventListener('touchmove', onTouchMove, { passive: false });
    raf = requestAnimationFrame(tick);

    // Pause when off-screen
    const obs = new IntersectionObserver(([e]) => {
      if (e.isIntersecting) { if (!raf) raf = requestAnimationFrame(tick); }
      else { if (raf) { cancelAnimationFrame(raf); raf = null; } }
    }, { threshold: 0.05 });
    obs.observe(container);

    return () => {
      if (raf) cancelAnimationFrame(raf);
      window.removeEventListener('resize', onResize);
      window.removeEventListener('mousemove', onMouseMove);
      container.removeEventListener('touchmove', onTouchMove);
      obs.disconnect();
    };
  }

  // Auto-init all elements with data-waves
  document.querySelectorAll('[data-waves]').forEach(el => {
    initWaves(el, {
      strokeWidth: el.dataset.wavesStroke || '1.5'
    });
  });

  window.initWaves = initWaves;
})();
