# Cross-Repo Documentation Inventory

Scope: four local clones under `c:\openSource\dataPreservation\`:

- `EJScreen` — legacy ASP.NET 4.8 web app (unofficial EJScreen reconstruction)
- `EJAM` — R package/Shiny app ("Environmental Justice Analysis Multisite" tool)
- `EJAM-API` — Docker/Plumber R API that fronts the EJAM package (report/data/query/handoff endpoints)
- `EJSCREEN-Data-Processing` (this repo) — Python data pipeline

Goal of this pass: locate *what documentation exists and where*, not yet to rewrite it.
Generated 2026-09-19, updated 2026-09-19 to add EJAM-API. Re-run/update as files move.

## Target of the update work (decided 2026-09-19)

1. **Primary: make accurate.** Fix stale, wrong, or confusing content — this is not a
   pass to fill in TBA/TBD gaps.
2. **Secondary: reduce dependence on web.archive.org.** Where an archive.org link is
   used as a doc source, prefer a live source, a locally-stored copy, or removal if the
   content is no longer relevant.

### Hard constraints

- **The legacy EPA API is dead/offline.** Any file being edited that discusses "the
  API" must not leave the reader confused about whether it means the dead EPA API or
  the live EJAM-API. Fix this opportunistically whenever a relevant file is touched —
  this is not asking for a proactive sweep on its own.
- **The legacy EPA-hosted EJSCREEN and EJAM sites are also dead/offline.** Going
  forward, they should only be mentioned to clarify that these repos are *unofficial,
  non-EPA replacements* — never described as if the original EPA tools are still live
  or authoritative.
- **EJSCREEN-Data-Processing (this repo) is lowest priority.** It has no public-facing
  role and may eventually be folded into another repo. Don't over-invest here.

### Implied priority order for doc work
`EJScreen` and `EJAM-API` (most public-facing legacy-API/legacy-site confusion +
heaviest archive.org dependence) > `EJAM` (large but mostly internally consistent
pkgdown docs) > `EJSCREEN-Data-Processing` (lowest priority, internal-only).

### Branch review snapshot (2026-09-20)

The four repositories are now on the release branches selected for this documentation
work:

- `EJScreen`: `v4-2024-0`
- `EJAM`: `development`
- `EJAM-API`: `version4-2024-0`
- `EJSCREEN-Data-Processing`: `v4.2024.0`

The branches are not fully version-aligned, which matters when documentation describes
current behavior or data vintages:

- **EJScreen `v4-2024-0`** changes `index.html` relative to `main`. Its desktop splash
  now says EPA removed EJSCREEN v2.32 and identifies this as v4.2024.0 with updated
  indicators. The Donate/help work should target this branch's desktop and mobile
  entry points.
- **EJAM `development`** has broad code and vignette changes relative to `main`, but
  `DESCRIPTION` still says `VersionEJSCREEN: 3.2022.2`, `Version: 3.2022.2`, and
  `VersionACS: 2018-2022`. `vignettes/ejscreen.Rmd` changed only its generated-docs
  URL handling; it does not yet contain the planned current 13-indicator table. The
  archived overview and per-indicator HTML/Rmd files are unchanged relative to
  `main`.
- **EJAM-API `version4-2024-0`** currently points at the same commit as `main`; its
  current README/API documentation is therefore already the release-branch content,
  with no additional branch-only documentation change identified.
- **EJSCREEN-Data-Processing `v4.2024.0`** adds the internal readmes already listed
  below. Note that it is potentially confusing but actually correct that its O3 and 
  PM2.5 configs/scripts default to indicator data version `1.2022`. 
  That is year of the latest data available for those indicators and is intentionally
  being merged with the ACS2024 data that gives our release its name.

This is a documentation-risk finding, not an instruction to resolve the version
metadata now: the planned Rmd table should describe the agreed release behavior only
after the team confirms which branch metadata is authoritative.

---

## 1. EJScreen (legacy ASP.NET web app)

Messiest set, as expected — a mix of real docs, app-code HTML that isn't documentation, one embedded PDF, and heavy reliance on **web.archive.org** snapshots of pages EPA has since taken down or changed.

| Path | Type | Topic | Doc vs. app-code |
|---|---|---|---|
| [README.md](../EJScreen/README.md) | md | Project overview, deep-link URL parameter reference table, links to sibling repos | **Documentation** — the richest doc file in this repo, includes a real parameter reference table |
| `INSTALLATION.md` | md | How to run the ASP.NET 4.8 site locally (VS Code + IIS Express) | Documentation |
| `WALKTHROUGH.md` | md | File-by-file explanation of key directories (`index.html`, `comparemapper.html`, `/javascript`) | Documentation, explicitly marked "work in progress" |
| `CONTRIBUTING.md`, `CONDUCT.md` | md | Standard | Documentation |
| `help/ejscreen_help.pdf` | pdf | EJScreen User Guide (looks like an internal copy of the EPA help PDF) | Documentation — **worth checking if stale vs. the archive.org copy linked from README** |
| `help/ExceedanceFieldDesc*.html` (3 files) | html | Field/exceedance descriptions for EJ Index / Supplemental Index | Documentation |
| `help/filterhelp.html` + `filterlegend.png`, `filtermap.png`, `filterpane.png`, `natstat.png`, `winstat.png` | html + images | In-app filter help popup content | Documentation (app help content, not app logic) |
| `ejscreen-map-descriptions.html` | html | Map layer/indicator glossary | Documentation — **likely duplicates EJAM's `vignettes/ejscreen-map-descriptions.Rmd`** |
| `ejscreen-report-digest.html` | html | Report content description | Documentation, not yet content-reviewed |
| `ejsoefielddesc.html`, `ejsoefielddesc1.html` | html | State-of-environment field descriptions | Documentation |
| `ejscreenAPI.html`, `ejscreenAPI1.html` | html | API usage docs | **Documents the dead legacy EPA API** — needs rework to either describe the live EJAM-API instead or clearly frame this as historical/legacy-only; also a likely duplicated/near-duplicate pair (note the `1` suffix) |
| `EJAPIInstructions.pdf` | pdf | API instructions | **Likely describes the dead legacy EPA API** — check before treating as current; probably needs replacing/retiring in favor of pointing at `EJAM-API/README.md` |
| `comparemapper.html`, `index_maintenance.html`, `entry.html`, `entry2.html`, `oauth-callback.html`, `gfxSvgProxyFrame.html` | html | App entry points / iframes / OAuth callback shell / old widget embeds | **App code, not documentation** (per WALKTHROUGH.md) |
| `index.html` | html | **Desktop home screen** — first-load splash-screen `<div>` contains the Help/Glossary/FAQ links (the three `web.archive.org` links listed below) | **📌 NEEDS DONATE HTML** — this is the desktop landing page where the Donate content should be added; also the file to fix once the archive.org Help/Glossary links are replaced |
| `mobile/index.html` | html | **Mobile home screen** — separate file/layout from desktop `index.html`. Content is reached via a hamburger menu (`.dropdown-menu.calcite-menu-drawer`, upper-left) rather than an on-load splash div. That menu contains: Enter Location, Show EJScreen Maps, EJ Report, Map Legend, Layers, Basemaps, an **"About EJScreen"** item (opens the `#myModal` dialog with the "unofficial copy, not affiliated with US Government" disclaimer), and two yellow `.feedback-button` links — **"Share data feedback"** and **"Help improve the tool"** (both Google Forms). It does **not** have the desktop splash's three `web.archive.org` links (User Guide PDF, Glossary, FAQ) | **📌 NEEDS DONATE HTML** — add as a new `<li>` in the `.dropdown-menu.calcite-menu-drawer` list (next to the two feedback-button `<li>`s), and/or inside the `#myModal` "About EJScreen" dialog body, to match how feedback links are already surfaced here |
| `*.aspx`, `*.aspx.vb`, `*.ashx`, `Web.config`, `proxy.config`, `proxy.xsd`, `packages.config` | code/config | Server-side app logic and IIS config | App code |
| `javascript/`, `css/`, `stylesheets/`, `mapdijit/`, `mobile/` (remaining files), `images/`, `nls/` | folders | Client-side app source and assets | App code, not inventoried file-by-file here |

**External / archive.org links (from README.md) — all four are candidates for removal/replacement per the "reduce archive.org dependence" goal:**
- `https://web.archive.org/web/20241008150339/https://www.youtube.com/watch?v=HZp3AWDJt5A` — "EJScreen in 5" intro video (references the dead EPA tool; consider whether this historical video is still worth linking at all)
- `https://web.archive.org/web/20250121194015/https://ejscreen.epa.gov/mapper/help/ejscreen_help.pdf` — EJScreen User Guide PDF; local copy already exists at `help/ejscreen_help.pdf` — prefer that or a re-hosted copy over the archive.org link
- `https://web.archive.org/web/20250123161322/https://www.epa.gov/ejscreen/ejscreen-map-descriptions` — EJScreen Glossary; local copy already exists at `ejscreen-map-descriptions.html` — same fix
- `https://web.archive.org/web/20250123162243/https://www.epa.gov/ejscreen/frequent-questions-about-ejscreen` — FAQ; no local copy found yet, evaluate whether content is still relevant to an unofficial replacement tool
- Also references `EJAM-API` repo README (now cloned locally, see section 3) for deep-link parameter vocabulary

---

## 2. EJAM (R package)

Largest and most structured doc set — standard R package conventions (roxygen → `man/`, pkgdown site, vignettes) plus internal planning notes. This is the one with a real "documentation system," just spread across several R-specific mechanisms.

| Location | Type | Topic | Notes |
|---|---|---|---|
| [README.Rmd](../EJAM/README.Rmd) / `README.md` | Rmd→md | Package overview, "what can you do with EJAM" | `README.md` is generated — **edit the .Rmd, not the .md** |
| `NEWS.md` | md | Changelog / release notes | Currently at v3.2022.2 (Aug 2026) |
| `CITATION.cff` | yaml | Citation metadata | |
| `CONTRIBUTING.md`, `LICENSE.md` | md | Standard | |
| `_pkgdown.yml` | yaml | Site nav config for the published docs site | Drives `https://public-environmental-data-partners.github.io/EJAM/` |
| `vignettes/*.Rmd` (30 files) | Rmd | User + developer guides, rendered into the pkgdown "Articles" | See breakdown below |
| `man/*.Rd` (600+ files) | Rd | Auto-generated function reference (one per exported function) | Not hand-maintained; regenerate via roxygen, don't hand-edit |
| `planning/*.md` (4 files) | md | Internal design/handoff notes | Not user docs — see below |
| `pkgdown/assets/` | assets | Images/CSS for the pkgdown site | |
| `inst/app/`, `inst/plumber/` | folders | Shiny app / API source, may have their own in-code docs | Not yet inventoried in depth |
| `R/app_ui.R` (the `'About'` `tabPanel`) + `inst/global_defaults_shiny.R` (`aboutpage_texts$aboutpage_text`) | R (generates html) | **EJAM's home screen** — unlike EJScreen, EJAM is a single responsive Shiny app with no separate desktop/mobile files. The "About" tab is the landing content, and its HTML body text (links to "What is EJAM?", the EJSCREEN app, and `ejanalysis.org`) is defined as an R `tagList()` in `aboutpage_text` inside `global_defaults_shiny.R` | **📌 NEEDS DONATE HTML** — this is the file/variable to edit to add Donate content to EJAM's home/about screen. (The EJSCREEN link here points to `pedp-ejscreen.azurewebsites.net` — confirmed via `R/url_package.R`'s DESCRIPTION-config docs that this is the canonical `url_ejscreenapp`, with `ejscreen.ejanalysis.com` as an intentional alias, not an inconsistency to fix.) |
| `inst/app/www/ibutton_help.html`, `ibutton_frsFileFormat.html`, `ibutton_includeFacs.html`, `ibutton_locFileFormat.html`, `ibutton_MatchNAICS.html`, `ibutton_NAICSList.html` | html | Small in-app "info bubble" help popups (upload format help, not a Glossary) | Documentation, narrow scope — not the main Help/Glossary equivalent |
| `inst/app/www/user-guide-2025-02.pdf` | pdf | EJAM user guide | Documentation — check date/currency (Feb 2025) |

**Vignettes breakdown (`vignettes/*.Rmd`):**
- *User-facing (published under "EJAM Web App" / "EJAM R Package" nav sections):* `whatis.Rmd`, `webapp.Rmd`, `island-areas.Rmd`, `installing.Rmd`, `basics.Rmd`, `analyzing.Rmd`, `testdata.Rmd`, `distances.Rmd`, `naics.Rmd`, `counties.Rmd`, `zipcodes.Rmd`
- *EJSCREEN documentation (published under "EJSCREEN Documentation"):* `ejscreen.Rmd`, `ejscreen-map-descriptions.Rmd` — **these are likely the closest R-side counterpart to the legacy EJScreen glossary/help HTML pages; worth diffing against EJScreen's `ejscreen-map-descriptions.html` / `ejsoefielddesc.html` for duplication/drift**
- *Developer-only (published under "Developing & Hosting"):* `dev-speed.Rmd`, `dev-app-settings.Rmd`, `dev-api.Rmd`, `dev-deploy-app.Rmd`, `dev-deployment.Rmd`, `dev-run-shinytests.Rmd`, `dev-run-unit-tests.Rmd`, `dev-github-actions-install-tests.Rmd`, `dev-update-datasets.Rmd`, `dev-update-ejscreen-datasets-yearly.Rmd`, `dev-update-documentation.Rmd`, `dev-update-package.Rmd`, `dev-future-plans.Rmd`
- *Present but not currently in `_pkgdown.yml` nav* (orphaned from the published site — worth checking if intentional): `speeds.rda` (data, not a doc)

**Planning docs (`planning/*.md`) — internal, not end-user docs:**
- `api-in-ejam-handoff-2026-07-24.md`
- `ejscreen-multisite-selection-plan.md`
- `in-app-report-rendering-plan.md`
- `plumber-sync-with-ejam-api-plan.md`

No PDFs found directly in EJAM; no direct archive.org links found in the files sampled (EJAM instead links out to EJScreen/EPA live pages via helper functions like `url_ejscreentechdoc()`, `url_ejscreenmap()` in `man/`).

---

## 3. EJAM-API (Docker/Plumber R API)

Smallest repo, and the user's suspicion was right — there is effectively **one** documentation file, but it's dense and load-bearing (it's the API's only reference material besides the live `/__docs__/` page it serves).

| Path | Type | Topic | Notes |
|---|---|---|---|
| [README.md](../EJAM-API/README.md) | md | Full API reference: base URLs, `/report`, `/data`, `/query`, `/handoff` endpoints, params, curl/Python examples | This **is** the documentation — long, detailed, and clearly kept up to date (references specific EJAM PR numbers/versions for feature gating) |
| `CONTRIBUTING.md` | md | Standard, defers to EDGI org-level guidelines/conduct | |
| `LICENSE` | text | License | Not yet checked which license |
| `.github/stale.yml` | config | Bot config, not documentation | |
| `assets/communityreport.css`, `assets/www/` | assets | Static assets for rendered reports | Not documentation |
| `main.r`, `rest_controller.r`, `query_pagination.R`, `Dockerfile`, `tests/` | code | App/API source and tests | No inline doc site (no roxygen/pkgdown here — this is a Plumber API, not an R package) |

**Notable:**
- The live base URLs (`https://api.ejanalysis.com`, `https://ejamapi-84652557241.us-central1.run.app`) serve **interactive API docs at `/__docs__/`** — that's hosted/generated documentation outside this repo (likely auto-generated by Plumber) and isn't captured by a static file inventory. Worth opening in a browser during a deeper pass to see if it drifts from the README's hand-written examples.
- README links out to a live PDF example: `https://www.sf.gov/sites/default/files/2024-03/EJScreen%20Community%20Report.pdf` (San Francisco's own hosted example report, not an archive.org link).
- No CONDUCT.md in-repo; the badge/link at the top of README points to the EDGI org-level conduct doc directly (same pattern as EJScreen).

---

## 4. EJSCREEN-Data-Processing (this repo)

Smallest, cleanest doc set — mostly in-repo Markdown, low redundancy.

| Path | Type | Topic | Notes |
|---|---|---|---|
| [README.md](README.md) | md | Project overview, dev setup (`uv`), links to EJAM/EJScreen, license | Has a "Coming attractions" stub — docs still TBD |
| [CONTRIBUTING.md](CONTRIBUTING.md) | md | Contributing guidelines | Defers to org-level PEDP `overview` repo; project-specific section is "TBA" |
| [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) | md | Code of conduct | One-line pointer to org-level repo |
| [LICENSE](LICENSE) | text | AGPLv3 license | |
| [pipeline/readme.md](pipeline/readme.md) | md | Explains `pipeline/` is local mirror of S3 bucket layout | Very short |
| [scripts/readme.md](scripts/readme.md) | md | `uv sync` / venv activation instructions | |
| [scripts/o3/readme.md](scripts/o3/readme.md) | md | O3 indicator pipeline workflow, CLI examples, output contract | Good template quality |
| [scripts/pm25/readme.md](scripts/pm25/readme.md) | md | PM2.5 indicator pipeline workflow, CLI examples, output contract | Mirrors o3 readme structure |
| [scripts/shared/readme.md](scripts/shared/readme.md) | md | Shared config/path-resolution/fetch utilities reference | Notes 2 files "excluded pending review" |

**External links referenced from README.md:**
- `https://ejam.publicenvirodata.org/` — EJAM app
- `https://ejscreen.ejanalysis.com/` — EJScreen app
- `https://www.epa.gov/system/files/documents/2024-07/ejscreen-tech-doc-version-2-3.pdf` — EPA EJSCREEN tech doc (primary reference for re-engineering)
- `https://screening-tools.com/get-involved` — PEDP org working docs/comms channel

No `docs/` folder, no PDFs, no archive.org links in this repo.

---

## Cross-repo observations / likely duplication or drift

1. **"Map descriptions" / glossary content exists in two places, and they are NOT equivalent**: `EJScreen/ejscreen-map-descriptions.html` and `EJAM/vignettes/ejscreen-map-descriptions.Rmd`. Checked directly (2026-09-19):
   - `EJAM/vignettes/ejscreen-map-descriptions.Rmd` links to the archived "Overview of Environmental Indicators in EJScreen" table via `EJAM::url_github_preview(..., file="overview-environmental-indicators-ejscreen.html")` (the `htmlpreview.github.io` link, serving the copy checked into `EJAM/data-raw/EJSCREEN_archived_pages/`) **plus** a `web.archive.org` fallback — a working, team-controlled reference.
   - `EJScreen/ejscreen-map-descriptions.html` is a raw scrape of the *entire live EPA webpage* (full nav, USWDS banner, GTM scripts, `canonical` pointing at `www.epa.gov`) whose equivalent link is a **relative path** (`/ejscreen/overview-environmental-indicators-ejscreen`) — dead in this context since it only worked when served from `epa.gov` itself.
   - **Conclusion: treat `EJAM/vignettes/ejscreen-map-descriptions.Rmd` as the canonical, maintained version** (working links, plain Markdown, easy to edit). Consider retiring `EJScreen/ejscreen-map-descriptions.html` in favor of linking to the EJAM vignette, or at minimum fix its broken relative link if it must stay standalone.

2. **The EJScreen User Guide PDF exists in three forms**: local `EJScreen/help/ejscreen_help.pdf`, the archive.org snapshot linked from `EJScreen/README.md`, and (indirectly) EPA's original tech doc PDF linked from this repo's own `README.md`. Confirm which is authoritative before treating any as current.
3. **API docs are now split across three repos, not two**: `EJScreen/ejscreenAPI.html` / `ejscreenAPI1.html` / `EJAPIInstructions.pdf` (EJScreen side, describes the *old EPA* API) vs. `EJAM/vignettes/dev-api.Rmd` (EJAM package-dev perspective) vs. `EJAM-API/README.md` (the actual current, live API's reference doc). These describe related but distinct APIs (legacy EPA API vs. the new EJAM-API replacement) in three different formats — **there's a real risk of readers conflating the old EPA API docs with the new EJAM-API docs**, since EJScreen's README explicitly links to EJAM-API as its replacement.
4. **EJAM-API's README is the single source of truth for the current API** — it's detailed and appears actively maintained (references specific EJAM PR numbers), but it's also the *only* place that documentation lives (no separate vignette/site). If it goes stale, there's no secondary source to catch it.
5. **EJAM's `man/*.Rd` (600+ files)** are auto-generated reference docs, out of scope for manual editing — exclude from any manual doc-update pass, just confirm they regenerate cleanly.
6. **This repo (`EJSCREEN-Data-Processing`) is the least redundant** but also the least complete — several sections are literal "TBA"/"TBD" placeholders (`CONTRIBUTING.md`, README's R Setup section).
7. **Hosted/generated docs outside any repo's static files**: EJAM-API serves interactive docs at `/__docs__/` on its live URLs, and EJAM's pkgdown site is built from vignettes but hosted separately at `public-environmental-data-partners.github.io/EJAM/`. Both are worth checking against their source-of-truth files for drift during a deeper pass.
8. **`EJAM/data-raw/EJSCREEN_archived_pages/` is a mixed bag, not a uniform archive** (checked 2026-09-19, see its `NOTE.R`): `overview-environmental-indicators-ejscreen.html` (linked from the map-descriptions vignette via `url_github_preview()` + `htmlpreview.github.io`) and `ejscreen-tech-doc-version-2-3.pdf` are genuine frozen snapshots of dead EPA pages with no `.Rmd` source — the folder name is accurate for these. But `ejscreen-map-descriptions.Rmd`/`.html`, `overview-socioeconomic-indicators-ejscreen.Rmd`/`.html`, and the ozone/PM2.5/diesel-PM `-overview.Rmd`/`.html` pairs are, per `NOTE.R`, **near-duplicates of the actual vignettes in `EJAM/vignettes/`**, kept here only so old external links to this folder keep resolving. These aren't archived EPA content at all — they're forked copies of EJAM's own docs. Candidate for consolidation (not urgent): treat the vignettes as the single source of truth and either delete or redirect these duplicate copies once whatever links target them are updated. `url_github_preview()` itself does no generation/knitting — it just builds a `htmlpreview.github.io` URL pointing at whatever static file already exists in the repo at that path.


---
# Pending tasks / TODO:
(Acknowledging: This file has taken on the extra role of being a starting todo list.)

## 📌 Pending small task: Donate HTML

Files tagged **📌 NEEDS DONATE HTML** above are the home-screen files identified for
inserting the Donate HTML button:

- `EJScreen/index.html` — desktop home screen (splash-screen div with Help/Glossary/FAQ links)
- `EJScreen/mobile/index.html` — mobile home screen (see description above of how the new button/option will be inserted in the dropdown menu)
- `EJAM/inst/global_defaults_shiny.R` (`aboutpage_texts$aboutpage_text`, rendered by the
  `'About'` tab in `EJAM/R/app_ui.R`) — EJAM's single home/about screen (no desktop/mobile split)

Clicking the button should open a new tab in the current browser at:
`https://donorbox.org/open-environmental-data-project-donations-2`

---

## 📌 Pending task: EJAM "current data years" table (`ejscreen.Rmd`)

- **Branch selection:** resolved for this review; see the branch snapshot above. The
  remaining question is which branch's release metadata is authoritative where the
  selected branches are not version-aligned. (The archived-pages links seen so far use
  both `main` and `development` as the branch segment; update maintained links only
  after the intended branch is confirmed.)
- **File to edit:** `EJAM/vignettes/ejscreen.Rmd` — replacing/supplementing the existing
  "Key environmental indicators with year and source of each" bullet, which currently
  only links out to the archived, frozen `overview-environmental-indicators-ejscreen.html`
  (see cross-repo observation #8). The archived file/link stays as-is, untouched.
- **Scope:** a full table of **all 13 EJScreen environmental indicators** — not a short
  excerpt that still defers to the archive for "the rest." The new table in the Rmd
  should be self-contained.
- **Immediate driver:** PM2.5 and Ozone data years are changing for the upcoming release.
  Those rows should be visually flagged (asterisk, and possibly color) with a footnote
  below the table, e.g. "* Updated to current data year in release 4.2024.0."
- **Newly identified complication -- per-indicator detail pages:** the archived overview
  table itself links out further, per indicator, to pages such as:
  `https://htmlpreview.github.io/?https://github.com/Public-Environmental-Data-Partners/EJAM/blob/development/data-raw/EJSCREEN_archived_pages/ejscreen-indicators-overview-particulate-matter-25-pm25.html`
  (one of the `ejscreen-indicators-overview-*.html` files in that same folder -- see
  cross-repo observation #8). That PM2.5 detail page in turn points to the **outdated
  EJSCREEN v2.3 technical documentation PDF** (`ejscreen-tech-doc-version-2-3.pdf`,
  same folder). That tech doc will have to be updated -- or rebuilt gradually, indicator by
  indicator, as we re-implement the indicator code -- rather than all at once.
  Consequence: the new table in `ejscreen.Rmd` can't just link to the existing
  `ejscreen-indicators-overview-*.html` detail pages as-is for any indicator whose tech-doc
  section has actually changed; those need **new versions of the per-indicator HTML,
  stored in a new (not-yet-created) folder we can keep updating** as each indicator is
  rebuilt, separate from the frozen `EJSCREEN_archived_pages/` folder. Where an indicator's
  methodology/tech-doc section hasn't changed yet, it's fine to keep linking to the
  existing archived detail page for now.
- **Explicitly out of scope for this edit:** sourcing the table from the pipeline config
  files (`o3_config.json` / `pm25_config.json`). That config-as-code-as-source-of-truth
  idea is a real nice-to-have for later, but this table will be hand-maintained for now.
- **Status:** not started -- the repositories are now on the selected branches; still
  waiting on release-metadata confirmation and the user's work branch before any file
  changes happen.
