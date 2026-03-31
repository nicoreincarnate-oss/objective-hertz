# Athena Studios -- Brand Kit

**Version:** 1.0
**Last Updated:** 2026-03-29
**Purpose:** Complete visual identity specification for Athena Studios, a premium web design studio serving local businesses. Every value is explicit so any designer or AI can produce assets without ambiguity.

**Design Philosophy:** Antigravity methodology -- spatial depth, subtle glassmorphism, floating card elements -- applied to a professional, client-facing context. The result is premium but accessible, wisdom-inspired, and warm. Nothing cold or overly techy. This brand says: "We are experts you can trust, and we speak your language."

---

## 1. Color Palette

### 1.1 Primary Colors

| Name          | Hex       | RGB             | HSL              | Usage                                      |
|---------------|-----------|-----------------|------------------|---------------------------------------------|
| Athena Navy   | `#1B2A4A` | 27, 42, 74      | 221, 47%, 20%    | Backgrounds, headers, nav, footer, overlays |
| Athena Gold   | `#D4A853` | 212, 168, 83    | 40, 60%, 58%     | CTAs, accents, highlights, hover states     |
| Pure White    | `#FFFFFF` | 255, 255, 255   | 0, 0%, 100%      | Card backgrounds, text on navy, spacing     |

### 1.2 Secondary Colors

| Name          | Hex       | RGB             | Usage                                        |
|---------------|-----------|-----------------|-----------------------------------------------|
| Warm Slate    | `#4A5568` | 74, 85, 104     | Body text, paragraphs, descriptions           |
| Light Gold    | `#F5E6C8` | 245, 230, 200   | Alternate section backgrounds, subtle accents |
| Soft Gray     | `#F7F8FA` | 247, 248, 250   | Page background, card containers, dividers    |
| Success Green | `#38A169` | 56, 161, 105    | Success states, checkmarks, positive metrics  |
| Trust Blue    | `#3182CE` | 49, 130, 206    | Links, info badges, trust indicators          |

### 1.3 Extended Utility Colors

| Name            | Hex       | Usage                                    |
|-----------------|-----------|-------------------------------------------|
| Deep Navy       | `#111D35` | Footer background, deepest layer          |
| Navy Mid        | `#2D3E6B` | Gradient endpoint, hover backgrounds      |
| Border Gray     | `#E2E8F0` | Card borders, dividers, input borders     |
| Muted Text      | `#718096` | Captions, timestamps, tertiary text       |
| Error Red       | `#E53E3E` | Error states, destructive actions         |
| Warning Amber   | `#DD6B20` | Warning states, attention required        |
| Gold Light      | `#E8C373` | Gold hover state, lighter accent          |
| Gold Dark       | `#B8912F` | Gold active/pressed state                 |
| Overlay Black   | `rgba(27, 42, 74, 0.85)` | Modal overlays, hero darkening |

### 1.4 Gradients

| Name              | CSS                                                                 | Usage                          |
|-------------------|----------------------------------------------------------------------|--------------------------------|
| Hero Gradient     | `linear-gradient(135deg, #1B2A4A 0%, #2D3E6B 100%)`                | Hero sections, page headers    |
| Navy Depth        | `linear-gradient(180deg, #1B2A4A 0%, #111D35 100%)`                | Footer, deep sections          |
| Gold Hover        | `linear-gradient(135deg, #D4A853 0%, #E8C373 100%)`                | Button hover state             |
| Gold Shimmer      | `linear-gradient(90deg, #D4A853 0%, #E8C373 50%, #D4A853 100%)`   | Animated accent lines          |
| Light Section     | `linear-gradient(180deg, #FFFFFF 0%, #F7F8FA 100%)`                | Alternating content sections   |
| Glass Card        | `linear-gradient(135deg, rgba(255,255,255,0.1) 0%, rgba(255,255,255,0.05) 100%)` | Glassmorphism cards on navy |

### 1.5 Color Proportions

Apply the 60-30-10 rule across every page:

- **60% Navy** -- backgrounds, headers, hero sections, footer, navigation
- **30% White / Soft Gray** -- content areas, card backgrounds, breathing space
- **10% Gold** -- CTAs, accents, highlights, hover states, small decorative elements

Rules:
- Never use pure black (`#000000`) for text. Use Athena Navy (`#1B2A4A`) or Warm Slate (`#4A5568`).
- Gold is an accent color only. Never use it for large background areas or body text.
- On navy backgrounds, text is always Pure White or Light Gold.
- On white/gray backgrounds, headings use Athena Navy, body uses Warm Slate.
- Links on white backgrounds use Trust Blue (`#3182CE`), underlined on hover.

### 1.6 Dark Mode Palette (Optional)

For users who prefer dark mode or future dark-theme implementation.

| Light Mode        | Dark Mode Equivalent | Hex       | Notes                          |
|-------------------|----------------------|-----------|--------------------------------|
| Soft Gray bg      | Deep Navy bg         | `#111D35` | Page background                |
| White card bg     | Navy card bg         | `#1B2A4A` | Card surfaces                  |
| Athena Navy text  | Pure White text      | `#FFFFFF` | Heading text                   |
| Warm Slate text   | Muted light text     | `#CBD5E0` | Body text                      |
| Border Gray       | Dark border          | `#2D3E6B` | Card borders, dividers         |
| Athena Gold       | Athena Gold          | `#D4A853` | No change -- gold stays gold   |
| Light Gold bg     | Dark gold tint       | `#1E2A3E` | Subtle warm-tinted navy        |

Dark mode shadows use `rgba(0, 0, 0, 0.4)` instead of `rgba(0, 0, 0, 0.1)`.

---

## 2. Typography

### 2.1 Font Families

**Primary System (recommended):**
- **Headings + Body:** Inter (Google Fonts)
- Weights loaded: 400 (Regular), 500 (Medium), 600 (SemiBold), 700 (Bold)
- `font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;`

**Alternative System (editorial feel):**
- **Headings:** DM Serif Display (Google Fonts), weight 400 only
- **Body:** Inter (same as primary)
- `font-family: 'DM Serif Display', Georgia, 'Times New Roman', serif;` (headings)
- Use this alternative when a warmer, more classical tone is desired.

**Monospace (code, technical):**
- JetBrains Mono or `'SF Mono', 'Fira Code', 'Consolas', monospace`
- Weight 400, size 14px

### 2.2 Type Scale

| Element           | Desktop Size | Mobile Size | Weight | Line Height | Letter Spacing | Font Family     |
|-------------------|-------------|-------------|--------|-------------|----------------|-----------------|
| Hero H1           | 56px        | 36px        | 700    | 1.1         | -0.02em        | Inter or DM Serif |
| Hero H1 (large)   | 64px        | 40px        | 700    | 1.05        | -0.025em       | Inter or DM Serif |
| Section H2        | 36px        | 28px        | 700    | 1.2         | -0.015em       | Inter or DM Serif |
| Section H2 (large)| 40px        | 32px        | 700    | 1.15        | -0.02em        | Inter or DM Serif |
| Card H3           | 22px        | 20px        | 600    | 1.3         | -0.01em        | Inter           |
| Card H3 (large)   | 24px        | 22px        | 600    | 1.3         | -0.01em        | Inter           |
| Subheading H4     | 18px        | 16px        | 600    | 1.4         | 0              | Inter           |
| Body              | 17px        | 16px        | 400    | 1.7         | 0              | Inter           |
| Body (large)      | 18px        | 17px        | 400    | 1.7         | 0              | Inter           |
| Body (bold)       | 17px        | 16px        | 500    | 1.7         | 0              | Inter           |
| Small / Caption   | 14px        | 13px        | 400    | 1.5         | 0.01em         | Inter           |
| CTA Button        | 16px        | 16px        | 600    | 1.0         | 0.02em         | Inter           |
| CTA Button (large)| 18px        | 17px        | 600    | 1.0         | 0.02em         | Inter           |
| Navigation        | 15px        | 15px        | 500    | 1.0         | 0.01em         | Inter           |
| Price (large)     | 52px        | 40px        | 700    | 1.0         | -0.03em        | Inter           |
| Price (currency)  | 24px        | 20px        | 600    | 1.0         | 0              | Inter           |
| Badge / Tag       | 12px        | 12px        | 600    | 1.0         | 0.05em         | Inter           |
| Testimonial Quote | 20px        | 18px        | 400    | 1.6         | 0              | Inter or DM Serif |

### 2.3 Responsive Breakpoints for Typography

| Breakpoint    | Width       | Scale Factor |
|---------------|-------------|--------------|
| Desktop XL    | >= 1280px   | 1.0 (base)   |
| Desktop       | >= 1024px   | 0.95         |
| Tablet        | >= 768px    | 0.85         |
| Mobile        | < 768px     | 0.75         |

### 2.4 Typography Rules

- Maximum line width for body text: 680px (approximately 65-75 characters per line).
- Paragraph spacing: `margin-bottom: 1.5em` for body text.
- Heading spacing: `margin-top: 2em; margin-bottom: 0.75em` for section headings.
- Never use all-uppercase for body text. Uppercase is acceptable only for badges, tags, and small labels (always with `letter-spacing: 0.05em` minimum).
- Text on navy backgrounds: `color: #FFFFFF` for headings, `color: rgba(255,255,255,0.85)` for body.
- Text on white backgrounds: `color: #1B2A4A` for headings, `color: #4A5568` for body.

---

## 3. Logo Concepts

### 3.1 Direction A -- The Owl Mark

**Description:** A geometric, minimalist owl face constructed from simple shapes. The head is a rounded shield or inverted triangle form. Two concentric gold circles form the eyes (outer ring `#D4A853`, inner fill `#1B2A4A`). The body below the eyes tapers to a point, creating a shield-like silhouette. No excessive detail -- the owl reads clearly at 32px and 400px alike.

**Paired with wordmark:** "ATHENA STUDIOS" set to the right in Inter Bold 700, all caps, `letter-spacing: 0.1em`. "ATHENA" in navy, "STUDIOS" in Warm Slate at slightly smaller size or lighter weight (500).

**Color variants:**
- Full color: Navy owl + gold eyes on white background
- Reversed: White owl + gold eyes on navy background
- Monochrome: All navy or all white (eyes outlined instead of filled gold)

### 3.2 Direction B -- The Shield Aegis

**Description:** An abstract shield shape inspired by Athena's aegis. The shield has clean, angular edges with subtle rounded corners (4px radius at scale). Inside the shield, two symmetrical arcs create an owl-eye motif. Negative space between the arcs and the shield edge forms a subtle letter "A." The shield outline is 2px stroke navy, with the interior arcs in gold fill.

**Color variants:**
- Full color: Navy shield outline, gold interior arcs, on white
- Reversed: White shield, gold arcs, on navy
- Monochrome: Single color, all strokes

### 3.3 Direction C -- Minimalist Wordmark

**Description:** The words "athena studios" in lowercase Inter Bold 700. The letter "a" in "athena" has a custom modification: the apex of the "a" is extended upward into a small triangular peak, evoking a Greek temple pediment or the tip of a spear. This peak is rendered in gold (`#D4A853`); the rest of the wordmark is Athena Navy. A thin gold horizontal rule (1px, 40px wide) sits beneath the wordmark, centered.

**Variations:**
- Stacked: "athena" on top, "studios" below, both centered
- Inline: "athena studios" on a single line
- Icon only: The custom "a" letterform used as a standalone mark

### 3.4 Logo Specifications

| Context               | Format   | Dimensions            | Min Clear Space      | Notes                              |
|-----------------------|----------|-----------------------|----------------------|------------------------------------|
| Website header        | SVG, PNG | Height: 40px (desktop), 32px (mobile) | 16px all sides | SVG preferred for sharpness        |
| Website header (wide) | SVG, PNG | Height: 48px          | 20px all sides       | For large desktop layouts          |
| Favicon               | ICO, PNG | 32x32, 16x16         | None (fills frame)   | Owl mark or "a" letterform only    |
| Apple Touch Icon      | PNG      | 180x180               | 20px padding inside  | Centered on white or navy square   |
| Social media avatar   | PNG      | 400x400               | 40px padding inside  | Centered mark on navy background   |
| Email signature       | PNG      | Width: 200px max      | 8px all sides        | Inline wordmark, 2x retina export  |
| Print / vector        | SVG, EPS | Scalable              | Minimum 10% of logo width on all sides | CMYK color values required |
| Presentation slide    | SVG, PNG | Height: 60-80px       | 24px all sides       | Bottom-right or top-left placement |
| Open Graph image      | PNG      | 1200x630              | Logo at 200px height, centered or bottom-left | On navy gradient background |

### 3.5 Logo Misuse Rules

- Never rotate the logo.
- Never stretch or distort proportions.
- Never place the full-color logo on a busy photographic background without an overlay.
- Never change the logo colors outside the defined variants.
- Never add drop shadows, glows, or bevels to the logo.
- Never reduce the logo below 24px height for the mark or 100px width for the wordmark.
- Always maintain minimum clear space as defined above.

---

## 4. Imagery Style

### 4.1 Photography

**Subject matter:**
- Real local business owners in their work environment (bakery, dental office, repair shop, salon).
- Warm, natural lighting. No harsh flash. Golden hour or soft indoor light preferred.
- Candid or semi-posed: the subject looks approachable and confident, not stiff.
- Environments should look lived-in and authentic, not sterile studio setups.

**Post-processing treatment:**
- Warm tone shift: increase warmth by 10-15% (subtle, not orange).
- Slight desaturation of blues and greens to keep the warm palette cohesive.
- Soft contrast: lift shadows slightly (`+10-15`), pull highlights down (`-5-10`).
- Subtle vignette (opacity 10-15%) to draw focus to the subject.
- Never apply heavy filters, high-contrast black and white, or trendy color grading.

**Aspect ratios:**
- Hero images: 16:9 or 21:9 (desktop), 4:3 or 1:1 (mobile crop)
- Testimonial portraits: 1:1 (circle-cropped, 120px diameter)
- Case study thumbnails: 3:2
- Team photos: 4:5 (portrait orientation)

### 4.2 Illustrations

**Style:** Flat geometric with subtle implied depth. Think simple shapes with one level of shadow or highlight to give dimension, not full 3D.

**Rules:**
- Use brand colors only: Navy, Gold, White, Warm Slate, Soft Gray. No off-palette colors.
- Stroke weight: 2px consistent across all illustrations.
- Corner radius: 4px on all rectangular shapes. Circles remain perfect circles.
- Shadows on illustration elements: `2px 2px 0px rgba(27, 42, 74, 0.1)` for subtle depth.
- Icons within illustrations: 24x24 base grid, 1.5px stroke, rounded line caps and joins.
- No gradients within illustrations (gradients are for UI only).
- Illustrations should be exportable as SVG for crisp rendering at any size.

**Common illustration subjects:**
- Website mockups floating in space (see 4.3 below).
- Abstract representations of growth (upward arrows, charts, expanding circles).
- Local business icons (storefronts, tools of the trade, location pins).
- Process flows (numbered steps with connecting lines).

### 4.3 Screenshot / Mockup Treatment

**Browser mockup style:**
- Clean, minimal browser chrome: rounded top bar (8px radius), three dots (left), URL bar (centered, light gray fill), no brand-specific browser UI.
- Browser chrome color: `#F7F8FA` (Soft Gray) with `1px solid #E2E8F0` border.

**3D tilt effect:**
- Apply a subtle 3D perspective tilt: `transform: perspective(1200px) rotateY(-5deg) rotateX(2deg);`
- Range: 5-10 degrees on the Y axis, 1-3 degrees on the X axis. Never more than 10 degrees total.
- Tilt direction: left side slightly receding (as if the screen is angled toward the viewer's right).

**Shadow:**
- `box-shadow: 0 25px 50px -12px rgba(27, 42, 74, 0.15), 0 12px 24px -8px rgba(27, 42, 74, 0.08);`
- The mockup should appear to float above the background.

**Background:**
- Place on Soft Gray (`#F7F8FA`) or Light Gold (`#F5E6C8`) background.
- Optionally add faint geometric decorative elements (circles, dots) in the background at 5-8% opacity using Navy or Gold.

**Responsive:**
- Show desktop + mobile side by side for portfolio pieces.
- Mobile mockup: phone frame outline, 2px navy stroke, 24px radius corners, no specific phone brand.

---

## 5. UI Component Direction

### 5.1 Cards

```css
.card {
  background: #FFFFFF;
  border: 1px solid #E2E8F0;
  border-radius: 12px;
  padding: 32px;
  box-shadow: 0 1px 3px rgba(27, 42, 74, 0.06), 0 1px 2px rgba(27, 42, 74, 0.04);
  transition: transform 0.2s ease, box-shadow 0.2s ease;
}

.card:hover {
  transform: translateY(-2px);
  box-shadow: 0 10px 25px rgba(27, 42, 74, 0.08), 0 4px 10px rgba(27, 42, 74, 0.05);
}
```

**Card variants:**
- **Default card:** As above. White background, subtle border.
- **Featured card:** Add `border-left: 3px solid #D4A853` or `border-top: 3px solid #D4A853`.
- **Glass card (on navy backgrounds):**
  ```css
  .card-glass {
    background: rgba(255, 255, 255, 0.08);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 12px;
    padding: 32px;
    color: #FFFFFF;
  }
  ```
- **Stat card:** Centered layout, large number (52px, 700 weight, Navy), label below (14px, Warm Slate), optional gold accent line (2px height, 40px width) between number and label.

### 5.2 Buttons

**Primary button (gold):**
```css
.btn-primary {
  background: #D4A853;
  color: #1B2A4A;
  font-size: 16px;
  font-weight: 600;
  padding: 14px 32px;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.2s ease, transform 0.15s ease;
  letter-spacing: 0.02em;
}
.btn-primary:hover {
  background: linear-gradient(135deg, #D4A853 0%, #E8C373 100%);
  transform: translateY(-1px);
}
.btn-primary:active {
  background: #B8912F;
  transform: translateY(0);
}
.btn-primary:focus-visible {
  outline: 2px solid #D4A853;
  outline-offset: 2px;
}
```

**Secondary button (outlined):**
```css
.btn-secondary {
  background: transparent;
  color: #1B2A4A;
  font-size: 16px;
  font-weight: 600;
  padding: 14px 32px;
  border: 2px solid #1B2A4A;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.2s ease, color 0.2s ease;
}
.btn-secondary:hover {
  background: #1B2A4A;
  color: #FFFFFF;
}
```

**Ghost button (on navy backgrounds):**
```css
.btn-ghost {
  background: transparent;
  color: #D4A853;
  font-size: 16px;
  font-weight: 600;
  padding: 14px 32px;
  border: 2px solid #D4A853;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.2s ease, color 0.2s ease;
}
.btn-ghost:hover {
  background: #D4A853;
  color: #1B2A4A;
}
```

**Button sizes:**
| Size   | Padding       | Font Size | Border Radius |
|--------|---------------|-----------|---------------|
| Small  | 10px 20px     | 14px      | 6px           |
| Medium | 14px 32px     | 16px      | 8px           |
| Large  | 18px 40px     | 18px      | 10px          |

### 5.3 Navigation

**Desktop navigation:**
```css
.nav {
  background: #1B2A4A;
  padding: 0 48px;
  height: 72px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  position: sticky;
  top: 0;
  z-index: 1000;
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  background: rgba(27, 42, 74, 0.95);
}

.nav-link {
  color: rgba(255, 255, 255, 0.8);
  font-size: 15px;
  font-weight: 500;
  text-decoration: none;
  padding: 8px 16px;
  border-radius: 6px;
  transition: color 0.2s ease, background 0.2s ease;
}

.nav-link:hover {
  color: #FFFFFF;
  background: rgba(255, 255, 255, 0.08);
}

.nav-link.active {
  color: #D4A853;
}
```

**Mobile navigation (hamburger):**
- Hamburger icon: three horizontal lines, 2px stroke, 20px wide, white on navy.
- Opens full-screen overlay: `background: rgba(27, 42, 74, 0.98)`.
- Links stacked vertically, centered, 24px font size, 600 weight, 48px vertical spacing.
- Close button: "X" icon, top-right, 24px, white.
- Transition: slide down from top, 0.3s ease-out.

**Sticky behavior:**
- Nav becomes sticky after scrolling past the hero section.
- On scroll, nav gains `box-shadow: 0 2px 8px rgba(27, 42, 74, 0.15)`.
- Background opacity transitions from `0.95` to `0.98` on scroll.

### 5.4 Pricing Cards

**Layout:** Three cards side by side (desktop), stacked (mobile). The recommended/middle card is elevated.

**Standard pricing card:**
```css
.pricing-card {
  background: #FFFFFF;
  border: 1px solid #E2E8F0;
  border-radius: 16px;
  padding: 40px 32px;
  text-align: center;
  transition: transform 0.2s ease, box-shadow 0.2s ease;
}
```

**Recommended pricing card (middle tier):**
```css
.pricing-card.recommended {
  border: 2px solid #D4A853;
  transform: scale(1.05);
  box-shadow: 0 20px 40px rgba(212, 168, 83, 0.15), 0 8px 16px rgba(27, 42, 74, 0.08);
  position: relative;
}

.pricing-card.recommended::before {
  content: "Most Popular";
  position: absolute;
  top: -14px;
  left: 50%;
  transform: translateX(-50%);
  background: #D4A853;
  color: #1B2A4A;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  padding: 6px 20px;
  border-radius: 20px;
}
```

**Pricing card anatomy (top to bottom):**
1. Plan name: 14px, 600 weight, uppercase, Warm Slate, `letter-spacing: 0.05em`
2. Price: 52px, 700 weight, Navy. Currency symbol at 24px, 600 weight, aligned to top.
3. Billing period: 14px, 400 weight, Muted Text (`#718096`), e.g., "/ month"
4. Divider: 1px solid `#E2E8F0`, 40px margin top and bottom
5. Feature list: 16px, 400 weight, Warm Slate. Checkmark icon (`#38A169`) before each item. 12px vertical spacing between items.
6. CTA button: full width, primary gold for recommended, secondary outlined for others. 16px margin top.

### 5.5 Form Inputs

```css
.input {
  width: 100%;
  padding: 14px 16px;
  font-size: 16px;
  font-family: 'Inter', sans-serif;
  color: #1B2A4A;
  background: #FFFFFF;
  border: 1px solid #E2E8F0;
  border-radius: 8px;
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}

.input:focus {
  border-color: #D4A853;
  box-shadow: 0 0 0 3px rgba(212, 168, 83, 0.15);
  outline: none;
}

.input::placeholder {
  color: #A0AEC0;
}

.input-label {
  font-size: 14px;
  font-weight: 600;
  color: #1B2A4A;
  margin-bottom: 6px;
  display: block;
}
```

### 5.6 Testimonial Component

```
+--------------------------------------------------+
|  "Quote text here in 20px, italic if DM Serif,   |
|   regular if Inter, color: #1B2A4A"              |
|                                                    |
|  [Circle Photo 56px]  Name (16px, 600, Navy)     |
|                        Title, Business (14px, Slate)|
|                        [5 gold stars]              |
+--------------------------------------------------+
```

- Card background: `#FFFFFF` or `#F7F8FA`
- Opening quotation mark: large decorative `"` in Gold, 48px, positioned top-left as a decorative accent at 20% opacity.
- Star icons: 16px, filled `#D4A853`, 2px spacing between stars.

### 5.7 Section Spacing

| Element                    | Desktop    | Mobile     |
|----------------------------|-----------|------------|
| Section vertical padding   | 96px      | 64px       |
| Between section heading and content | 48px | 32px  |
| Between cards in a grid    | 32px gap  | 24px gap   |
| Between stacked elements   | 24px      | 20px       |
| Container max-width        | 1200px    | 100% - 32px|
| Container horizontal padding | 48px   | 16px       |

---

## 6. Social Media Specifications

### 6.1 Profile Image

- **Size:** 400x400px
- **Content:** Logo mark (owl or shield) centered on Athena Navy background.
- **Padding:** 80px internal padding from edge to mark.
- **Export:** PNG, no transparency (navy background fills the square).

### 6.2 Cover Images

| Platform  | Dimensions   | Content                                                  |
|-----------|-------------|----------------------------------------------------------|
| LinkedIn  | 1584x396px  | Navy gradient background, logo wordmark left-aligned, tagline right-aligned in Light Gold. Subtle geometric pattern (circles, lines) at 5% opacity. |
| Facebook  | 820x312px   | Same treatment as LinkedIn, adjusted for narrower ratio.  |
| Twitter/X | 1500x500px  | Navy gradient, centered logo, tagline below in 18px white.|

### 6.3 Post Templates

**Quote Post (wisdom/tip sharing):**
- 1080x1080px square
- Navy background with subtle gradient
- Large quotation marks in gold at top-left, 20% opacity
- Quote text: 28px, Inter 600 or DM Serif 400, white, centered
- Attribution line: 16px, gold, below quote
- Logo wordmark: bottom center, 120px wide, white

**Portfolio Showcase Post:**
- 1080x1080px or 1080x1350px
- Top 60%: website screenshot with 3D tilt treatment on Soft Gray background
- Bottom 40%: Navy background with client name (24px, white, 600), result metric (e.g., "+340% traffic" in 36px gold), and logo bottom-right

**Pricing / Offer Post:**
- 1080x1080px
- Navy background
- "Starting at" in 16px, gold, uppercase
- Price in 72px, white, 700 weight
- Service name in 24px, white, 400 weight
- CTA line: "Book a Free Call" in 18px gold with arrow icon
- Logo bottom-center

**Carousel Post (educational):**
- 1080x1350px per slide
- Slide 1 (cover): Navy background, title in 36px white, subtitle in 18px gold
- Slides 2-9 (content): White background, heading in 28px Navy, body in 18px Warm Slate, illustrations in brand colors, slide number indicator (gold dot, current; gray dots, others) bottom-center
- Final slide (CTA): Navy background, CTA text in gold, logo

### 6.4 Email Signature

```
[Logo wordmark, 180px wide, PNG]
---
Name Surname
Title | Athena Studios
phone | email
athenastudios.com
```

- Divider line: 1px solid `#E2E8F0`, 200px wide
- Name: 16px, Inter 600, `#1B2A4A`
- Title and contact: 14px, Inter 400, `#4A5568`
- URL: 14px, Inter 500, `#3182CE`
- Total width: max 400px
- No images other than the logo wordmark

---

## 7. Favicon and App Icons

### 7.1 Favicon

| Format | Size    | Usage                           | Content                                    |
|--------|---------|---------------------------------|--------------------------------------------|
| ICO    | 16x16   | Browser tab (legacy)            | Simplified owl mark or "a" letterform, navy on transparent or white on navy |
| ICO    | 32x32   | Browser tab (standard)          | Same as 16x16, slightly more detail        |
| PNG    | 32x32   | Modern browsers                 | Same as ICO 32x32                          |
| PNG    | 48x48   | Windows taskbar                 | Owl mark on navy square, 4px corner radius |
| SVG    | scalable| Modern browsers (preferred)     | Vector owl mark, navy, on transparent      |

Favicon design notes:
- At 16x16, simplify the mark to its most essential form. For the owl, use just the two eye circles and a V-shape below. For the letterform, use just the "a" with the peak accent.
- Navy mark on transparent background for light browser themes.
- Provide an alternate: white mark on navy square for dark browser themes.

### 7.2 Apple Touch Icon

| Size    | Usage              | Notes                          |
|---------|--------------------|--------------------------------|
| 180x180 | iOS home screen    | Owl mark centered on navy background, 32px padding inside. No transparency (iOS adds its own rounding). |

### 7.3 Android / PWA Icons

| Size    | Usage              | Notes                          |
|---------|--------------------|--------------------------------|
| 192x192 | Android home screen| Owl mark on navy, maskable safe zone (center 60%) |
| 512x512 | PWA splash screen  | Owl mark on navy, high resolution |

### 7.4 Open Graph and Twitter Cards

| Asset        | Size      | Notes                                  |
|--------------|-----------|----------------------------------------|
| og:image     | 1200x630  | Navy gradient bg, logo at 200px height centered, tagline in white below |
| twitter:image| 1200x628  | Same as og:image                       |

---

## 8. Design Principles

### Principle 1: Trust First

Every visual decision must build trust. Local business owners are spending real money and need to feel confident. This means: professional typography, consistent spacing, real photography over stock, prominent testimonials, clear pricing with no hidden elements, and visible social proof. When in doubt between "cool" and "trustworthy," choose trustworthy.

### Principle 2: Clarity Over Decoration

Remove any element that does not serve comprehension or conversion. No decorative flourishes that distract from the message. White space is a feature, not wasted space. Every section has one clear purpose. Every page has one primary CTA. If something can be said in fewer words or shown with a simpler visual, do that.

### Principle 3: Subtle Depth (Antigravity)

Create spatial hierarchy through layering, not through heavy borders or dramatic shadows. Cards float above backgrounds with gentle shadows. Glassmorphism effects are used sparingly on dark backgrounds to create an ethereal, premium feel. Hover states lift elements slightly (`translateY(-2px)`). Backgrounds have subtle gradients that suggest depth. The overall impression is that content exists in a three-dimensional space, but the effect is calm, not dramatic.

### Principle 4: Mobile First

Design for the 375px viewport first, then scale up. Over 60% of local business traffic comes from mobile. Touch targets are minimum 44x44px. Typography scales fluidly between breakpoints. Navigation collapses cleanly. Images are responsive and lazy-loaded. Forms are easy to complete with a thumb. No horizontal scrolling ever.

### Principle 5: Consistency Above Novelty

Every page on the site should feel like it belongs to the same family. Use the defined type scale, color palette, spacing system, and component library without exception. Do not introduce one-off styles or custom components for a single page. If a new pattern is needed, define it here first, then apply it everywhere it is relevant. The brand should be recognizable from any single screenshot.

---

## Appendix A: CSS Custom Properties Reference

For implementation, define these at the `:root` level:

```css
:root {
  /* Primary */
  --color-navy: #1B2A4A;
  --color-gold: #D4A853;
  --color-white: #FFFFFF;

  /* Secondary */
  --color-slate: #4A5568;
  --color-light-gold: #F5E6C8;
  --color-soft-gray: #F7F8FA;
  --color-success: #38A169;
  --color-trust-blue: #3182CE;

  /* Extended */
  --color-deep-navy: #111D35;
  --color-navy-mid: #2D3E6B;
  --color-border: #E2E8F0;
  --color-muted: #718096;
  --color-error: #E53E3E;
  --color-warning: #DD6B20;
  --color-gold-light: #E8C373;
  --color-gold-dark: #B8912F;

  /* Gradients */
  --gradient-hero: linear-gradient(135deg, #1B2A4A 0%, #2D3E6B 100%);
  --gradient-navy-depth: linear-gradient(180deg, #1B2A4A 0%, #111D35 100%);
  --gradient-gold-hover: linear-gradient(135deg, #D4A853 0%, #E8C373 100%);
  --gradient-light-section: linear-gradient(180deg, #FFFFFF 0%, #F7F8FA 100%);

  /* Typography */
  --font-primary: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  --font-serif: 'DM Serif Display', Georgia, 'Times New Roman', serif;
  --font-mono: 'JetBrains Mono', 'SF Mono', 'Fira Code', 'Consolas', monospace;

  /* Spacing */
  --space-xs: 4px;
  --space-sm: 8px;
  --space-md: 16px;
  --space-lg: 24px;
  --space-xl: 32px;
  --space-2xl: 48px;
  --space-3xl: 64px;
  --space-4xl: 96px;

  /* Radii */
  --radius-sm: 6px;
  --radius-md: 8px;
  --radius-lg: 12px;
  --radius-xl: 16px;
  --radius-full: 9999px;

  /* Shadows */
  --shadow-sm: 0 1px 3px rgba(27, 42, 74, 0.06), 0 1px 2px rgba(27, 42, 74, 0.04);
  --shadow-md: 0 4px 12px rgba(27, 42, 74, 0.08), 0 2px 4px rgba(27, 42, 74, 0.04);
  --shadow-lg: 0 10px 25px rgba(27, 42, 74, 0.08), 0 4px 10px rgba(27, 42, 74, 0.05);
  --shadow-xl: 0 20px 40px rgba(27, 42, 74, 0.12), 0 8px 16px rgba(27, 42, 74, 0.06);
  --shadow-float: 0 25px 50px -12px rgba(27, 42, 74, 0.15), 0 12px 24px -8px rgba(27, 42, 74, 0.08);

  /* Transitions */
  --transition-fast: 0.15s ease;
  --transition-base: 0.2s ease;
  --transition-slow: 0.3s ease-out;

  /* Layout */
  --container-max: 1200px;
  --container-padding: 48px;
  --container-padding-mobile: 16px;
  --nav-height: 72px;
  --content-max-width: 680px;
}
```

---

## Appendix B: Accessibility Requirements

- All text must meet WCAG 2.1 AA contrast ratios (4.5:1 for normal text, 3:1 for large text).
- Athena Navy on White: contrast ratio 11.2:1 (passes AAA).
- White on Athena Navy: contrast ratio 11.2:1 (passes AAA).
- Athena Gold on Athena Navy: contrast ratio 4.7:1 (passes AA for large text; use 600+ weight or 18px+ for body).
- Athena Gold on White: contrast ratio 2.4:1 (fails -- never use gold text on white backgrounds for body text; gold on white is acceptable only for decorative/non-essential elements).
- Warm Slate on White: contrast ratio 5.9:1 (passes AA).
- All interactive elements must have visible focus states (defined in button and input specs above).
- Touch targets: minimum 44x44px on mobile.
- Images must have descriptive `alt` text.
- Animations respect `prefers-reduced-motion`: disable `translateY` hover effects and transitions when the user prefers reduced motion.
