# Keycloak RBAC → Django auth (ACM as data)

A Django + DRF project that treats **Keycloak's access control matrix (ACM) as
data**, mirrors it into Django's own `Group` / `Permission` tables, and enforces
it in two layers: a DRF model-level gate + row scoping in SQL.

Built on the pattern from
[*Permissions as Data: The ERP the Org Rewires Without an Engineer*](https://kdpisda.in/erp-dynamic-permissions-with-django-groups/)
by Kuldeep Pisda.

## How it works

```
Keycloak (identity + ACM source)          Django (authorization, as data)
──────────────────────────────            ────────────────────────────────
realm roles / client roles / groups  ──►  auth.Group  (auto-created on login)
   │                                        │  GroupProfile: kind (dept /
   │                                        │    position / kc role) + scope
   │   acm_matrix.json (subject x object x action)
   └── import_acm ───────────────────────►  AcmEntry rows + auth_group_permissions
                                              │
                                              ▼
                                  user.has_perm() ─ Layer 1 (DRF gate, 403)
                                              │
                                              ▼
                                  get_queryset().filter(org_unit__in=...) ─ Layer 2 (row scope in SQL)
```

- **Authentication** (who are you) — `mozilla_django_oidc` code flow against
  Keycloak; DRF accepts Keycloak Bearer tokens. Every login/request re-syncs
  the subject's Keycloak principals into Django groups.
- **Authorization** (what may you do) — the ACM lives in Django data:
  `AcmEntry` (one row per subject × object × action) materialised into
  `auth_group_permissions`. Editing the matrix is a data write; the change is
  live on the next request. No code change, no deploy.
- **Row scope** — departments are groups with a `GroupProfile.scope`; Layer 2
  filters `WHERE org_unit_id IN (…your departments…)` on an index.

## Layout

| Path | Purpose |
| --- | --- |
| `erp/models.py` | `OrgUnit`, `GroupProfile`, `AcmEntry` (the ACM), `Invoice` |
| `erp/acm.py` | ACM import engine (`subject x resource x action` -> groups/perms) |
| `erp/keycloak.py` | Keycloak admin sync + OIDC claims -> Django group mapping |
| `erp/backend.py` | `KeycloakOIDCBackend` (login-time group sync) |
| `erp/authentication.py` | DRF Bearer auth against Keycloak |
| `erp/permissions.py` | `DepartmentScopedModelPermissions` (Layer 1 + object backstop) |
| `erp/views.py` | `InvoiceViewSet` with Layer-2 row scoping in `get_queryset` |
| `acm_matrix.json` | Sample Keycloak ACM to import |
| `docker-compose.yml` | Keycloak + Postgres for local dev |

## Quick start

```bash
# 1. dependencies
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. configure
cp .env.example .env          # defaults work for local Keycloak (docker)

# 3. local run without Keycloak (sqlite, session auth)
python manage.py migrate
python manage.py seed_demo                 # admin@erp.test / admin + invoices
python manage.py import_acm acm_matrix.json  # ACM -> Django groups/permissions
python manage.py runserver                 # http://localhost:8000/api/
```

Then log into `/admin/`, edit a group's permissions — the running API honours
the change immediately. `erp-admin` (org-wide) sees every invoice; a
`pune-coordinator` role sees only Pune's rows.

## With a real Keycloak

```bash
docker compose up -d          # Keycloak on :8080, admin / admin
```

1. Create realm `erp`, a client `erp-api` (confidential, standard flow).
   For the admin API path, use a realm-management-enabled service account.
2. Create roles matching `acm_matrix.json` (or your own) and assign users.
3. Put the client secret in `.env` (`KEYCLOAK_CLIENT_SECRET`).
4. `python manage.py sync_keycloak` — mirrors every Keycloak role/group into
   Django groups, then `python manage.py import_acm acm_matrix.json`.

Test the Bearer path:

```bash
TOKEN=$(curl -s -d grant_type=password -d client_id=erp-api \
  -d client_secret=$SECRET -d username=$USER -d password=$PW \
  http://localhost:8080/realms/erp/protocol/openid-connect/token | jq -r .access_token)
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/invoices/
```

## The ACM (access control matrix)

`acm_matrix.json` mirrors Keycloak client authorization: **rows** are roles
(subjects), **columns** are resources (Django models via `ACM_RESOURCE_MODELS`
in settings), **cells** are actions (`view`/`add`/`change`/`delete`). The
special `grants` key carries org-wide Django codenames such as
`erp.view_all_invoices`.

```json
{
  "roles": {
    "erp-admin": {
      "grants": ["erp.view_all_invoices", "erp.view_acm"],
      "invoice": ["view", "add", "change", "delete"],
      "orgunit": ["view"]
    },
    "pune-coordinator": { "invoice": ["view", "change"] }
  }
}
```

`python manage.py import_acm acm_matrix.json` turns every cell into an
`AcmEntry` row and the matching `auth_group_permissions` row. Re-importing a
revised matrix is an update (each subject's grants are replaced).

## Two layers of enforcement

1. **Layer 1 — the gate.** `DepartmentScopedModelPermissions` maps the HTTP
   method to a codename (`GET`→`view_…`, `POST`→`add_…`, …) and calls
   `has_perm`. Denied → 403, the query never runs. `has_object_permission`
   is a per-object backstop on detail routes.
2. **Layer 2 — the scope.** `InvoiceViewSet.get_queryset` returns all rows for
   subjects with `erp.view_all_invoices`, otherwise
   `.filter(org_unit_id__in=your_department_ids)` — one indexed `WHERE`, in
   SQL, never a Python loop.

Neither layer contains a role name. The code only knows permissions and the
scope key; which Keycloak principals hold which of those is data.

## Tests

```bash
python manage.py test erp
```

Covers the ACM import/re-import, claims→group mapping, Layer-1 denials,
Layer-2 row scoping, foreign-row 404s, and the ACM audit endpoint.