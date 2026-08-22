# BOURSEVIEW — ACCESS AUDIT

**2026-08-22. Phases 1–2 of the BourseView data-source evaluation. STOPPED at
Phase 2 by design, before any authenticated request was attempted.**

**No credential of any kind was requested, received, transmitted, stored or
logged during this audit. No login was attempted.**

---

## 1. Verdict on access

| Question | Answer |
|---|---|
| Is there a documented public API? | **No** |
| Is there published API documentation? | **No** |
| Are there published terms of service? | **No** |
| Is there a `robots.txt`? | **No** |
| Is there a `sitemap.xml`? | **No** |
| Does a backend API exist? | **Yes — undocumented** |
| Is the API surface discoverable without reading the client? | **No** |
| Is automation *permitted* per available documentation? | **CANNOT BE ESTABLISHED** |
| Does the product offer an in-app export? | **UNKNOWN — only the account holder can check** |

**Access classification: BLOCKED PENDING USER VERIFICATION OF EXPORT.**

If no in-app export exists, the classification is **D — NOT SUITABLE**, on
access grounds alone and regardless of what data BourseView holds.

## 2. What was probed, and what it showed

All probes were `GET` only, unauthenticated, rate-limited to roughly one
request per second, through the project's own `providers.transport` session.

### 2.1 Documentation surface — absent

`www.bourseview.com` serves a React single-page application. Its server returns
the SPA shell (2,110 bytes) for any route it does not handle, which makes
"exists" and "does not exist" distinguishable by response size.

| Path | Result |
|---|---|
| `/swagger`, `/swagger/index.html` | SPA shell — **no server route** |
| `/docs` | SPA shell — **no server route** |
| `/terms`, `/tos`, `/rules` | SPA shell — **no server route** |
| `/about`, `/help`, `/faq`, `/pricing` | SPA shell — **no server route** |
| `/sitemap.xml` | SPA shell — **no server route** |
| `/robots.txt` | not served |
| `/manifest.json` | real file, 306 bytes (PWA manifest) |
| `/api`, `/api-docs` | **distinct 404**, 161 bytes, not the shell |

### 2.2 A backend exists, and its routes are not guessable

Following redirects on `/api/*` resolves as:

    /api/login  --307-->  /api/login/  --302-->  https://.../api/login/  --> 404

The 307 is a trailing-slash redirect and the 302 is `http`→`https`. The final
404 is served by the application backend, not by the SPA and not by nginx — its
body is a Pyramid/Pylons-style error page. So there **is** a real API behind
`/api/`, and it answered for every path tried.

Nine conventional authentication route names were tried. **All returned 404:**

    /api/login          /api/auth/login     /api/account/login
    /api/v1/login       /api/users/login    /api/token
    /api/captcha        /api/auth/captcha   /api/otp

### 2.3 Why this is where the audit stops

The API exists but publishes no route names, no schema and no documentation.
The only remaining way to locate its endpoints is to read the SPA's JavaScript
bundle and extract the URLs the client calls — that is reverse-engineering a
client in order to reach undocumented private endpoints.

The governing brief prohibits it (§4), and lists it as a stop condition (§29).
**It was not attempted.**

## 3. The distinction that decides this

A valid subscription grants the right to **use** the product. It does not, by
itself, establish a right to **programmatic extraction**, and in the absence of
published terms that right cannot be inferred from anything.

This is not a finding that automation is forbidden. It is a finding that
**permission cannot be established**, which under this project's governance is
treated the same way an unverifiable data check is treated: absence of evidence
is not approval.

### The two legitimate paths that remain

1. **In-app export.** If the product offers Excel/CSV download to a logged-in
   subscriber, that is a documented-in-product mechanism the account holder is
   entitled to use. The account holder exports; SIRAT-AI ingests the file with
   full provenance and checksums. **No credential ever enters the codebase.**
   This is the preferred path.
2. **Ask the vendor.** One email to BourseView support asking whether API
   access or bulk export is offered to subscribers settles the question
   definitively, in either direction.

## 4. Credential handling — what was and was not done

| | |
|---|---|
| Credentials requested in chat | **No** |
| Credentials received | **No** |
| Login attempted | **No** |
| Credentials written to code, config, database or logs | **No** |
| Environment inspected | Yes — **variable NAMES only**, values never read |
| Result of that inspection | no `BOURSE*` / `BV_*` variables present |

### The mechanism that would be used, if a path opens

`settings.py` is the only module in this repository that reads `os.environ`,
and that contract would be followed rather than worked around:

    SIRAT_BOURSEVIEW_USER
    SIRAT_BOURSEVIEW_PASS

Set by the account holder in their own shell. Never committed, never printed,
never persisted to SQLite, never included in a report or an experiment record.
Session tokens would live in process memory only and be redacted from every
diagnostic.

**None of this has been built, because building it before establishing a
legitimate access path would be building the wrong thing.**

## 5. Observed rate limits

None exercised. Roughly 25 unauthenticated `GET` requests were made across the
whole audit, spaced about one second apart. No throttling, no `429`, no
`Retry-After` was observed at that rate — which says nothing about what an
authenticated extraction workload would encounter.

## 6. CAPTCHA / MFA

**Undetermined.** The login flow could not be characterised without either
reading the client bundle or attempting a login. Neither was done.

This matters for a reason worth stating plainly: if login requires a CAPTCHA or
a one-time code, then automating it is *circumvention* and the answer is no
regardless of anything else in this document.

## 7. What the account holder can check in five minutes

Inside BourseView, while logged in, look for:

- a download / export / خروجی button on any data table or chart
- a right-click "export to Excel" on a grid
- a "دانلود اکسل" or "خروجی اکسل" option in any menu
- a reports or گزارش‌ها section offering file downloads
- anything in account settings mentioning API, توکن or کلید

If any of these exist, say which, and the ingestion layer can be built against
it with full provenance. If none exist, the classification is **D** and the
evaluation closes cleanly.

## Provenance

| | |
|---|---|
| phases completed | 1 (repository audit), 2 (access mechanism) |
| phases not started | 3–15 — blocked on access |
| requests made | ~25, unauthenticated `GET`, ~1/second |
| credentials involved | **none** |
| code written | **none** |
| files added to the repository | this document only |
