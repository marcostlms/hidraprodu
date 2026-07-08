# Hidrara — Product Catalog & Admin Panel

## Original Problem Statement
Build a full-stack site for **Hidrara Conexões** (Brazilian industrial supplier) using the Emergent template layout as the visual base, but replacing the content with real Hidrara data. Requirements:
- Keep the general template design intact, only replace the content
- **Fix HQ from Sertãozinho to Araraquara/SP** (Hidrara was founded in 1991 in Araraquara)
- Import real product codes / photos / categories from the Hidrara HTML clones
- Build backend allowing admin to add/remove products with photos, description, and identification codes
- Public users can search products by name or code
- Category system for navigation (Hidráulica, Pneumática, Ferragens, etc.)
- Secure JWT authentication for admin panel
- Admin form with validation; image URL input + file upload
- Sidebar/top menu with category filters
- Data structure supporting id, name, category, description, image URL
- UI toggle between public catalog and admin panel

## Stack
- Backend: FastAPI + MongoDB (motor) + JWT + bcrypt + Emergent Object Storage
- Frontend: React 19 + Tailwind + shadcn/ui + sonner + lucide-react + react-router
- Design palette: `#3b3073` purple, `#facb15` yellow, `#f8f6eb` cream. Fonts: Barlow Condensed + Manrope.

## What's Implemented (Feb 2026)
- ✅ MATRIZ corrected to **Araraquara/SP** (all 8 units seeded)
- ✅ 8 categories seeded (Hidráulica, Pneumática, Ferragens, Filtros, Mangueiras, Vedações, Rolamentos, Chicotes Elétricos)
- ✅ 12 real Hidrara product codes seeded (P954208, WK10002, X220184, PERI-340-10C, PF420, REC-153, REL-100, X770733...)
- ✅ Public site: Hero + Sectors + Products (sidebar filter + search bar + product detail dialog) + Projects + Units + Brands marquee + Testimonials + Contact form + Footer
- ✅ Admin `/admin/login` (JWT + cookie + Bearer fallback) → `/admin` dashboard
- ✅ Admin CRUD: create/edit/delete products with validation, unique code check, category select, featured toggle
- ✅ Image handling: URL input OR direct upload (Emergent Object Storage)
- ✅ Search by name/code/description; category filter with counts
- ✅ WhatsApp CTA on product details

## Test Credentials
- Email: `admin@hidrara.com.br`
- Password: `Hidrara@2026`

## Testing Status (iteration_1.json)
- Backend: 100% pass (10/10 endpoint scenarios)
- Frontend: ~95% pass (10/11 flows verified) — full CRUD, auth, search, filters all working
- No blocking issues

## P1/P2 Backlog
- Batch/CSV product import for large catalogs
- Product spec sheet (PDF) upload alongside images
- Contact form → backend inbox (currently opens mailto)
- SEO metadata + Open Graph tags per category
- Google Analytics / conversion tracking
