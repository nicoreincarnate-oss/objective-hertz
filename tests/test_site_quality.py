"""Tests for site-quality guards that block broken/generic sites."""

import asyncio
from unittest.mock import AsyncMock

from clawdbot.site_quality import analyze_site_markup


def run(coro):
    return asyncio.run(coro)


def test_analyze_site_markup_rejects_placeholder_assets_and_copy():
    html = """
    <!DOCTYPE html>
    <html>
      <body>
        <nav>Menu</nav>
        <h1>Atlas Dental</h1>
        <img src="https://images.unsplash.com/photo-123" alt="placeholder" />
        <main><p>Lorem ipsum dolor sit amet.</p></main>
        <footer>Contact us</footer>
      </body>
    </html>
    """

    result = analyze_site_markup(html, business_name="Atlas Dental", site_type="demo")

    assert result["passed"] is False
    assert "no_placeholder_assets" in result["issues"]
    assert "no_placeholder_copy" in result["issues"]


def test_analyze_site_markup_accepts_business_specific_markup():
    html = """
    <!DOCTYPE html>
    <html>
      <head>
        <style>
          :root { --ink: #101828; }
          body { font-family: Inter, sans-serif; background: linear-gradient(180deg, #fff, #f4f7fb); }
          .hero { transition: transform 0.3s ease; }
          @media (prefers-reduced-motion: reduce) { * { animation: none !important; } }
        </style>
      </head>
      <body>
        <nav>Atlas Dental</nav>
        <main>
          <section class="hero">
            <h1>Atlas Dental</h1>
            <p>Family and cosmetic dentistry in Mazatlan with same-week consultations.</p>
            <a href="#contact">Book a consultation</a>
          </section>
          <section>
            <h2>Why choose us</h2>
            <p>Trusted by local families, 15 years of experience, and 200+ smile restorations.</p>
          </section>
          <section id="contact">
            <form><input type="email" /><button>Request appointment</button></form>
          </section>
        </main>
        <footer>Atlas Dental | Mazatlan | hello@example.com</footer>
        <script>
          const observer = new IntersectionObserver(() => {});
          observer.observe(document.querySelector('.hero'));
        </script>
      </body>
    </html>
    """

    result = analyze_site_markup(html, business_name="Atlas Dental", site_type="demo")

    assert result["passed"] is True
    assert result["score"] >= result["threshold"]


def test_evaluate_site_experience_uses_visual_critic(monkeypatch):
    from clawdbot import site_quality

    html = """
    <!DOCTYPE html>
    <html>
      <head>
        <style>
          body { background: linear-gradient(180deg, #fff, #eef4ff); font-family: Inter, sans-serif; }
          .hero { transition: transform 0.3s ease; }
        </style>
      </head>
      <body>
        <nav>Atlas Dental</nav>
        <main>
          <section class="hero">
            <h1>Atlas Dental</h1>
            <p>Cosmetic and family dentistry in Mazatlan with same-week consultations.</p>
            <a href="#contact">Book now</a>
          </section>
          <section>
            <h2>Why choose us</h2>
            <p>Trusted by local families, 15 years of experience, and modern care.</p>
          </section>
          <section id="contact"><form><input type="email" /><button>Request appointment</button></form></section>
        </main>
        <footer>Atlas Dental | Mazatlan</footer>
      </body>
    </html>
    """

    monkeypatch.setattr(
        site_quality,
        "_capture_site_screenshots",
        AsyncMock(return_value={"desktop": b"png", "mobile": b"png"}),
    )
    monkeypatch.setattr(
        site_quality,
        "_visual_critic",
        AsyncMock(
            return_value={
                "score": 81,
                "passed": True,
                "summary": "Strong hierarchy",
                "strengths": ["clear hero"],
                "issues": [],
                "revision_instructions": [],
            }
        ),
    )

    result = run(site_quality.evaluate_site_experience(html=html, business_name="Atlas Dental", site_type="demo"))

    assert result["passed"] is True
    assert result["visual_review"]["score"] == 81


def _make_valid_page(business_name: str, page_title: str) -> str:
    # Must exceed min_html_length=2600 for full site type
    return f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{page_title} - {business_name}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
      :root {{ --ink: #101828; --accent: #e85d50; --surface: #f8fafc; }}
      body {{ font-family: Inter, sans-serif; background: linear-gradient(180deg, #fff, #f4f7fb); color: var(--ink); }}
      .hero {{ transition: transform 0.3s ease; }}
      .card {{ box-shadow: 0 4px 24px rgba(0,0,0,0.06); backdrop-filter: blur(8px); letter-spacing: -0.01em; }}
      @media (prefers-reduced-motion: reduce) {{ * {{ animation: none !important; transition: none !important; }} }}
    </style>
  </head>
  <body class="min-h-screen">
    <nav class="flex items-center justify-between px-6 py-4 border-b border-gray-100">
      <a href="index.html" class="text-xl font-bold">{business_name}</a>
      <div class="hidden md:flex gap-6">
        <a href="about.html" class="text-gray-600 hover:text-gray-900">About</a>
        <a href="services.html" class="text-gray-600 hover:text-gray-900">Services</a>
        <a href="gallery.html" class="text-gray-600 hover:text-gray-900">Gallery</a>
        <a href="contact.html" class="text-gray-600 hover:text-gray-900">Contact</a>
      </div>
    </nav>
    <main>
      <section class="hero max-w-4xl mx-auto px-6 py-20 text-center">
        <h1 class="text-5xl font-bold mb-6">{page_title} - {business_name}</h1>
        <p class="text-xl text-gray-600 mb-8">Professional dental and cosmetic services with over 15 years of experience serving families in Mazatlan. We combine modern technology with compassionate care to deliver beautiful, healthy smiles that last a lifetime.</p>
        <a href="#contact" class="inline-block bg-[var(--accent)] text-white px-8 py-4 rounded-lg font-semibold hover:opacity-90 transition">Book a consultation today</a>
      </section>
      <section class="bg-[var(--surface)] py-16 px-6">
        <div class="max-w-4xl mx-auto grid grid-cols-1 md:grid-cols-3 gap-8">
          <div class="card bg-white p-6 rounded-xl">
            <h3 class="font-semibold mb-2">15+ Years Experience</h3>
            <p class="text-gray-600">Trusted by hundreds of families across Mazatlan and surrounding areas for comprehensive dental care.</p>
          </div>
          <div class="card bg-white p-6 rounded-xl">
            <h3 class="font-semibold mb-2">Modern Technology</h3>
            <p class="text-gray-600">State-of-the-art equipment and techniques for painless, efficient treatments and beautiful results.</p>
          </div>
          <div class="card bg-white p-6 rounded-xl">
            <h3 class="font-semibold mb-2">Same-Week Appointments</h3>
            <p class="text-gray-600">We value your time. Most new patients are seen within the same week of booking their consultation.</p>
          </div>
        </div>
      </section>
      <section class="py-16 px-6">
        <div class="max-w-4xl mx-auto">
          <h2 class="text-3xl font-bold mb-8 text-center">Why choose {business_name}</h2>
          <p class="text-gray-600 text-center max-w-2xl mx-auto">With over 200 smile restorations completed and a 98 percent patient satisfaction rate, we are the trusted choice for families who want the best dental care available. Our testimonials speak for themselves.</p>
        </div>
      </section>
      <section id="contact" class="bg-gray-50 py-16 px-6">
        <div class="max-w-md mx-auto">
          <h2 class="text-2xl font-bold mb-6 text-center">Request an appointment</h2>
          <form class="space-y-4">
            <input type="text" name="name" class="w-full px-4 py-3 rounded-lg border" />
            <input type="email" name="email" class="w-full px-4 py-3 rounded-lg border" />
            <textarea name="message" class="w-full px-4 py-3 rounded-lg border" rows="3"></textarea>
            <button type="submit" class="w-full bg-[var(--accent)] text-white py-3 rounded-lg font-semibold">Book now</button>
          </form>
        </div>
      </section>
    </main>
    <footer class="bg-gray-900 text-white py-8 px-6">
      <div class="max-w-4xl mx-auto flex flex-col md:flex-row justify-between items-center gap-4">
        <p class="font-semibold">{business_name}</p>
        <p class="text-gray-400">Mazatlan, Mexico | hello@atlasdental.mx | Tel: +52 669 123 4567</p>
      </div>
    </footer>
    <script>
      const observer = new IntersectionObserver((entries) => {{
        entries.forEach(e => {{ if (e.isIntersecting) e.target.style.opacity = 1; }});
      }});
      document.querySelectorAll('.card').forEach(el => observer.observe(el));
    </script>
  </body>
</html>"""


def test_evaluate_multipage_site_passes_when_all_pages_pass(monkeypatch):
    from clawdbot import site_quality

    pages = {
        "index.html": _make_valid_page("Atlas Dental", "Home"),
        "about.html": _make_valid_page("Atlas Dental", "About"),
        "services.html": _make_valid_page("Atlas Dental", "Services"),
        "contact.html": _make_valid_page("Atlas Dental", "Contact"),
    }

    monkeypatch.setattr(
        site_quality,
        "_capture_site_screenshots",
        AsyncMock(return_value={"desktop": b"png", "mobile": b"png"}),
    )
    monkeypatch.setattr(
        site_quality,
        "_visual_critic",
        AsyncMock(return_value={"score": 85, "passed": True, "summary": "Good", "strengths": [], "issues": [], "revision_instructions": []}),
    )

    result = run(site_quality.evaluate_multipage_site(pages=pages, business_name="Atlas Dental"))

    assert result["passed"] is True
    assert result["page_count"] == 4
    assert "index.html" in result["page_results"]
    assert "about.html" in result["page_results"]


def test_evaluate_multipage_site_fails_when_one_page_fails():
    from clawdbot import site_quality

    pages = {
        "index.html": _make_valid_page("Atlas Dental", "Home"),
        "about.html": "<html><body>broken page with lorem ipsum placeholder</body></html>",
    }

    result = run(site_quality.evaluate_multipage_site(pages=pages, business_name="Atlas Dental"))

    assert result["passed"] is False
    assert any("about.html" in issue for issue in result["issues"])
