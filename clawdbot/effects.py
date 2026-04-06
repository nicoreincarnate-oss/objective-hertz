"""Effect translation layer for ClawdBot site generation.

15 premium effects translated from Aceternity UI / Magic UI / Awwwards
patterns to vanilla JS (GSAP, Motion, CSS, p5.js). Each effect respects
prefers-reduced-motion and has a no-JS fallback.
"""

from __future__ import annotations

import textwrap
from typing import Any


def inject_effect(effect_name: str, selector: str, config: dict[str, Any] | None = None) -> str:
    """Return a <script> block that applies the named effect to `selector`.

    Args:
        effect_name: Key from the EFFECTS dict.
        selector: CSS selector string (e.g., '.hero-title', '#features .card').
        config: Optional overrides for effect parameters.

    Returns:
        A complete <script> block string ready to embed in HTML.
        Returns an empty string if the effect name is unknown.
    """
    generator = EFFECTS.get(effect_name)
    if generator is None:
        return ""
    return generator(selector, config or {})


def get_effect_css(effect_name: str) -> str:
    """Return CSS for effects that need stylesheet rules.

    Only some effects (text_shimmer, marquee_ticker) require CSS.
    Returns empty string for JS-only effects.
    """
    generator = _EFFECT_CSS.get(effect_name)
    if generator is None:
        return ""
    return generator()


def list_effects() -> list[str]:
    """Return the names of all available effects."""
    return list(EFFECTS.keys())


# ---------------------------------------------------------------------------
# Reduced-motion wrapper
# ---------------------------------------------------------------------------

def _wrap_reduced_motion(js: str) -> str:
    """Wrap JS in a reduced-motion check."""
    return textwrap.dedent(f"""\
        <script>
        (function() {{
          if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
          {js}
        }})();
        </script>""")


# ---------------------------------------------------------------------------
# GSAP-based effects (scroll-driven)
# ---------------------------------------------------------------------------

def _fade_up_scroll(selector: str, cfg: dict[str, Any]) -> str:
    y = cfg.get("y", 40)
    duration = cfg.get("duration", 0.8)
    stagger = cfg.get("stagger", 0)
    js = textwrap.dedent(f"""\
        gsap.registerPlugin(ScrollTrigger);
        gsap.utils.toArray('{selector}').forEach(function(el) {{
          gsap.from(el, {{
            y: {y},
            opacity: 0,
            duration: {duration},
            stagger: {stagger},
            ease: 'power2.out',
            scrollTrigger: {{
              trigger: el,
              start: 'top 85%',
              toggleActions: 'play none none none'
            }}
          }});
        }});""")
    return _wrap_reduced_motion(js)


def _stagger_children(selector: str, cfg: dict[str, Any]) -> str:
    delay = cfg.get("delay", 0.1)
    y = cfg.get("y", 30)
    duration = cfg.get("duration", 0.6)
    js = textwrap.dedent(f"""\
        gsap.registerPlugin(ScrollTrigger);
        document.querySelectorAll('{selector}').forEach(function(parent) {{
          gsap.from(parent.children, {{
            y: {y},
            opacity: 0,
            duration: {duration},
            stagger: {delay},
            ease: 'power2.out',
            scrollTrigger: {{
              trigger: parent,
              start: 'top 80%',
              toggleActions: 'play none none none'
            }}
          }});
        }});""")
    return _wrap_reduced_motion(js)


def _parallax_bg(selector: str, cfg: dict[str, Any]) -> str:
    speed = cfg.get("speed", 0.3)
    js = textwrap.dedent(f"""\
        gsap.registerPlugin(ScrollTrigger);
        gsap.utils.toArray('{selector}').forEach(function(el) {{
          gsap.to(el, {{
            yPercent: {speed * 100},
            ease: 'none',
            scrollTrigger: {{
              trigger: el.parentElement || el,
              start: 'top bottom',
              end: 'bottom top',
              scrub: true
            }}
          }});
        }});""")
    return _wrap_reduced_motion(js)


def _split_text_reveal(selector: str, cfg: dict[str, Any]) -> str:
    duration = cfg.get("duration", 0.05)
    stagger = cfg.get("stagger", 0.03)
    js = textwrap.dedent(f"""\
        gsap.registerPlugin(ScrollTrigger);
        document.querySelectorAll('{selector}').forEach(function(el) {{
          var text = el.textContent;
          el.innerHTML = '';
          text.split('').forEach(function(ch) {{
            var span = document.createElement('span');
            span.style.display = 'inline-block';
            span.style.opacity = '0';
            span.style.transform = 'translateY(20px)';
            span.textContent = ch === ' ' ? '\\u00A0' : ch;
            el.appendChild(span);
          }});
          gsap.to(el.children, {{
            opacity: 1,
            y: 0,
            duration: {duration},
            stagger: {stagger},
            ease: 'power2.out',
            scrollTrigger: {{
              trigger: el,
              start: 'top 80%',
              toggleActions: 'play none none none'
            }}
          }});
        }});""")
    return _wrap_reduced_motion(js)


def _pin_scrub(selector: str, cfg: dict[str, Any]) -> str:
    duration_vw = cfg.get("duration_vw", "200%")
    js = textwrap.dedent(f"""\
        gsap.registerPlugin(ScrollTrigger);
        document.querySelectorAll('{selector}').forEach(function(el) {{
          ScrollTrigger.create({{
            trigger: el,
            start: 'top top',
            end: '+={duration_vw}',
            pin: true,
            scrub: 1
          }});
        }});""")
    return _wrap_reduced_motion(js)


# ---------------------------------------------------------------------------
# Motion (vanilla JS, spring physics)
# ---------------------------------------------------------------------------

def _spring_hover(selector: str, cfg: dict[str, Any]) -> str:
    scale = cfg.get("scale", 1.05)
    js = textwrap.dedent(f"""\
        import {{ animate, spring }} from 'https://cdn.jsdelivr.net/npm/motion@12/+esm';
        document.querySelectorAll('{selector}').forEach(function(el) {{
          el.addEventListener('mouseenter', function() {{
            animate(el, {{ scale: {scale} }}, {{ type: spring, stiffness: 300, damping: 20 }});
          }});
          el.addEventListener('mouseleave', function() {{
            animate(el, {{ scale: 1 }}, {{ type: spring, stiffness: 300, damping: 20 }});
          }});
        }});""")
    return textwrap.dedent(f"""\
        <script type="module">
        if (!window.matchMedia('(prefers-reduced-motion: reduce)').matches) {{
          {js}
        }}
        </script>""")


def _layout_animate(selector: str, cfg: dict[str, Any]) -> str:
    duration = cfg.get("duration", 0.4)
    js = textwrap.dedent(f"""\
        import {{ animate }} from 'https://cdn.jsdelivr.net/npm/motion@12/+esm';
        document.querySelectorAll('{selector}').forEach(function(el) {{
          var observer = new MutationObserver(function() {{
            animate(el, {{ opacity: [0.5, 1] }}, {{ duration: {duration} }});
          }});
          observer.observe(el, {{ attributes: true, childList: true, subtree: false }});
        }});""")
    return textwrap.dedent(f"""\
        <script type="module">
        if (!window.matchMedia('(prefers-reduced-motion: reduce)').matches) {{
          {js}
        }}
        </script>""")


def _exit_animate(selector: str, cfg: dict[str, Any]) -> str:
    duration = cfg.get("duration", 0.3)
    y = cfg.get("y", 10)
    js = textwrap.dedent(f"""\
        import {{ animate }} from 'https://cdn.jsdelivr.net/npm/motion@12/+esm';
        window.__exitAnimate = function(el) {{
          return animate(el, {{ opacity: 0, y: {y} }}, {{ duration: {duration} }}).finished;
        }};""")
    return textwrap.dedent(f"""\
        <script type="module">
        if (!window.matchMedia('(prefers-reduced-motion: reduce)').matches) {{
          {js}
        }}
        </script>""")


# ---------------------------------------------------------------------------
# Vanilla JS + CSS effects
# ---------------------------------------------------------------------------

def _number_counter(selector: str, cfg: dict[str, Any]) -> str:
    duration_ms = cfg.get("duration", 2000)
    js = textwrap.dedent(f"""\
        var observer = new IntersectionObserver(function(entries) {{
          entries.forEach(function(entry) {{
            if (!entry.isIntersecting) return;
            var el = entry.target;
            var target = parseFloat(el.dataset.target || el.textContent.replace(/[^0-9.]/g, ''));
            if (isNaN(target)) return;
            var prefix = el.textContent.match(/^[^0-9]*/)[0] || '';
            var suffix = el.textContent.match(/[^0-9]*$/)[0] || '';
            var start = performance.now();
            function step(ts) {{
              var progress = Math.min((ts - start) / {duration_ms}, 1);
              var ease = 1 - Math.pow(1 - progress, 3);
              var val = Math.round(ease * target);
              el.textContent = prefix + val.toLocaleString() + suffix;
              if (progress < 1) requestAnimationFrame(step);
            }}
            requestAnimationFrame(step);
            observer.unobserve(el);
          }});
        }}, {{ threshold: 0.3 }});
        document.querySelectorAll('{selector}').forEach(function(el) {{ observer.observe(el); }});""")
    return _wrap_reduced_motion(js)


def _text_shimmer(selector: str, cfg: dict[str, Any]) -> str:
    duration = cfg.get("duration", "3s")
    js = textwrap.dedent(f"""\
        document.querySelectorAll('{selector}').forEach(function(el) {{
          el.style.backgroundImage = 'linear-gradient(90deg, var(--color-text) 0%, var(--color-accent) 50%, var(--color-text) 100%)';
          el.style.backgroundSize = '200% auto';
          el.style.webkitBackgroundClip = 'text';
          el.style.backgroundClip = 'text';
          el.style.webkitTextFillColor = 'transparent';
          el.style.animation = 'shimmer {duration} linear infinite';
        }});""")
    return _wrap_reduced_motion(js)


def _spotlight_cursor(selector: str, cfg: dict[str, Any]) -> str:
    size = cfg.get("size", 400)
    js = textwrap.dedent(f"""\
        document.querySelectorAll('{selector}').forEach(function(el) {{
          el.style.position = el.style.position || 'relative';
          el.style.overflow = 'hidden';
          var spot = document.createElement('div');
          spot.style.cssText = 'position:absolute;inset:0;pointer-events:none;opacity:0;transition:opacity 0.3s;';
          el.appendChild(spot);
          el.addEventListener('mousemove', function(e) {{
            var r = el.getBoundingClientRect();
            var x = e.clientX - r.left;
            var y = e.clientY - r.top;
            spot.style.background = 'radial-gradient(circle {size}px at ' + x + 'px ' + y + 'px, rgba(255,255,255,0.06), transparent 70%)';
            spot.style.opacity = '1';
          }});
          el.addEventListener('mouseleave', function() {{
            spot.style.opacity = '0';
          }});
        }});""")
    return _wrap_reduced_motion(js)


def _magnetic_button(selector: str, cfg: dict[str, Any]) -> str:
    strength = cfg.get("strength", 0.3)
    js = textwrap.dedent(f"""\
        document.querySelectorAll('{selector}').forEach(function(btn) {{
          btn.style.transition = 'transform 0.2s ease-out';
          btn.addEventListener('mousemove', function(e) {{
            var r = btn.getBoundingClientRect();
            var x = (e.clientX - r.left - r.width / 2) * {strength};
            var y = (e.clientY - r.top - r.height / 2) * {strength};
            btn.style.transform = 'translate(' + x + 'px, ' + y + 'px)';
          }});
          btn.addEventListener('mouseleave', function() {{
            btn.style.transform = 'translate(0, 0)';
          }});
        }});""")
    return _wrap_reduced_motion(js)


def _marquee_ticker(selector: str, cfg: dict[str, Any]) -> str:
    duration = cfg.get("duration", "20s")
    js = textwrap.dedent(f"""\
        document.querySelectorAll('{selector}').forEach(function(el) {{
          var content = el.innerHTML;
          el.innerHTML = '<div class=\"marquee-track\" style=\"display:flex;gap:2rem;animation:marquee-scroll {duration} linear infinite;width:max-content;\">' + content + content + '</div>';
        }});""")
    return _wrap_reduced_motion(js)


def _card_tilt_3d(selector: str, cfg: dict[str, Any]) -> str:
    max_deg = cfg.get("max_deg", 10)
    js = textwrap.dedent(f"""\
        document.querySelectorAll('{selector}').forEach(function(card) {{
          card.style.transition = 'transform 0.15s ease-out';
          card.style.transformStyle = 'preserve-3d';
          card.addEventListener('mousemove', function(e) {{
            var r = card.getBoundingClientRect();
            var x = (e.clientX - r.left) / r.width - 0.5;
            var y = (e.clientY - r.top) / r.height - 0.5;
            card.style.transform = 'perspective(800px) rotateY(' + (x * {max_deg}) + 'deg) rotateX(' + (-y * {max_deg}) + 'deg)';
          }});
          card.addEventListener('mouseleave', function() {{
            card.style.transform = 'perspective(800px) rotateY(0deg) rotateX(0deg)';
          }});
        }});""")
    return _wrap_reduced_motion(js)


def _p5_particles(selector: str, cfg: dict[str, Any]) -> str:
    count = cfg.get("count", 60)
    color_var = cfg.get("color", "var(--color-accent)")
    js = textwrap.dedent(f"""\
        var container = document.querySelector('{selector}');
        if (container) {{
          container.style.position = container.style.position || 'relative';
          var canvasDiv = document.createElement('div');
          canvasDiv.id = 'p5-particles-host';
          canvasDiv.style.cssText = 'position:absolute;inset:0;pointer-events:none;z-index:0;';
          container.prepend(canvasDiv);
          new p5(function(p) {{
            var particles = [];
            var accentRGB;
            p.setup = function() {{
              var c = p.createCanvas(container.offsetWidth, container.offsetHeight);
              c.parent('p5-particles-host');
              var tempDiv = document.createElement('div');
              tempDiv.style.color = '{color_var}';
              document.body.appendChild(tempDiv);
              var rgb = getComputedStyle(tempDiv).color.match(/\\d+/g);
              document.body.removeChild(tempDiv);
              accentRGB = rgb ? rgb.map(Number) : [100, 100, 255];
              for (var i = 0; i < {count}; i++) {{
                particles.push({{
                  x: p.random(p.width),
                  y: p.random(p.height),
                  vx: p.random(-0.3, 0.3),
                  vy: p.random(-0.3, 0.3),
                  size: p.random(2, 5)
                }});
              }}
            }};
            p.draw = function() {{
              p.clear();
              for (var i = 0; i < particles.length; i++) {{
                var pt = particles[i];
                pt.x += pt.vx; pt.y += pt.vy;
                if (pt.x < 0 || pt.x > p.width) pt.vx *= -1;
                if (pt.y < 0 || pt.y > p.height) pt.vy *= -1;
                p.noStroke();
                p.fill(accentRGB[0], accentRGB[1], accentRGB[2], 80);
                p.ellipse(pt.x, pt.y, pt.size, pt.size);
              }}
            }};
            p.windowResized = function() {{
              p.resizeCanvas(container.offsetWidth, container.offsetHeight);
            }};
          }});
        }}""")
    return _wrap_reduced_motion(js)


# ---------------------------------------------------------------------------
# CSS generators for effects that need stylesheets
# ---------------------------------------------------------------------------

def _shimmer_css() -> str:
    return textwrap.dedent("""\
        <style>
        @keyframes shimmer {
          0% { background-position: 200% center; }
          100% { background-position: -200% center; }
        }
        </style>""")


def _marquee_css() -> str:
    return textwrap.dedent("""\
        <style>
        @keyframes marquee-scroll {
          0% { transform: translateX(0); }
          100% { transform: translateX(-50%); }
        }
        </style>""")


# ---------------------------------------------------------------------------
# Effect registries
# ---------------------------------------------------------------------------

EFFECTS: dict[str, Any] = {
    "fade_up_scroll": _fade_up_scroll,
    "stagger_children": _stagger_children,
    "parallax_bg": _parallax_bg,
    "split_text_reveal": _split_text_reveal,
    "pin_scrub": _pin_scrub,
    "spring_hover": _spring_hover,
    "layout_animate": _layout_animate,
    "exit_animate": _exit_animate,
    "number_counter": _number_counter,
    "text_shimmer": _text_shimmer,
    "spotlight_cursor": _spotlight_cursor,
    "magnetic_button": _magnetic_button,
    "marquee_ticker": _marquee_ticker,
    "card_tilt_3d": _card_tilt_3d,
    "p5_particles": _p5_particles,
}

_EFFECT_CSS: dict[str, Any] = {
    "text_shimmer": _shimmer_css,
    "marquee_ticker": _marquee_css,
}
