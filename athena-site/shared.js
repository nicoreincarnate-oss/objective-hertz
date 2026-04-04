// ===== SHARED JS — Athena Studios =====

// Floating Navbar (hide on scroll down, show on scroll up)
let lastScroll = 0;
const navbar = document.getElementById('navbar');
if (navbar) {
  window.addEventListener('scroll', () => {
    const y = window.scrollY;
    navbar.classList.toggle('hidden', y > lastScroll && y > 100);
    navbar.classList.toggle('scrolled', y > 50);
    lastScroll = y;
  });
}

// Mobile nav toggle
const navToggle = document.getElementById('navToggle');
const navLinks = document.getElementById('navLinks');
if (navToggle && navLinks) {
  navToggle.addEventListener('click', () => navLinks.classList.toggle('open'));
  navLinks.querySelectorAll('a').forEach(a =>
    a.addEventListener('click', () => navLinks.classList.remove('open'))
  );
}

// Custom Cursor
const cursor = document.getElementById('cursor');
if (cursor && window.matchMedia('(hover: hover)').matches) {
  document.addEventListener('mousemove', (e) => {
    cursor.style.left = e.clientX + 'px';
    cursor.style.top = e.clientY + 'px';
    if (!cursor.classList.contains('active')) cursor.classList.add('active');
  });
  document.querySelectorAll('a, button, [data-tilt]').forEach(el => {
    el.addEventListener('mouseenter', () => cursor.classList.add('hovering'));
    el.addEventListener('mouseleave', () => cursor.classList.remove('hovering'));
  });
}

// 3D Card Tilt + Spotlight
document.querySelectorAll('[data-tilt]').forEach(card => {
  card.addEventListener('mousemove', (e) => {
    const rect = card.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    const cx = rect.width / 2;
    const cy = rect.height / 2;
    card.style.setProperty('--rx', ((y - cy) / cy * -4) + 'deg');
    card.style.setProperty('--ry', ((x - cx) / cx * 4) + 'deg');
    card.style.setProperty('--cx', x + 'px');
    card.style.setProperty('--cy', y + 'px');
  });
  card.addEventListener('mouseleave', () => {
    card.style.setProperty('--rx', '0deg');
    card.style.setProperty('--ry', '0deg');
  });
});

// Button Ripple
document.querySelectorAll('[data-ripple]').forEach(btn => {
  btn.addEventListener('click', function(e) {
    const rect = this.getBoundingClientRect();
    const ripple = document.createElement('span');
    ripple.classList.add('ripple');
    ripple.style.left = (e.clientX - rect.left) + 'px';
    ripple.style.top = (e.clientY - rect.top) + 'px';
    this.appendChild(ripple);
    setTimeout(() => ripple.remove(), 600);
  });
});

// Scroll Reveal
const revealObserver = new IntersectionObserver((entries) => {
  entries.forEach(entry => {
    if (entry.isIntersecting) entry.target.classList.add('visible');
  });
}, { threshold: 0.15, rootMargin: '0px 0px -40px 0px' });
document.querySelectorAll('.reveal').forEach(el => revealObserver.observe(el));

// Animated Counters
const counterObserver = new IntersectionObserver((entries) => {
  entries.forEach(entry => {
    if (!entry.isIntersecting) return;
    const el = entry.target;
    const target = parseFloat(el.dataset.count);
    const prefix = el.dataset.prefix || '';
    const suffix = el.dataset.suffix || '';
    const isFloat = target % 1 !== 0;
    const duration = 2000;
    const start = performance.now();
    function update(now) {
      const progress = Math.min((now - start) / duration, 1);
      const ease = 1 - Math.pow(1 - progress, 3);
      const current = isFloat ? (target * ease).toFixed(1) : Math.floor(target * ease);
      el.textContent = prefix + current + suffix;
      if (progress < 1) requestAnimationFrame(update);
      else el.textContent = prefix + (isFloat ? target.toFixed(1) : target) + suffix;
    }
    requestAnimationFrame(update);
    counterObserver.unobserve(el);
  });
}, { threshold: 0.5 });
document.querySelectorAll('.stat-number').forEach(el => counterObserver.observe(el));

// FAQ Accordion
document.querySelectorAll('.faq-question').forEach(btn => {
  btn.addEventListener('click', () => {
    const item = btn.parentElement;
    const isOpen = item.classList.contains('open');
    document.querySelectorAll('.faq-item.open').forEach(i => i.classList.remove('open'));
    if (!isOpen) item.classList.add('open');
  });
});

// Smooth Scroll
document.querySelectorAll('a[href^="#"]').forEach(a => {
  a.addEventListener('click', (e) => {
    const href = a.getAttribute('href');
    if (href === '#') return;
    e.preventDefault();
    const target = document.querySelector(href);
    if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
});

// Liquid Metal Texture (generated once, applied via CSS animation)
if (typeof requestIdleCallback !== 'undefined') {
  requestIdleCallback(function() { generateMetalTexture(); });
} else {
  setTimeout(function() { generateMetalTexture(); }, 100);
}

function generateMetalTexture() {
  const btns = document.querySelectorAll('.btn-metal-shader');
  if (!btns.length) return;

  const W = 128, H = 24;
  const c = document.createElement('canvas');
  c.width = W; c.height = H;
  const ctx = c.getContext('2d');
  if (!ctx) return;

  function h(x,y){const n=Math.sin(x*127.1+y*311.7)*43758.5;return n-Math.floor(n);}
  function sn(x,y){
    const ix=x|0,iy=y|0,fx=x-ix,fy=y-iy;
    const sx=fx*fx*(3-2*fx),sy=fy*fy*(3-2*fy);
    return h(ix,iy)+(h(ix+1,iy)-h(ix,iy))*sx+(h(ix,iy+1)-h(ix,iy))*sy+(h(ix,iy)-h(ix+1,iy)-h(ix,iy+1)+h(ix+1,iy+1))*sx*sy;
  }
  function fb(x,y){let v=0,a=.5;for(let i=0;i<3;i++){v+=a*sn(x,y);x*=2.1;y*=2.1;a*=.5;}return v;}

  const d = ctx.createImageData(W,H);
  for(let py=0;py<H;py++){
    for(let px=0;px<W;px++){
      const u=px/W,v=py/H;
      const n1=fb(u*6+1.7,v*3+.3);
      const n2=fb(u*5+n1*1.5,v*2.5-.3);
      const hi=Math.max(0,Math.min(1,(n2-.3)/.4));
      const sh=Math.max(0,Math.min(1,(.6-n1)/.3));
      let r=20+sh*55+hi*95,g=20+sh*55+hi*95,b=25+sh*60+hi*105;
      r+=Math.sin(n1*6.28)*8;b+=Math.cos(n2*6.28)*8;
      const i=(py*W+px)*4;
      d.data[i]=r|0;d.data[i+1]=g|0;d.data[i+2]=b|0;d.data[i+3]=255;
    }
  }
  ctx.putImageData(d,0,0);
  const url=c.toDataURL();

  const s=document.createElement('style');
  s.textContent=`
    @keyframes metal-flow{0%{background-position:0% 50%}50%{background-position:100% 50%}100%{background-position:0% 50%}}
    .btn-metal-shader{background-image:url(${url})!important;background-size:200% 100%!important;animation:metal-flow 8s ease-in-out infinite!important;}
    .btn-metal:hover .btn-metal-shader{animation-duration:3s!important;}
  `;
  document.head.appendChild(s);
  c.width=c.height=0;
}
