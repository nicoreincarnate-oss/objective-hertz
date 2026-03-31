# Athena Studios -- Website Content Brief

Version 1.0 | Last Updated: March 2026

---

## Builder Instructions

This is a complete website content brief for **Athena Studios** (athenastudios.com). It contains every piece of copy, every section layout, every SEO tag, and every behavioral annotation needed to build the full site. Nothing is left ambiguous.

**Companion files in the same directory:**
- **BRAND-IDENTITY.md** -- brand story, voice guidelines, persona profiles, competitive positioning, messaging hierarchy. Read this for tone and language rules.
- **BRAND-KIT.md** -- full color palette (hex, RGB, HSL), typography scale, spacing system, component CSS (buttons, cards, forms, nav, footer), gradients, shadows, animation specs. Read this for every visual decision.

**Tech stack recommendation:**
- Framework: Next.js (preferred) or Astro -- static site generation for speed
- Styling: Tailwind CSS with custom theme derived from BRAND-KIT.md CSS variables
- Fonts: Google Fonts -- Inter (400, 500, 600, 700)
- Responsive breakpoints: 640px (mobile), 768px (tablet), 1024px (desktop), 1280px (wide)

**Implementation rules:**
- All copy is provided -- implement as-written. Do not rewrite, rephrase, or "improve" the text.
- All SEO metadata is provided -- implement exactly as specified.
- All pricing figures are exact -- do not round, change, or omit any price.
- NEVER mention AI, algorithms, autonomous systems, or automation in any visible text. The word "AI" must not appear anywhere on the site except in the service name "24/7 Phone Answering" (which describes the outcome, not the technology).
- The site must feel like a professional design studio, not a tech company. Warm, direct, confident.
- Where items are marked [PLACEHOLDER], implement the structure and visual treatment but use the placeholder text. These will be replaced with real data later.

---

## Site Map

| # | Page | Route | Priority |
|---|------|-------|----------|
| 1 | Home | `/` | Highest -- primary landing page |
| 2 | Services | `/services` | High |
| 3 | Portfolio | `/portfolio` | High |
| 4 | Pricing | `/pricing` | Highest -- key conversion page |
| 5 | About | `/about` | Medium |
| 6 | Contact | `/contact` | High -- conversion endpoint |
| 7 | Blog | `/blog` | Low -- placeholder structure only |

---

## Behavioral Psychology Models (referenced throughout)

These five models are annotated in section notes across every page. The builder should understand them to implement visual hierarchy and emphasis correctly.

1. **Anchoring** -- Show a high reference price first, then the Athena price. The first number the visitor sees sets the mental benchmark. Used on Home, Pricing, and Services pages.
2. **Loss Aversion** -- People fear losing more than they desire gaining. Frame inaction as loss: "every day without a website, you are invisible." Used on Home and Pricing pages.
3. **Social Proof** -- People follow the actions of others. Industry stats, client counts, testimonial frameworks, satisfaction ratings. Used on Home, Portfolio, and Pricing pages.
4. **Paradox of Choice** -- Too many options paralyze. Three tiers maximum, always highlight one as recommended. Used on Pricing and Services pages.
5. **Risk Reversal** -- Remove the perceived risk of purchase. "See your site before you pay" eliminates buyer anxiety. Used on Home, Portfolio, Pricing, and Contact pages.

---

## Page 1: Home (`/`)

This is the primary landing page. Most visitors arrive here first. It must establish credibility, communicate the value proposition, and drive visitors to either Pricing or Contact within 30 seconds.

### SEO

- **Title tag:** Custom Websites for Local Businesses | Athena Studios
- **Meta description:** Professional websites for dentists, plumbers, restaurants, and local businesses. Custom design starting at $299. No templates. Live in days.
- **H1:** (whichever headline option is selected from the hero)
- **Target key phrases:** custom websites for local businesses, affordable web design, professional website design, small business website, local business website

---

### Section 1: Hero

**Component type:** Full-width hero with gradient background (Hero Gradient from BRAND-KIT.md), centered text, two CTA buttons.

**Desktop layout:** Centered headline and subheadline with max-width 800px. Two buttons side by side below. Trust signals as a horizontal row beneath the buttons.

**Mobile layout:** Same centered stack. Buttons stack vertically. Trust signals become a single column.

**Headline options (builder picks one, or present to user for selection):**

- **Option A (benefit-led):** "Your business deserves a website that works as hard as you do"
  - Rationale: Leads with empathy. Speaks to the owner's work ethic. Warm and personal.

- **Option B (price-anchored):** "Professional websites for local businesses. Starting at $299."
  - Rationale: Leads with the two key value props -- quality and price. Direct. Sets the anchor immediately.

- **Option C (disruption-led):** "Professional websites shouldn't cost $5,000."
  - Rationale: Opens with a market truth that every local business owner has felt. Polarizing and bold. Highest attention-grabbing potential.

**Subheadline:**
"Custom-designed websites for dentists, plumbers, restaurants, salons, and local businesses worldwide. No templates. No surprises. Just a site that gets you customers."

**Primary CTA button:** "See Our Work" (links to `/portfolio`)
- Alternative: "Get Started -- $299" (links to `/contact`)
- Builder note: If Option B headline is selected, use "See Our Work" as primary CTA (avoid repeating the price). If Option A or C is selected, use "Get Started -- $299" as primary.

**Secondary CTA button:** "View Pricing" (links to `/pricing`)
- Styled as ghost/outline button per BRAND-KIT.md secondary button spec.

**Trust signals (horizontal row below CTAs):**
- "500+ local businesses served" [PLACEHOLDER -- replace with real number when available]
- "Custom design, not templates"
- "Live in days, not months"

**Psychology annotations:**
- Anchoring: Option B and C both set a price anchor. Option C anchors against $5,000 (competitor frame).
- Loss Aversion: The subheadline implies "without us, you are using templates or paying too much."

---

### Section 2: Problem

**Component type:** Centered headline, short paragraph, three icon-text cards in a row.

**Desktop layout:** Headline centered. Body paragraph centered below (max-width 650px). Three cards in a horizontal row with equal spacing.

**Mobile layout:** Headline and paragraph stack. Cards stack vertically.

**Headline:** "Your competitors have websites. Do you?"

**Body text:**
"97% of consumers search online before visiting a local business. If you are not there, you are invisible to every potential customer who searches before they buy. Your reputation, your reviews, your years of experience -- none of it matters if people cannot find you."

**Pain point cards (3 cards, each with an icon placeholder and text):**

1. **Icon:** Search/magnifying glass
   **Text:** "Losing customers to competitors with websites"

2. **Icon:** Phone/clock
   **Text:** "Answering the same questions by phone that a website would handle"

3. **Icon:** Map pin/location
   **Text:** "Missing out on 'near me' searches that drive foot traffic"

**Psychology annotations:**
- Loss Aversion: The entire section is framed around what the visitor is losing by not having a site. "Invisible," "losing customers," "missing out" -- all loss language.
- Social Proof: The 97% statistic (BrightLocal) provides third-party validation.

---

### Section 3: How It Works

**Component type:** Numbered step cards (3 steps), horizontal layout with connecting line or arrows.

**Desktop layout:** Three step cards side by side with step numbers (1, 2, 3) prominently displayed. Optional connecting line between them.

**Mobile layout:** Steps stack vertically.

**Headline:** "Three steps to your new website"

**Step 1:**
- **Number:** 1
- **Title:** "Tell us about your business"
- **Description:** "We learn what makes you different. Your services, your customers, your story."

**Step 2:**
- **Number:** 2
- **Title:** "We design your site"
- **Description:** "A custom website built specifically for your business. Not a template with your logo swapped in."

**Step 3:**
- **Number:** 3
- **Title:** "Go live and get customers"
- **Description:** "Your site launches. Your phone rings more. That is the goal."

**CTA button:** "Start Your Project -- $299" (links to `/contact`)

**Psychology annotations:**
- Paradox of Choice: Three steps. Simple. No branching paths or complex decision trees. Reduces perceived effort.
- Risk Reversal: Step 3 focuses on the outcome (customers), not the deliverable (website).

---

### Section 4: Pricing Preview

**Component type:** Three pricing cards in a row with the center card elevated/highlighted as recommended.

**Desktop layout:** Three cards side by side. Center card has a "Recommended" badge, slightly larger or elevated with a gold border per BRAND-KIT.md card styles.

**Mobile layout:** Cards stack vertically. Recommended card appears first or is visually distinct.

**Section background:** Soft Gray (`#F7F8FA`) or Light Gold (`#F5E6C8`) to differentiate from white sections above.

**Headline:** "Transparent pricing. No surprises."

**Card 1: Landing Page -- $149**
- Description: "One page. One goal. Perfect for a specific campaign or service."
- CTA: "Learn More" (links to `/pricing`)

**Card 2: Professional Website -- $299 [RECOMMENDED badge]**
- Description: "5 custom pages. Everything a local business needs."
- CTA: "Get Started" (links to `/contact`)

**Card 3: Premium Package -- Custom Quote**
- Description: "For businesses that need more. E-commerce, booking systems, multi-location."
- CTA: "Contact Us" (links to `/contact`)

**Anchoring note (displayed below the cards):**
"The average agency charges $3,000-$10,000 for what we deliver at $299."

**CTA link:** "See full pricing details" (links to `/pricing`)

**Psychology annotations:**
- Anchoring: The note below the cards sets the competitor price as the reference point. $299 feels like a fraction of the "normal" price.
- Paradox of Choice: Three options only. The recommended badge guides the decision.

---

### Section 5: Social Proof

**Component type:** Industry showcase cards (3) + horizontal stats bar.

**Desktop layout:** Three portfolio-preview cards in a row showing template screenshots. Below: a full-width stats bar with four metrics.

**Mobile layout:** Cards stack vertically or horizontal scroll. Stats bar becomes a 2x2 grid.

**Headline:** "Built for businesses like yours"

**Industry showcase cards (3):**

1. **Industry:** Dental Clinic
   **Visual:** Screenshot mockup of dental website template (use Antigravity float treatment from BRAND-KIT.md)
   **Caption:** "Clean, trustworthy design with online booking"

2. **Industry:** Plumbing Company
   **Visual:** Screenshot mockup of plumber website template
   **Caption:** "Service area map, reviews, emergency contact"

3. **Industry:** Restaurant
   **Visual:** Screenshot mockup of restaurant website template
   **Caption:** "Menu, gallery, reservations, atmosphere"

**Stats bar (horizontal, full-width, navy background with white text):**
- "[X] websites delivered" [PLACEHOLDER -- replace with real number when available]
- "48-hour average delivery"
- "$299 starting price"
- "4.9/5 client satisfaction" [PLACEHOLDER -- replace with real rating when available]

**Builder note:** All specific numbers marked [PLACEHOLDER] should be implemented with the placeholder text visible. Structure the component so numbers can be easily updated later (ideally as props or config values, not hardcoded in JSX).

**Psychology annotations:**
- Social Proof: Client count, satisfaction rating, and industry-specific examples all build trust through evidence of others.

---

### Section 6: Final CTA

**Component type:** Full-width banner with navy gradient background, centered text, single CTA button.

**Desktop layout:** Centered headline, body paragraph, and CTA button. Max-width 700px for text.

**Mobile layout:** Same stack, full-width padding.

**Headline:** "Ready to get found online?"

**Body text:**
"Every day without a website is a day your competitors get the customers who should be yours."

**CTA button:** "Get Started Today" (links to `/contact`)

**Risk reversal line (below button, smaller text):**
"See a preview of your site before you pay a cent."

**Psychology annotations:**
- Loss Aversion: "Every day without a website is a day your competitors get the customers who should be yours." Direct loss framing.
- Risk Reversal: The preview offer eliminates the fear of paying for something sight-unseen.

---

## Page 2: Services (`/services`)

### SEO

- **Title tag:** Web Design & Marketing Services | Athena Studios
- **Meta description:** Custom website design, landing pages, managed hosting, and marketing services for local businesses. Transparent pricing. No contracts.
- **H1:** (whichever headline option is selected from the hero)
- **Target key phrases:** web design services, local business marketing, managed website hosting, landing page design, business website services

---

### Section 1: Hero

**Component type:** Page hero with gradient background, centered text.

**Headline options:**
- **Option A:** "Everything your business needs online"
- **Option B:** "Web design, hosting, and marketing -- all in one place"
- **Option C:** "From website to customers. We handle it all."

**Subheadline:**
"Professional web design and digital marketing services built for local businesses. Clear pricing. No contracts. No jargon."

---

### Section 2: Service -- Custom Website Design

**Component type:** Two-column layout. Left: text content. Right: visual placeholder (screenshot or illustration).

**Title:** "Custom Website Design"

**Description:**
"A professional website designed specifically for your business. Not a template -- every page, every section, every word is crafted for you. Mobile-responsive, SEO-ready, and built to convert visitors into customers."

**What is included (bulleted list):**
- 5 custom-designed pages
- Mobile-responsive design
- Contact form with email notification
- Google Maps integration
- Basic SEO setup (meta tags, sitemap)
- Social media links
- Analytics tracking (Google Analytics 4)

**Price:** "Starting at $299"

**CTA button:** "Get Your Website" (links to `/contact`)

---

### Section 3: Service -- Landing Pages

**Component type:** Two-column layout (reversed direction from above for visual variety).

**Title:** "Landing Pages"

**Description:**
"One page with one goal -- drive calls, bookings, or sign-ups for a specific service or promotion."

**What is included (bulleted list):**
- Single custom-designed page
- Mobile-responsive layout
- CTA-optimized design
- Contact form or click-to-call button

**Price:** "$149"

**CTA button:** "Order a Landing Page" (links to `/contact`)

---

### Section 4: Service -- Managed Hosting

**Component type:** Two-column layout.

**Title:** "Managed Hosting"

**Description:**
"Your site stays fast, secure, and online. We handle the technical stuff so you do not have to think about it."

**What is included (bulleted list):**
- SSL certificate (HTTPS)
- Daily backups
- Uptime monitoring
- Security updates and patches
- Performance optimization

**Price:** "$52/month"

**CTA button:** "Add Hosting" (links to `/contact`)

---

### Section 5: Service -- 24/7 Phone Answering

**Component type:** Two-column layout (reversed).

**Title:** "24/7 Phone Answering"

**Description:**
"Never miss a call again. An intelligent receptionist answers your phone around the clock -- books appointments, answers common questions, and sends you a summary."

**What is included (bulleted list):**
- 24/7 call answering
- Appointment booking
- FAQ responses for your business
- Call summaries sent to you
- Custom greeting with your business name

**Price:** "$398/month"

**CTA button:** "Never Miss a Call" (links to `/contact`)

**Builder note:** Do NOT use the words "AI," "artificial intelligence," "automated," or "bot" anywhere in this section. The service is described by its outcome (phone answering, appointment booking), not its technology.

---

### Section 6: Service -- Email Marketing

**Component type:** Two-column layout.

**Title:** "Email Marketing"

**Description:**
"Stay in touch with your customers. We set up and manage email campaigns that bring people back."

**What is included (bulleted list):**
- Email template design
- Monthly campaign management
- Subscriber list management
- Performance reports

**Price:** "Custom quote"

**CTA button:** "Ask About Marketing" (links to `/contact`)

---

### Section 7: CTA Banner

**Component type:** Full-width CTA banner (same pattern as Home page final CTA).

**Headline:** "Not sure what you need?"

**Body text:** "Tell us about your business. We will recommend the right services for your goals and budget."

**CTA button:** "Contact Us" (links to `/contact`)

---

## Page 3: Portfolio (`/portfolio`)

### SEO

- **Title tag:** Our Work -- Website Portfolio | Athena Studios
- **Meta description:** See real websites we have built for dentists, plumbers, restaurants, and local businesses. Custom design starting at $299.
- **H1:** (whichever headline option is selected from the hero)
- **Target key phrases:** web design portfolio, local business website examples, custom website examples, small business web design

---

### Section 1: Hero

**Component type:** Page hero with gradient background, centered text.

**Headline options:**
- **Option A:** "Websites we have built for businesses like yours"
- **Option B:** "See what $299 looks like"
- **Option C:** "Real websites. Real businesses. Real results."

---

### Section 2: Portfolio Grid

**Component type:** Three showcase cards in a responsive grid. Each card uses the Antigravity float treatment (translateY hover effect, subtle shadow) from BRAND-KIT.md.

**Desktop layout:** Three cards in a row.

**Mobile layout:** Single column stack.

**Card 1:**
- **Industry:** Dental Clinic
- **Screenshot:** [Use dental template mockup]
- **Location:** [PLACEHOLDER -- e.g., "Toronto, Canada"]
- **Key features:** Online booking, patient FAQ, credentials display, mobile-responsive
- **Link text:** "View Live Site" [PLACEHOLDER -- link to template demo or # until live]

**Card 2:**
- **Industry:** Plumbing Company
- **Screenshot:** [Use plumber template mockup]
- **Location:** [PLACEHOLDER -- e.g., "Chicago, USA"]
- **Key features:** Service area map, Google reviews integration, emergency contact, click-to-call
- **Link text:** "View Live Site" [PLACEHOLDER]

**Card 3:**
- **Industry:** Restaurant
- **Screenshot:** [Use restaurant template mockup]
- **Location:** [PLACEHOLDER -- e.g., "Mexico City, Mexico"]
- **Key features:** Menu display, photo gallery, reservation form, event booking
- **Link text:** "View Live Site" [PLACEHOLDER]

**Psychology annotations:**
- Social Proof: Showing real work for real industries builds trust. The visitor sees themselves in one of these examples.
- Anchoring: If Option B headline is selected ("See what $299 looks like"), the price anchor is set before the visitor evaluates the quality.

---

### Section 3: Industries We Serve

**Component type:** Grid of industry icons/labels. 4 columns on desktop, 3 on tablet, 2 on mobile.

**Headline:** "We build for:"

**Industry list (each with a simple icon placeholder and label):**
1. Dentists
2. Plumbers
3. Restaurants
4. Salons
5. Contractors
6. Electricians
7. Chiropractors
8. Auto Repair
9. Landscaping
10. Fitness Studios
11. Law Offices
12. Real Estate

**Below the grid:**
"Don't see your industry? We build for any local business."

---

### Section 4: CTA Banner

**Component type:** Full-width CTA banner with navy background.

**Headline:** "Want to see what your site could look like?"

**Body text:**
"We will design a free preview page for your business -- no commitment."

**CTA button:** "Get Your Free Preview" (links to `/contact`)

**Psychology annotations:**
- Risk Reversal: Free preview eliminates purchase anxiety entirely. The visitor risks nothing.

---

## Page 4: Pricing (`/pricing`)

This is the key conversion page. Every element is designed to reduce friction and drive the visitor to contact. The anchoring strategy is critical here.

### SEO

- **Title tag:** Pricing -- Professional Websites from $149 | Athena Studios
- **Meta description:** Transparent website pricing. Landing pages from $149. Professional 5-page websites from $299. No hidden fees. No contracts. See what is included.
- **H1:** (whichever headline option is selected from the hero)
- **Target key phrases:** website pricing, affordable web design pricing, how much does a website cost, local business website cost, cheap professional website

---

### Section 1: Hero

**Component type:** Page hero with gradient background, centered text.

**Headline options:**
- **Option A:** "Simple pricing. No surprises."
- **Option B:** "You shouldn't need a finance degree to buy a website"
- **Option C:** "Professional websites. Starting at $149."

---

### Section 2: Pricing Table -- Three Tiers

**Component type:** Three pricing cards side by side. Center card is the recommended tier -- visually elevated, gold border or badge, slightly larger.

**Desktop layout:** Three cards in a row with equal width. Center card elevated.

**Mobile layout:** Cards stack vertically. Recommended card appears first or with distinct visual treatment.

---

**Tier 1: Landing Page -- $149**

- **Price display:** "$149" (large) + "one-time" (small, below)
- **Badge:** None
- **Included features (checkmark list):**
  - 1 custom-designed page
  - Mobile-responsive layout
  - Contact form or click-to-call
  - Basic SEO setup
  - Delivered in 24-48 hours
- **Best for:** "Specific promotions, single-service businesses, event pages"
- **CTA button:** "Get a Landing Page" (links to `/contact` with pre-selected "Landing page" in dropdown)

---

**Tier 2: Professional Website -- $299 [RECOMMENDED]**

- **Price display:** "$299" (large) + "one-time" (small, below)
- **Badge:** "RECOMMENDED" or "Most Popular" -- gold background, white text
- **Included features (checkmark list):**
  - 5 custom-designed pages (Home, About, Services, Contact, +1)
  - Mobile-responsive design
  - Contact form + Google Maps
  - Full SEO setup (meta tags, sitemap, schema markup)
  - Social media integration
  - Analytics tracking (Google Analytics 4)
  - Delivered in days
- **Best for:** "Most local businesses"
- **CTA button:** "Get Started -- $299" (links to `/contact` with pre-selected "New website" in dropdown)

---

**Tier 3: Premium Package -- Custom Quote**

- **Price display:** "Custom" (large) + "quote" (small, below)
- **Badge:** None
- **Included features (checkmark list):**
  - Everything in Professional, plus:
  - Additional pages (6-15)
  - Online booking/scheduling integration
  - E-commerce (up to 50 products)
  - Blog setup and design
  - Multi-location support
  - Priority support
- **Best for:** "Growing businesses, multi-service companies, multi-location operations"
- **CTA button:** "Request a Quote" (links to `/contact`)

**Psychology annotations:**
- Paradox of Choice: Three tiers only. Recommended badge guides the undecided.
- Anchoring: The Premium tier (custom quote) implies high value, making $299 feel like a clear deal.

---

### Section 3: Comparison Table -- "How we compare"

**Component type:** Responsive comparison table with 5 columns. Athena Studios column highlighted.

**Headline:** "How we compare"

**Table structure:**

| Factor | Athena Studios | Wix / Squarespace | Freelancer (avg) | Agency (mid-market) |
|---|---|---|---|---|
| Price (5-page site) | **$299 one-time** | $0 build / $16-45/mo | $500-$2,500 | $3,000-$10,000+ |
| Customization | Fully custom | Template-only | Variable | Fully custom |
| Delivery time | Days | Self-service (days-weeks) | 2-8 weeks | 6-16 weeks |
| Ongoing support | Included | Self-service only | Often none | Retainer required |
| Quality control | Studio standard | Template quality | Inconsistent | High but slow |

**Desktop layout:** Full table visible. Athena Studios column has a gold top border or highlighted background.

**Mobile layout:** Comparison cards (one per competitor) or horizontal scroll table. Athena Studios always visible.

**Psychology annotations:**
- Anchoring: The competitor prices ($3,000-$10,000 for agencies) appear in the same visual field as $299. This is the primary anchoring mechanism on the entire site.
- Social Proof: "Studio standard" quality language implies professional process.

---

### Section 4: What Is Always Included

**Component type:** Grid of feature items with checkmark icons. 2 columns on desktop, 1 on mobile.

**Headline:** "Included with every website"

**Items (each with a green checkmark icon):**
1. Custom design (never a template)
2. Mobile-responsive layout
3. Contact form
4. Basic SEO setup
5. Google Analytics setup
6. 30 days of free edits after launch

---

### Section 5: FAQ

**Component type:** Accordion/collapsible FAQ list. Each question expands to reveal the answer.

**Headline:** "Frequently asked questions"

**Q: "Is $299 really the full price?"**
A: "Yes. $299 covers the complete design and build of your 5-page website. There are no hidden fees, setup costs, or surprise charges. Hosting is separate at $52/month if you would like us to manage it."

**Q: "How can you charge so much less than other agencies?"**
A: "We have streamlined our design process to eliminate the overhead that makes agencies expensive -- no account managers, no long meetings, no months of back-and-forth. You get the same quality result in a fraction of the time."

**Q: "What if I don't like the design?"**
A: "We offer 30 days of free revisions after your site launches. If you are not happy with the direction during the build, we will adjust until it is right."

**Q: "Do I need to provide anything?"**
A: "Just tell us about your business -- your services, what makes you different, and any photos or content you would like included. We handle the rest."

**Q: "Can I see an example before I commit?"**
A: "Yes. We will design a free preview page for your business before you pay anything. If you like it, we continue. If not, no charge."

**Q: "What about hosting?"**
A: "Hosting keeps your site online, fast, and secure. We offer managed hosting at $52/month which includes SSL, backups, security updates, and uptime monitoring. You can also host elsewhere if you prefer."

**Q: "Do you work with businesses outside the US?"**
A: "We work with local businesses worldwide. Our clients include businesses in North America, Europe, Latin America, and Asia-Pacific."

**Psychology annotations:**
- Risk Reversal: The FAQ directly addresses every common objection -- price transparency, quality assurance, revision policy, free preview.
- Anchoring: The "How can you charge so much less" question reinforces the price differential with agencies.

---

### Section 6: CTA Banner

**Component type:** Full-width CTA banner.

**Headline:** "Still have questions? Let's talk."

**CTA buttons (side by side):**
- "Contact Us" (links to `/contact`) -- secondary/outline style
- "Get Started -- $299" (links to `/contact`) -- primary gold style

---

## Page 5: About (`/about`)

### SEO

- **Title tag:** About Athena Studios | Web Design for Local Businesses
- **Meta description:** Athena Studios builds custom websites for local businesses worldwide. Craft, transparency, and fair pricing. Learn about our studio and process.
- **H1:** (whichever headline option is selected from the hero)
- **Target key phrases:** about athena studios, web design studio, local business web design company, affordable web design agency

---

### Section 1: Hero

**Component type:** Page hero with gradient background, centered text.

**Headline options:**
- **Option A:** "The studio behind your website"
- **Option B:** "We believe every local business deserves a great website"
- **Option C:** "Craft, transparency, and fair pricing"

---

### Section 2: Brand Story

**Component type:** Two-column layout. Left: text content. Right: abstract brand visual or workspace photo placeholder.

**Body text (2 paragraphs):**

"Professional web design has a pricing problem. Agencies charge $3,000 to $10,000 because their overhead demands it -- account managers, long meetings, months of revisions. Freelancers charge less but deliver inconsistently and go dark mid-project. Template builders hand you a generic layout and call it custom. Local businesses -- the dentist, the plumber, the restaurant that has been a neighborhood staple for twenty years -- deserve better than those options."

"Athena Studios was built to close that gap. We built a studio from the ground up with one rule: every client gets a custom, professional website at a price that makes sense for a small business. Not a template with a logo swapped in. Not a bargain package that takes four months. A real website, built for your specific business, live in days. We work with businesses worldwide -- from a dental clinic in Toronto to a plumbing company in Melbourne to a restaurant in Mexico City."

---

### Section 3: Values

**Component type:** Five value cards in a grid. 3 columns on desktop (2+3 or 3+2 row), stacked on mobile. Each card has an icon placeholder, title, and description.

**Headline:** "What we stand for"

**Value 1: Craft and Quality**
- Icon: [Pencil/ruler or quality mark]
- Description: "Every website is built for the specific business it represents. No templates. Every layout, every line of copy, every image choice is made for this client."

**Value 2: Radical Transparency**
- Icon: [Eye or open book]
- Description: "We show prices before anyone asks. We explain what is included and what is not. If something will not work for your business, we say so."

**Value 3: Accessibility**
- Icon: [Key or unlock]
- Description: "Professional web design should not require a $5,000 budget. Every local business deserves the same quality online presence as the corporate chain down the street."

**Value 4: Results Over Aesthetics**
- Icon: [Phone ringing or chart trending up]
- Description: "A beautiful website that does not convert is a decoration. We design for calls, bookings, and form submissions first -- then refine the aesthetics around that."

**Value 5: Speed and Reliability**
- Icon: [Clock or lightning bolt]
- Description: "Local businesses lose money every day they are offline. Fast delivery is a commitment, not a marketing claim. Once live, your site stays live."

---

### Section 4: How We Work

**Component type:** Four-step vertical timeline or horizontal process flow.

**Desktop layout:** Horizontal process flow with connecting lines.

**Mobile layout:** Vertical timeline.

**Headline:** "Our process is simple"

**Step 1: Discovery**
"We learn about your business, your customers, and your goals."

**Step 2: Design**
"We build your site from scratch, designed for your specific business."

**Step 3: Review**
"You see the site and request any changes. 30 days of free edits."

**Step 4: Launch**
"Your site goes live. We set up analytics so you can see results."

---

### Section 5: Worldwide

**Component type:** Centered text block with optional map illustration or globe icon.

**Headline:** "We work with businesses worldwide"

**Body text:**
"Our clients span North America, Europe, Latin America, and Asia-Pacific. Whether you run a dental clinic in Toronto, a plumbing company in Sydney, or a restaurant in Mexico City -- if you serve local customers, we build for you. Any country. Any industry. Any language."

---

### Section 6: CTA Banner

**Component type:** Full-width CTA banner.

**Headline:** "Ready to work with us?"

**CTA button:** "Get Started" (links to `/contact`)

---

## Page 6: Contact (`/contact`)

### SEO

- **Title tag:** Contact Athena Studios | Get Your Website Started
- **Meta description:** Tell us about your business and get a custom website quote within 24 hours. No consultation fee. No commitment.
- **H1:** (whichever headline option is selected from the hero)
- **Target key phrases:** contact web designer, get a website quote, custom website inquiry, web design consultation

---

### Section 1: Hero

**Component type:** Page hero with gradient background, centered text. Shorter than other pages -- transitions quickly to the form.

**Headline options:**
- **Option A:** "Let's build your website"
- **Option B:** "Get in touch"
- **Option C:** "Ready to get started?"

**Subheadline:**
"Tell us about your business and we will get back to you within 24 hours."

---

### Section 2: Contact Form

**Component type:** Two-column layout. Left: the form. Right: contact information and FAQ mini-section.

**Desktop layout:** Form takes 60% width (left). Contact info and FAQ take 40% (right).

**Mobile layout:** Form first (full width), then contact info and FAQ below.

**Form fields:**

1. **Name** -- text input, required
   - Label: "Your name"
   - Placeholder: "Maria Santos"

2. **Email** -- email input, required
   - Label: "Email address"
   - Placeholder: "maria@example.com"

3. **Phone** -- tel input, optional
   - Label: "Phone number (optional)"
   - Placeholder: "+1 (555) 000-0000"

4. **Business name** -- text input, required
   - Label: "Business name"
   - Placeholder: "Santos Dental Clinic"

5. **Business type / industry** -- select dropdown, required
   - Label: "What type of business?"
   - Options: Dentist, Plumber, Restaurant, Salon, Contractor, Electrician, Chiropractor, Auto Repair, Landscaping, Fitness Studio, Law Office, Real Estate, Other

6. **What do you need?** -- select dropdown, required
   - Label: "What do you need?"
   - Options: New website ($299), Landing page ($149), Website redesign, Managed hosting ($52/mo), 24/7 Phone Answering ($398/mo), Email marketing, Other
   - Builder note: If the visitor arrives from a pricing tier CTA, pre-select the appropriate option via URL parameter (e.g., `/contact?service=new-website`).

7. **Tell us about your business** -- textarea, optional
   - Label: "Tell us about your business (optional)"
   - Placeholder: "What services do you offer? What makes your business different? Any specific features you need on your website?"

8. **Submit button:** "Send Message" -- primary gold button, full width on mobile

**Form behavior:**
- Submit to email or webhook (Formspree, Netlify Forms, or similar)
- Show success message after submission: "Thank you. We will be in touch within 24 hours."
- Basic client-side validation on required fields

---

### Section 3: Alternative Contact (right column on desktop, below form on mobile)

**Email:** hello@athenastudios.com [PLACEHOLDER -- replace with real email when ready]

**Response time:** "We respond within 24 hours."

---

### Section 4: FAQ Mini-Section (right column on desktop, below alternative contact on mobile)

**Q: "What happens after I submit?"**
A: "We will review your information and reply with questions or a proposal within 24 hours."

**Q: "Is there a consultation fee?"**
A: "No. Our initial conversation is always free."

---

## Page 7: Blog (`/blog`)

### SEO

- **Title tag:** Blog -- Web Design Tips for Local Businesses | Athena Studios
- **Meta description:** Web design tips, marketing guides, and case studies for local businesses. Learn how to get more customers online.
- **H1:** "Blog"
- **Target key phrases:** web design blog, local business marketing tips, small business website tips

---

### Blog Listing Page Structure

**Component type:** Card grid for blog post previews. 3 columns on desktop, 2 on tablet, 1 on mobile.

**Each blog card contains:**
- Featured image (placeholder)
- Title (linked to post)
- Excerpt (2-3 lines, truncated)
- Date published
- Estimated read time
- Category tag

**Categories (for filtering):**
- Web Design Tips
- Marketing for Local Businesses
- Case Studies
- Industry Guides

**Sidebar or filter bar:** Category filter (buttons or dropdown). On desktop: sidebar. On mobile: horizontal filter bar above cards.

**Builder note:** No blog posts need to be created at launch. Build the listing page template and the individual post template. Use 3 placeholder cards with lorem ipsum titles to demonstrate the layout.

---

### Individual Blog Post Template

**Layout:**
- Hero image (full-width, optional)
- Title (H1)
- Author name + date + read time
- Body content area (prose styling -- good line height, max-width 720px, centered)
- Related posts section (3 cards at bottom)
- CTA banner: "Need a website for your business?" with button linking to `/contact`

---

## Global Elements

These elements appear on every page.

---

### Header / Navigation

**Component type:** Sticky header with backdrop-blur effect on scroll.

**Desktop layout:**
- Logo (left-aligned)
- Navigation links (center or right): Home, Services, Portfolio, Pricing, About, Contact
- CTA button (far right): "Get Started" -- gold button, links to `/contact`

**Mobile layout:**
- Logo (left-aligned)
- Hamburger menu icon (right-aligned)
- Full-screen overlay menu when hamburger is tapped: all nav links stacked vertically, CTA button at bottom

**Behavior:**
- Sticky on scroll with `backdrop-filter: blur(12px)` and semi-transparent background
- Current page link is visually distinguished (gold underline or bold weight)
- Logo links to Home (`/`)

---

### Footer

**Component type:** Multi-column footer on navy background.

**Desktop layout:** 4 columns.

**Column 1: Brand**
- Logo
- Tagline: "Custom websites. Honest prices."

**Column 2: Quick Links**
- Services
- Pricing
- Portfolio
- About
- Contact
- Blog

**Column 3: Contact**
- Email: hello@athenastudios.com [PLACEHOLDER]
- Phone: [PLACEHOLDER]

**Column 4: Social**
- LinkedIn [PLACEHOLDER URL]
- Instagram [PLACEHOLDER URL]
- Facebook [PLACEHOLDER URL]

**Bottom bar (full width, below columns):**
- Left: "2026 Athena Studios. All rights reserved."
- Center: "Built with craft in Baja California Sur, Mexico"
- Right: Privacy Policy | Terms of Service [PLACEHOLDER -- link to placeholder pages]

---

### Cookie Banner

**Component type:** Fixed bar at the bottom of the viewport. Appears on first visit.

**Text:** "We use cookies to improve your experience."

**Buttons:**
- "Accept" -- small gold button
- "Decline" -- text link or ghost button

**Link:** "Privacy Policy" (links to privacy policy placeholder page)

**Behavior:** Sets a cookie or localStorage flag. Does not reappear after the visitor makes a choice. Default to declined if no interaction (privacy-first).

---

### 404 Page

**Route:** Any unmatched route.

**Component type:** Centered text on gradient background.

**Headline:** "This page doesn't exist. But your website could."

**CTA buttons:**
- "Go Home" (links to `/`) -- secondary/outline style
- "Get Started" (links to `/contact`) -- primary gold style

---

## Technical Requirements

### Performance
- Target Lighthouse score: 90+ on all four categories (Performance, Accessibility, Best Practices, SEO)
- All images optimized as WebP with fallback (JPEG/PNG)
- Lazy loading on all images below the fold
- Critical CSS inlined for above-the-fold content

### SEO Infrastructure
- `sitemap.xml` generated automatically
- `robots.txt` allowing all crawlers
- Open Graph tags on every page (title, description, image)
- Twitter Card tags on every page
- `schema.org` LocalBusiness structured data on the Home page
- Canonical URLs on every page

### Analytics
- Google Analytics 4 placeholder (tag ID as environment variable or config value)
- Event tracking on all CTA button clicks (event name: `cta_click`, parameters: `page`, `button_label`, `destination`)

### Forms
- Contact form submits to email or webhook (Formspree, Netlify Forms, or equivalent)
- Client-side validation on required fields
- Honeypot field for spam prevention (hidden field, reject if filled)
- Success/error state handling with user-facing messages

### Images
- All images served as WebP with JPEG fallback
- Lazy loading via `loading="lazy"` attribute
- Responsive images with `srcset` where applicable
- Portfolio mockups use the Antigravity float treatment (see BRAND-KIT.md Section 7.2)

### Animations
- Card hover: `translateY(-2px)` with `transition: transform 0.2s ease`
- Section fade-in on scroll: `opacity 0 -> 1` with `translateY(20px -> 0)` as section enters viewport
- Button hover: subtle scale (`transform: scale(1.02)`) and color shift per BRAND-KIT.md button specs
- All animations must respect `prefers-reduced-motion: reduce` -- disable transforms and opacity transitions when this media query matches

### Hosting Recommendation
- **Next.js:** Deploy to Vercel (zero-config, automatic builds from Git)
- **Astro:** Deploy to Netlify (similar zero-config experience)
- Custom domain: athenastudios.com [PLACEHOLDER -- configure when domain is purchased]

### Accessibility
- All images have descriptive `alt` text
- All form inputs have associated `<label>` elements
- Color contrast ratios meet WCAG AA (4.5:1 for body text, 3:1 for large text) -- verified by BRAND-KIT.md palette
- Keyboard navigation works for all interactive elements
- Focus states are visible on all interactive elements
- Skip-to-content link at the top of every page

---

## Content Inventory Summary

| Page | Sections | Headline Options | CTAs | FAQ Items |
|------|----------|-----------------|------|-----------|
| Home | 6 | 3 | 6 | 0 |
| Services | 7 | 3 | 6 | 0 |
| Portfolio | 4 | 3 | 2 | 0 |
| Pricing | 6 | 3 | 5 | 7 |
| About | 6 | 3 | 1 | 0 |
| Contact | 4 | 3 | 1 | 2 |
| Blog | 2 (templates) | 0 | 1 | 0 |
| **Total** | **35** | **18** | **22** | **9** |

---

## Placeholder Registry

All items marked [PLACEHOLDER] in this document. The builder should implement the structure and display the placeholder text. Update with real data when available.

| Item | Location | Current Value | Notes |
|------|----------|---------------|-------|
| Client count | Home hero, Home stats bar | "500+ local businesses served" / "[X] websites delivered" | Replace with real number |
| Client satisfaction | Home stats bar | "4.9/5 client satisfaction" | Replace with real rating |
| Portfolio locations | Portfolio cards | "[PLACEHOLDER]" | Add real client locations |
| Portfolio live links | Portfolio cards | "#" | Add real URLs when sites are live |
| Contact email | Contact page, Footer | hello@athenastudios.com | Confirm or replace |
| Phone number | Footer | [PLACEHOLDER] | Add when business phone is set up |
| Social media URLs | Footer | [PLACEHOLDER] | Add real profile URLs |
| Legal pages | Footer | Privacy Policy, Terms of Service | Create actual pages |
| Custom domain | Hosting config | athenastudios.com | Configure when purchased |
| GA4 tag ID | Analytics config | [PLACEHOLDER] | Add real measurement ID |

---

*Athena Studios -- El Sargento, Baja California Sur, Mexico*
*This document is the complete website content brief. No external context is needed beyond BRAND-IDENTITY.md and BRAND-KIT.md in the same directory.*
