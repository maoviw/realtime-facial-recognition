# CLAUDE.md — Cognitive Face Live

## Project overview

Real-time 1:N facial recognition dashboard. Webcam captures are periodically
sent to a FastAPI backend that uses Azure Face API (detection + head-pose) and
DeepFace (local verification against enrolled references). Results are shown
live on a Next.js frontend with bounding-box overlays.

## Repository layout

```
backend/          FastAPI app (Python)
  main.py         API routes
  recognition.py  Azure Face + DeepFace logic
  actions.py      Configurable post-recognition actions
  database.py     SQLite helpers (references + history)
  config.py       Settings (pydantic-settings, reads .env)
  tests/          pytest suite

frontend/         Next.js 16 / React 19 / TypeScript / Tailwind 4
  src/app/        page.tsx — main dashboard (client component)
  src/components/ CameraView, TelemetryPanel, HistoryPanel,
                  ReferencesPanel, SettingsPanel, Toast
  src/lib/        api.ts, types.ts
```

## Development commands

### Backend

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload          # dev server on :8000
pytest                             # run tests
```

Requires a `backend/.env` (copy `backend/.env.example`):
```
AZURE_FACE_ENDPOINT=https://<resource>.cognitiveservices.azure.com
AZURE_FACE_KEY=<key>
```

### Frontend

```bash
cd frontend
npm ci
npm run dev                        # dev server on :3000
npm run build && npm start         # production build
```

### Docker (full stack)

```bash
cp backend/.env.example backend/.env
docker compose up --build
```

Frontend: http://localhost:3000 — Backend: http://localhost:8000

## Key conventions

- **No Azure key?** Backend still starts; `/health` returns `azure_configured: false` and the frontend shows a toast warning.
- **Coordinate mapping**: screenshot is taken at the camera's intrinsic resolution (`forceScreenshotSourceSize`). Bounding boxes are expressed as percentages of that resolution and mapped 1:1 onto the video element (which uses `object-fill`, not `object-cover`).
- **Anti-stacking**: `inFlightRef` prevents concurrent `/analyze-face` calls; a new capture only fires when the previous one has completed.
- **Small-face filter**: faces whose width < 6 % of the frame are discarded (background TVs, reflections).
- **Mirrored video**: the webcam feed is mirrored for a natural selfie feel; Azure coordinates are in the *unmirrored* space, so the CSS `transform: scaleX(-1)` is applied only to the `<video>` element, not to the overlay container.

## Next.js version note

This project uses Next.js 16 / React 19. Read `frontend/AGENTS.md` before
modifying frontend code — APIs and conventions may differ from older versions.

## Security Hardening (2026-05 Sprint)

Implemented 7 CVE-class hardening measures across backend, Docker, and CI:

### 1. Input Validation & Limits (DoS Prevention)
- **backend/config.py**: Added `MAX_IMAGE_BYTES`, `MAX_UPLOAD_BYTES`, `HISTORY_LIMIT_MAX`
- **backend/main.py**: Image magic-byte detection before decoding (`_IMAGE_MAGIC`, `_looks_like_image()`)
- **backend/main.py**: Size validation in `_decode_image()` prevents memory exhaustion
- `.env.example`: Default limits: 8 MB images, 200-item history

### 2. Command Execution (RCE Prevention)
- **backend/config.py**: Added `ALLOW_COMMAND_ACTION` boolean (default `false`)
- **backend/actions.py**: Command execution blocked unless `ALLOW_COMMAND_ACTION=true`
- `.env.example`: Documented that commands require explicit trust-environment override

### 3. Production Mode & API Authentication
- **backend/config.py**: Added `ENV` setting (`development` | `production`)
- **backend/config.py**: `is_production()` classmethod enforces `API_KEY` requirement in prod
- **backend/main.py**: `lifespan()` raises early if production without API_KEY (fail-safe startup)
- `.env.example`: Documented that API_KEY is mandatory in production

### 4. Multi-Face Handling (Info Leak Prevention)
- **backend/main.py**: Each face in a frame gets its own cropped capture via `save_temp_capture(image_bytes, rect)`
- **backend/recognition.py**: `_crop_face_bytes(image_bytes, rect, padding=0.25)` extracts individual face region as JPEG
- Prevents accidental exposure of full frame when sharing/logging individual face results

### 5. Non-Root Docker Execution
- **backend/Dockerfile**: Added `gosu` (privilege-dropping utility)
- **backend/docker-entrypoint.sh** (new): Runs as root → fixes volume ownership → drops to appuser via gosu
- Handles pre-existing root-owned volumes from `docker compose up`
- Prevents container escape via elevated privileges

### 6. Cross-Platform Line Endings (Script Integrity)
- **.gitattributes** (new): Forces `*.sh` and `docker-entrypoint.sh` to LF line endings
- **backend/Dockerfile**: Sed-based CRLF cleanup as defense-in-depth
- Prevents shebang corruption on Windows git clones

### 7. Dependency Audit in CI
- **.github/workflows/ci.yml**: Added `audit` job running `pip-audit` and `npm audit`
- Detects vulnerable transitive dependencies before merge

---

## Auto-Enrollment Feature (2026-05 Sprint)

Allows automatic registration of clearly-detected faces with optional user renaming.

### Database Schema (backend/database.py)

Added to `references_face` table:
- `auto INTEGER` — 1 if auto-enrolled, 0 if manually named (marks enrollment mode)
- Migration logic checks for existing column before ALTER (idempotent)
- New `meta` table tracks `next_auto_label` counter for "Visage N" naming

### Recognition Flow (backend/main.py)

1. **Pose Clarity Check**: `is_clear_for_enrollment(pitch, yaw, roll)` verifies strict thresholds:
   - Pitch ≤ `AUTO_ENROLL_PITCH_MAX` (default 8°)
   - Yaw ≤ `AUTO_ENROLL_YAW_MAX` (default 8°)
   - Roll ≤ `AUTO_ENROLL_ROLL_MAX` (default 12°)

2. **Size Filter**: Face width ≥ `AUTO_ENROLL_MIN_WIDTH_RATIO` (default 8% of frame)
   - Prevents tiny/blurry faces from being enrolled

3. **Non-Matching**: DeepFace.verify returns False (face not in references)

4. **Auto-Enrollment Trigger**: `_maybe_auto_enroll()` registers face as "Visage N"
   - Cropped face stored via `save_reference_image()`
   - System action set to `"Auto-enrôlé"` in response

### Frontend Updates (frontend/src/components/ReferencesPanel.tsx)

- **ReferenceRow**: Inline edit with Enter/Escape, Check/X confirm buttons
- **Thumbnail**: Async loading from `api.referenceImageUrl(id)` with URL cleanup
- **Auto Badge**: Amber Sparkles icon + "auto" label on auto-enrolled faces
- **Rename Flow**: Click pencil → edit name → press Enter/Click Check → system clears auto flag
- Helper text explains auto-enrollment behavior and renaming

### Frontend API (frontend/src/lib/api.ts)

- `updateReference(id, name)` — PATCH endpoint to rename
- `referenceImageUrl(id)` — GET endpoint returning object URL blob for thumbnail display
- Both include auth headers if API_KEY is set

### Environment Variables (.env.example)

```
AUTO_ENROLL=true
AUTO_ENROLL_PITCH_MAX=8       # Max head pitch for clear enrollment
AUTO_ENROLL_YAW_MAX=8         # Max head yaw
AUTO_ENROLL_ROLL_MAX=12       # Max head roll
AUTO_ENROLL_MIN_WIDTH_RATIO=0.08  # Min face width ratio
```

### Testing (backend/tests/test_api.py)

- `test_auto_enroll_unknown_clear_face()`: Verify auto-enrollment on clear unrecognized faces
- `test_auto_enroll_disabled()`: Verify AUTO_ENROLL=false blocks enrollment
- `test_rename_reference()`: PATCH endpoint 200/404 cases
- `test_reference_image_endpoint()`: GET /references/{id}/image 200/404 cases

---

## Docker Volume Permission Fix (2026-05)

### Problem
Non-root hardening (dropping to `appuser`) prevented writing to volumes owned by root.

### Solution
- **docker-entrypoint.sh**: Runs as root, fixes ownership of `/app/data` via `chown`, then execs `gosu appuser`
- **Dockerfile**: Changed `USER appuser` to `ENTRYPOINT ["/docker-entrypoint.sh"]`
- Handles both fresh and pre-existing root-owned volumes

### CRLF Issue
Windows git clones converted `docker-entrypoint.sh` to CRLF, breaking the shebang.
- Fix 1: `.gitattributes` forces LF for all `*.sh` files
- Fix 2: Dockerfile sed clears CRLF as defense-in-depth

---

## Hardening Program (2026-06) — 5-sprint roadmap

Multi-sprint program to harden security, reliability, performance, and
operability. Executed **sprint-by-sprint with a review pause between each**.
Status: **Sprint 0 ✅ · Sprint 1 ✅ · Sprint 2 ✅ · Sprint 3 ✅ · Sprint 4 ✅ — programme terminé.**

### Sprint 0 — Diagnostic & Cadrage

Full backend+frontend diagnostic. Risk register below; each risk is mapped to a
sprint and has acceptance criteria.

#### Risk Register

| ID | Domaine | Risque | Réf | Criticité | Sprint |
|----|---------|--------|-----|-----------|--------|
| R1 | Sécurité | Aucun rate limiting (DoS / brute-force API_KEY) | main.py endpoints | Critique | 1 ✅ |
| R2 | Privacy | Noms (PII) en clair dans les logs | main.py:107, actions.py:25 | Élevée | 1 ✅ |
| R3 | Sécurité | Dépendances non épinglées (requests, Pillow, deepface, tf-keras, opencv) | requirements.txt | Élevée | 1 ✅ |
| R4 | Sécurité | Pas de security headers (CSP, X-Frame-Options, HSTS) | main.py | Moyenne | 1 ✅ |
| R5 | Sécurité | CORS methods/headers en wildcard | main.py | Moyenne | 1 ✅ |
| R6 | Sécurité | API_KEY exposée dans le bundle client (NEXT_PUBLIC_) | frontend api.ts | Moyenne | 1 ⚠️ documenté |
| R7 | Fiabilité | Aucun retry/backoff sur Azure Face API | recognition.py | Élevée | 2 |
| R8 | Fiabilité | DeepFace.verify sans timeout (hang possible) | recognition.py | Élevée | 2 |
| R9 | Fiabilité | Frontend `.json()` sur erreur réseau → TypeError | api.ts | Moyenne | 2 |
| R10 | Persistance | Historique sans purge/TTL (croissance illimitée) | database.py | Moyenne | 2 |
| R11 | Persistance | Aucun index (created_at, reference_id), pas de FK | database.py | Moyenne | 2 |
| R12 | Fiabilité | Fichiers temporaires non nettoyés sur exception | main.py | Moyenne | 2 |
| R13 | Observabilité | Logs non structurés, pas de métriques | main.py | Moyenne | 4 |
| R14 | Tests | Pas de tests d'échec backend ; aucun test frontend | tests/, frontend | Moyenne | 2/3 |
| R15 | UX | Pas de health-check périodique ni reconnexion auto | page.tsx | Faible | 3 |

Lower-severity items tracked for later sprints: re-renders frontend
(useCallback), clés de liste par index (CameraView), debounce du slider,
mémoïsation `timeAgo`, dimensions d'image non validées, VACUUM SQLite.

#### Prioritized Backlog
- **Sprint 1 (Sécurité)**: R1, R2, R3, R4, R5, R6
- **Sprint 2 (Fiabilité)**: R7, R8, R9, R10, R11, R12, R14 (backend)
- **Sprint 3 (Perf/UX)**: R15, frontend re-render/keys/debounce, R14 (frontend)
- **Sprint 4 (Exploitation)**: R13, metrics endpoint, runbook, dashboards

#### Acceptance Criteria (Sprint 1)
- Rate limiting: au-delà de la limite, `/analyze-face` (et autres) renvoie 429. *(testé)*
- PII: aucun nom en clair dans les logs quand `LOG_MASK_PII=true`. *(testé)*
- Deps: toutes les deps runtime épinglées ; `pip-audit` propre en CI.
- Headers: réponses portent CSP / X-Frame-Options / X-Content-Type-Options / Referrer-Policy. *(testé)*
- CORS: methods/headers restreints à une allowlist explicite.
- Auth: endpoint protégé renvoie 401 sans clé quand `API_KEY` est défini. *(testé)*

### Sprint 1 — Sécurité & Conformité (livré)

- **Rate limiting (R1)**: `slowapi` `Limiter` (clé = IP) + `SlowAPIMiddleware`,
  limite par défaut configurable `RATE_LIMIT` (défaut `60/minute`, vide =
  désactivé). Handler 429 dédié (`main._rate_limit_handler`).
- **PII log masking (R2)**: `config.mask_name()` (1ère lettre + astérisques),
  appliqué à `actions.py` (visage reconnu) et `main.py` (auto-enrôlement).
  Réglable via `LOG_MASK_PII` (défaut `true`).
- **Pinned deps (R3)**: `requirements.txt` épingle requests 2.32.3, Pillow
  10.4.0, deepface 0.0.93, tf-keras 2.17.0, opencv-python 4.10.0.84,
  python-multipart 0.0.18, + slowapi 0.1.9.
- **Security headers (R4)**: `SecurityHeadersMiddleware` ajoute
  `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`,
  `Content-Security-Policy: default-src 'none'`, + `Strict-Transport-Security`
  en production seulement.
- **CORS hardening (R5)**: methods/headers restreints
  (`GET/POST/PATCH/DELETE/OPTIONS`, `x-api-key`/`content-type`).
- **Secret exposure (R6)**: avertissement inline dans `frontend/src/lib/api.ts`
  (les `NEXT_PUBLIC_*` partent au client) ; correctif proxy serveur prévu Sprint 4.
- **Tests**: `test_security_headers_present`, `test_requires_api_key`,
  `test_pii_masking`, `test_rate_limit_enforced` (conftest désactive
  `RATE_LIMIT` pour des tests déterministes).

#### Sprint 1 — Checklist sécurité
- [x] Rate limiting par IP (429)
- [x] PII masquée dans les logs
- [x] Dépendances épinglées
- [x] Security headers
- [x] CORS durci
- [x] Exposition secret client documentée
- [x] Tests d'authentification / headers / 429 / masquage

### Sprint 2 — Fiabilité & Résilience (livré)

- **Azure retries/backoff (R7)**: `recognition.detect_faces` réessaie sur
  erreurs réseau (`ConnectionError`/`Timeout`) et statuts transitoires
  (429/500/502/503/504) avec backoff exponentiel
  (`AZURE_BACKOFF_BASE * 2**tentative`). Réglable :
  `AZURE_MAX_RETRIES` (défaut 2), `AZURE_BACKOFF_BASE` (défaut 0.5). Les 4xx
  non transitoires sont propagées immédiatement.
- **DeepFace timeout (R8)**: `_verify_with_timeout()` exécute `DeepFace.verify`
  dans un `ThreadPoolExecutor` borné par `DEEPFACE_TIMEOUT` (défaut 20 s). Un
  dépassement journalise et passe à la référence suivante (pas de hang).
- **Frontend erreur réseau (R9)**: `api.ts` convertit le `TypeError` de `fetch`
  (backend injoignable) en message clair « Backend injoignable » au lieu de
  propager « Failed to fetch ».
- **Rétention historique (R10)**: `database.purge_old_events(ttl_days, max_rows)`
  supprime les événements > `HISTORY_TTL_DAYS` (défaut 30) et plafonne à
  `HISTORY_MAX_ROWS` (défaut 10000). Appelée au démarrage + tâche d'arrière-plan
  périodique (`_periodic_purge`, toutes les 6 h) dans `lifespan`.
- **Index & intégrité (R11)**: index `idx_events_created_at` et
  `idx_events_reference_id` (idempotents). À la suppression d'une référence,
  les événements liés voient leur `reference_id` mis à `NULL` (évite les
  orphelins). FK stricte reportée (nécessiterait un rebuild de table).
- **Nettoyage temp robuste (R12)**: la suppression du fichier de capture dans
  `analyze_face` est best-effort (try/except `OSError`) et ne masque plus
  l'erreur métier d'origine.
- **Tests d'échec (R14 backend)**: `test_azure_retries_then_succeeds`,
  `test_azure_retries_exhausted`, `test_deepface_timeout_skips_reference`,
  `test_purge_old_events`, `test_purge_max_rows_cap`,
  `test_delete_reference_nulls_event_link`.

#### Sprint 2 — Checklist fiabilité
- [x] Retries/backoff Azure
- [x] Timeout DeepFace
- [x] Gestion erreur réseau frontend
- [x] Purge/TTL historique + plafond de lignes
- [x] Index SQLite + intégrité au delete
- [x] Nettoyage fichiers temporaires robuste
- [x] Tests d'échec backend

### Sprint 3 — Performance & UX (livré)

- **Clés de liste stables (CameraView)**: les overlays de visages sont désormais
  keyés par position (`left-top-width-height`) plutôt que par index, évitant les
  réassociations erronées quand l'ordre des visages change entre frames.
- **Debounce du curseur (SettingsPanel)**: valeur locale immédiate + remontée
  débouncée (250 ms) au parent, pour ne plus recréer la boucle de capture à
  chaque tick du curseur. Resync prop via ajustement d'état pendant le rendu.
- **Mémoïsation `timeAgo` (HistoryPanel)**: libellés temporels calculés via
  `useMemo` (recalcul uniquement quand `events` change).
- **Health-check périodique + reconnexion auto (R15)**: `page.tsx` sonde
  `/health` toutes les 10 s ; au retour en ligne après coupure, recharge
  références + historique et notifie l'utilisateur. (`checkHealth`,
  `loadReferences`, `loadHistory`, `captureAndAnalyze` déjà en `useCallback`.)
- **Tests frontend (R14 front)**: stack **Vitest + Testing Library + jsdom**
  (`vitest.config.ts`, `vitest.setup.ts`, script `npm test`). Tests :
  `src/lib/api.test.ts` (erreur réseau → message clair, extraction `detail`,
  succès JSON) et `src/components/SettingsPanel.test.tsx` (debounce → un seul
  appel parent avec la dernière valeur). Étape `Test` ajoutée au job frontend CI.

#### Sprint 3 — Checklist perf/UX
- [x] Clés de liste stables (CameraView)
- [x] Debounce du curseur d'intervalle
- [x] Mémoïsation des libellés temporels
- [x] Health-check périodique + reconnexion auto
- [x] Tests frontend (Vitest) + intégration CI

### Sprint 4 — Exploitation & Gouvernance (livré)

- **Logs structurés (R13)**: `LOG_FORMAT=json` active `_JsonLogFormatter`
  (1 ligne JSON/enregistrement : `ts/level/logger/msg/exc`), sinon format texte
  lisible. Niveau réglable via `LOG_LEVEL`. Setup centralisé dans
  `_configure_logging()`.
- **Endpoint `/metrics`**: `backend/metrics.py` (registre in-process sans
  dépendance) + `MetricsMiddleware` (compte requêtes, 4xx/5xx, latence). Rendu
  au **format d'exposition Prometheus** (`text/plain`). Compteurs métier :
  `azure_requests_total`, `azure_failures_total`, `deepface_timeouts_total`,
  `auto_enrollments_total`, `request_latency_seconds_{sum,count}`.
- **Proxy serveur Next.js (R6 — corrigé)**: `src/app/api/proxy/[...path]/route.ts`
  relaie les appels same-origin vers le backend en injectant `x-api-key` **côté
  serveur** (`API_KEY`/`BACKEND_ORIGIN`, sans `NEXT_PUBLIC_`). La vraie clé ne
  touche plus le navigateur. `api.ts` cible `/api/proxy` par défaut ; mode direct
  conservé via `NEXT_PUBLIC_BACKEND_URL` (dev/legacy). Streaming binaire géré
  (miniatures), en-têtes hop-by-hop filtrés, 502 si backend injoignable.
- **Tests**: backend `test_metrics_endpoint_exposes_counters`,
  `test_metrics_count_increases`, `test_json_log_formatter_outputs_json` ;
  frontend `src/app/api/proxy/route.test.ts` (injection clé serveur + 502).

#### Sprint 4 — Checklist exploitation
- [x] Logs structurés JSON (LOG_FORMAT/LOG_LEVEL)
- [x] Endpoint `/metrics` (format Prometheus) + middleware
- [x] Proxy serveur pour R6 (clé API hors navigateur)
- [x] Runbook (ci-dessous) + métriques pour dashboards
- [x] Tests observabilité + proxy

---

## Runbook d'exploitation

### Démarrage
- **Local backend** : `cd backend && uvicorn main:app --reload` (`:8000`).
- **Local frontend** : `cd frontend && npm run dev` (`:3000`).
- **Full stack** : `cp backend/.env.example backend/.env && docker compose up --build`.
- **Production** : exiger `ENV=production` + `API_KEY` (le backend refuse de
  démarrer sans clé). Définir `BACKEND_ORIGIN` + `API_KEY` côté Next.js (mode
  proxy) ; ne pas utiliser `NEXT_PUBLIC_API_KEY`.

### Santé & observabilité
- **Liveness** : `GET /health` → `azure_configured`, `deepface_available`,
  nb de références.
- **Métriques** : `GET /metrics` (Prometheus). Surveiller :
  `azure_failures_total` (taux d'erreur Azure), `deepface_timeouts_total`
  (saturation/lenteur), `http_responses_5xx_total`, latence moyenne =
  `request_latency_seconds_sum / request_latency_seconds_count`.
- **Logs** : `LOG_FORMAT=json` pour ingestion SIEM/ELK ; PII masquée par défaut
  (`LOG_MASK_PII=true`).

### Dashboards suggérés (à partir de `/metrics`)
- **Trafic** : taux `http_requests_total`, répartition 4xx/5xx.
- **Latence** : moyenne (sum/count), à alerter au-delà d'un seuil (ex. > 2 s).
- **Dépendances** : ratio `azure_failures_total / azure_requests_total`,
  `deepface_timeouts_total`.
- **Métier** : `auto_enrollments_total` (dérive du nb d'auto-enrôlements).

### Incidents fréquents
- **429 en masse** : `RATE_LIMIT` trop bas → ajuster, ou abus → vérifier les IP.
- **502 « Erreur réseau vers Azure »** : Azure down/clé invalide → retries déjà
  en place (`AZURE_MAX_RETRIES`), vérifier `azure_configured` et le quota.
- **Vérif. qui traîne** : `deepface_timeouts_total` grimpe → augmenter
  `DEEPFACE_TIMEOUT` ou réduire le nb de références.
- **Backend hors ligne** : le frontend sonde `/health` toutes les 10 s et se
  reconnecte automatiquement (notification utilisateur).
- **Permissions volume Docker** : gérées par `docker-entrypoint.sh` (chown +
  gosu). Voir « Docker Volume Permission Fix ».

### Sauvegarde / restauration
- État persistant = SQLite (`backend/data/app.db`) + images de référence
  (`backend/data/references/`). Sauvegarder le dossier `DATA_DIR`.
- Rétention historique automatique : `HISTORY_TTL_DAYS` (30 j) +
  `HISTORY_MAX_ROWS` (10000), purge au démarrage et toutes les 6 h.

### Rollback
- Revenir au commit/tag précédent et redéployer (images Docker reconstruites).
- Migrations DB idempotentes et additives (colonnes/index `IF NOT EXISTS`), donc
  un rollback applicatif ne casse pas un schéma déjà migré.

---

## Revue finale (clôture du programme)

- **Sécurité (Sprint 1)** : rate limiting, PII masquée, deps épinglées, security
  headers, CORS durci, exposition secret **corrigée** (proxy, Sprint 4).
- **Fiabilité (Sprint 2)** : retries Azure, timeout DeepFace, purge/index DB,
  erreur réseau frontend, nettoyage temp robuste.
- **Perf/UX (Sprint 3)** : clés stables, debounce, mémoïsation, reconnexion auto,
  tests frontend.
- **Exploitation (Sprint 4)** : logs structurés, `/metrics`, runbook, proxy R6.
- **Tests** : backend 28, frontend 6 — verts. CI : backend (pytest) + audit
  (pip-audit/npm audit) + frontend (lint/test/build).
- **Risques résiduels / suites possibles** : FK SQLite stricte (reportée, lien
  mis à NULL au delete) ; métriques sans labels (cardinalité volontairement
  faible) ; auth utilisateur forte (OAuth/JWT) non couverte — la clé API reste
  un contrôle d'accès de périmètre, pas une authentification d'utilisateur final.
