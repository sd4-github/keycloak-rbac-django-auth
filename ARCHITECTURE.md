# Architecture & Workflow — Keycloak RBAC / ACM → Django auth

A guided walkthrough of how this project works, written to be studied top to
bottom. Read the "Aha points" and "Exercises" at the end even if the middle is
familiar — that's where the real understanding lives.

---

## 1. The one idea everything hangs on

> **Authorization should be *data*, not *code*.**

When an org needs a new role, an admin should tick a few boxes in a UI and the
running system should behave differently on the very next request — with zero
deploys. That is only possible if the rules that decide "who may do what" live
in **rows of a database**, not in `if user.role == "manager":` statements
compiled into the binary.

Django already ships an excellent permissions-as-data system:

- **`auth_permission`** — a row per `(app, model, action)`. Auto-created:
  `view_`, `add_`, `change_`, `delete_` for every model.
- **`auth_group`** — a named bag of permissions. Users belong to groups; a
  user's effective permissions = **union** of all their groups' permissions.
- **`auth_group_permissions`** — the join table.
- `user.has_perm("erp.view_invoice")` walks those tables and answers yes/no.

Keycloak provides **identity** (who are you). This project makes Keycloak's
**access control matrix** (what may you do) live in Django's tables, and
enforces it at two layers. That's the whole project.

---

## 2. Two questions people blur together

| Question | Meaning | Who answers it |
| --- | --- | --- |
| **Authentication** | Who are you? | Keycloak (OIDC). It says "this is Priya, and she is really Priya." |
| **Authorization** | What may you do? | Django (this project). Keycloak has no opinion on whether Priya may delete an invoice. |

Authentication is settled before our API ever sees a request. Authorization is
the ERP's problem, and *where* it lives is the design decision this project
makes.

---

## 3. The Access Control Matrix (ACM) in one picture

An ACM is a grid:

```
                        OBJECTS (resources)
              invoice  orgunit  booking  payroll ...
        ┌─────────┬─────────┬─────────┬─────────┐
  S     │erp-admin│ view+add│  view   │         │
  U  ───┼─────────┼─────────┼─────────┼─────────┤
  B     │finance  │  view   │         │   view  │
  J  ───┼─────────┼─────────┼─────────┼─────────┤
  E     │pune-coo │  view   │         │         │
  C  ───┼─────────┼─────────┼─────────┼─────────┤
  T  S  │front-   │         │         │         │
       desk│         │         │         │
        └─────────┴─────────┴─────────┴─────────┘
         ^cells = one action granted to that subject on that object
```

Keycloak's authorization services express exactly this for a client: **roles**
(rows), **resources** (columns), **scopes** (cells). This project imports that
matrix into Django as an `AcmEntry` per allowed cell.

**Where each piece of the ACM lives in Django:**

| ACM concept | Django home |
| --- | --- |
| Subject (a role) | `auth.Group` |
| Object (a resource) | `ContentType` (a model) |
| Action | a permission codename (`view_`, `add_`, …) |
| Cell → grant | `auth_group_permissions` join row |
| Matrix, kept as data | `erp_acm_entry` (`AcmEntry`) |
| Role metadata (is it a dept? what does it scope to?) | `erp_group_profile` (`GroupProfile`) |

---

## 4. Data model

```
auth_user ──< auth_user_groups >── auth_group ──< auth_group_permissions >── auth_permission
                 │                     │  ▲                                   ▲
                 │               OneToOne   │  AcmEntry.subject               │
                 │                     ▼    │                                │
                 │              erp_group_profile ───────────────────────────┼── AcmEntry cell
                 │              (kind, scope)                                │    (action, allow)
                 │                     │ scope (FK)                          │
                 │                     ▼                                     │
                 │             erp_orgunit ◄──── Invoice.org_unit (FK)        │
                 │                                                            │
                 └────────────────────────────────────────────────────────────┘
                                                      AcmEntry.object_type (FK → ContentType)
```

- `auth_group` / `auth_permission` / `auth_group_permissions` — **Django's own
  tables**, reused untouched.
- `erp_group_profile` — the sidecar (what kind of group, which scope).
- `erp_acm_entry` — the matrix as data, referencing groups (subjects) and
  `ContentType` (objects).
- `erp_orgunit` — the row-scope key; `Invoice.org_unit` points into it.

### `OrgUnit` (`erp/models.py`)
A node in the org chart (Pune, Mumbai, a region…). **This is the row-scope
key.** Business rows (invoices) carry an FK to one OrgUnit.

### `GroupProfile` — the sidecar Django is missing
`auth.Group` has no place to record *what kind* of group it is. One
`OneToOneField` fixes that:

| `kind` | Meaning | `scope` |
| --- | --- | --- |
| `department` | scopes rows | **required** → the OrgUnit whose rows this group may see |
| `position` | a named bundle of permissions | `NULL` |
| `keycloak_role` | mirrored from Keycloak | `NULL` |

A `CheckConstraint` enforces: *department must have a scope; positions and
Keycloak roles must not* — the database itself refuses nonsense rows.

### `AcmEntry` — the matrix as data
One row per cell: `subject (Group) × object_type (ContentType) × action
(view/add/change/delete)`, plus an `allow` flag (deny rows exist too). Saving
an entry **materialises** it: it writes (or removes) the matching
`auth_group_permissions` row.

### `Invoice` — the demo resource
Row-scoped business object. Every invoice belongs to exactly one `OrgUnit`.

---

## 5. The two layers of enforcement

Django's built-in permissions are **model-level**: `view_invoice` is
all-or-nothing across the whole table. An ERP actually needs **row-level**
permissions: the Pune coordinator may view invoices, but only Pune's. The right
tool for "which rows" is not a permission check — it's a `WHERE` clause. So
authorization happens in **two layers**:

```
 Request ──► Layer 1: model-level gate          Layer 2: row scope
             has_perm("view_invoice")?   ──►    WHERE org_unit_id IN (your departments)
             │                                   │
             ├─ denied  ──► 403 (query never runs)   └─ executed on an index, in SQL
             └─ allowed ───────────────────────────────────────────► rows you may see
```

### Layer 1 — `DepartmentScopedModelPermissions` (`erp/permissions.py`)
Subclasses DRF's `DjangoModelPermissions`, fixing its two gaps:
1. It leaves `GET` **unguarded** → we map `GET` to `view_<model>`.
2. It has no **object-level** hook → we implement `has_object_permission` as a
   per-object backstop on detail routes.

`has_permission` maps the HTTP method to a codename and calls `has_perm`.
Fail → 403, the query never runs.

### Layer 2 — the queryset (`erp/views.py`, `InvoiceViewSet.get_queryset`)
```
qs = Invoice.objects.select_related("org_unit")
if user.has_perm("erp.view_all_invoices"):   # org-wide bypass role
    return qs
dept_ids = user.groups.filter(profile__kind="department")
                   .values_list("profile__scope_id", flat=True)
return qs.filter(org_unit_id__in=list(dept_ids))
```
One indexed `WHERE`, in SQL — never a Python loop over rows.

> **Key insight:** *neither layer contains a role name.* There is no
> `if user.is_accountant`. The code only knows **permissions**
> (`view_all_invoices`) and a **scope key** (`org_unit_id`) — the stable
> vocabulary. Which Keycloak roles hold which of those is *data*.

### Why not django-guardian?
Guardian stores a row per `(user-or-group, object, permission)` triple — the
right tool when *every object carries its own ACL* (a doc shared with these
five people). Wrong tool here: our rule is **per-department**, so the scope is
one indexed `WHERE org_unit_id IN (…)` with zero extra rows. Guardian over a
200k-row table would mean millions of ACL rows to write and keep in sync.

---

## 6. Keycloak → Django mapping

### Login-time sync (OIDC) — `erp/backend.py`
`mozilla_django_oidc` runs the standard code flow. We subclass its backend and
hook `get_or_create_user`:

```
Keycloak ID token / userinfo
   ├─ realm_access.roles        ─┐
   ├─ resource_access[erp-api].roles ──► group_names_from_claims() ──► set of principal names
   └─ groups                     ─┘                    │
                                             get_or_create Django Group per name
                                             (GroupProfile kind=keycloak_role)
                                             │
                                             ▼
                                     user.groups.set(groups)
```

Every successful login **replaces** the user's Django groups with exactly their
Keycloak principals. The groups are created on first sight, so a role that
exists in Keycloak but not Django is a phantom no one can use.

### Bearer-token sync (DRF) — `erp/authentication.py`
`KeycloakBearerAuthentication` authenticates each API request's access token
through the same backend, which **re-syncs groups on every request**. That is
what makes permission changes live on the *very next request* — no restart, no
cache flush.

### Admin-API path — `erp/keycloak.py`
For bootstrapping and users who've never logged in, `sync_keycloak` uses the
Keycloak admin REST API (`python-keycloak`) to list every realm role, client
role and group, and mirror them into Django groups.

### The `grants` bypass
Some permissions aren't a single model/action cell — e.g.
`erp.view_all_invoices` (see everything, org-wide) or `erp.view_acm` (audit the
matrix). These are custom codenames defined in `Meta.permissions` and applied
directly to a role via the `grants` list in the matrix file.

---

## 7. The ACM as data — lifecycle of a grant change

```
 admin edits acm_matrix.json
        │
        ▼
 manage.py import_acm acm_matrix.json
        │  for each role × resource × action:
        │     get_or_create Group
        │     write AcmEntry row            ── the matrix, kept as data
        │     write auth_group_permissions  ── materialised grant
        ▼
  next request
        │
        ▼
 request.user (freshly loaded) ──► has_perm() ──► SQL join:
        SELECT 1
        FROM   auth_group_permissions gp
        JOIN   auth_user_groups       ug ON ug.group_id = gp.group_id
        JOIN   auth_permission         p  ON p.id = gp.permission_id
        WHERE  ug.user_id = %s
        AND    p.codename = 'view_invoice'
        LIMIT  1;
```

Re-importing a **revised** matrix is an *update*: each subject's old
entries/grants are revoked first, so a narrower grant actually stops being
enforced. (See `_revoke_entries` in `erp/acm.py` — a subtle bug class.)

---

## 8. Request lifecycle (API)

```
 Client (Bearer token / session)
   │
   ▼
 DRF authentication  ── KeycloakBearerAuthentication
   │  validates access token against Keycloak
   │  re-syncs user's Keycloak roles → Django groups   (fresh authZ data)
   ▼
 Layer 1  DepartmentScopedModelPermissions.has_permission
   │  GET     → erp.view_invoice
   │  POST    → erp.add_invoice
   │  PATCH   → erp.change_invoice
   │  DELETE  → erp.delete_invoice
   │  ── no perm ──► 403 (query never runs)
   ▼
 get_queryset  Layer 2
   │  has view_all_invoices?  →  all rows
   │  else                    →  WHERE org_unit_id IN (my department scopes)
   ▼
 detail route?  has_object_permission backstop (row isolation even if a view
   │            forgets to scope its queryset)
   ▼
 serializer → JSON
```

---

## 9. Component map

| File | What it is |
| --- | --- |
| `config/settings.py` | Keycloak/OIDC/DRF settings, `ACM_RESOURCE_MODELS` |
| `erp/models.py` | `OrgUnit`, `GroupProfile`, `AcmEntry`, `Invoice` |
| `erp/acm.py` | ACM import engine: JSON matrix → groups + `auth_group_permissions` |
| `erp/keycloak.py` | OIDC-claims → groups; admin-API sync |
| `erp/backend.py` | `KeycloakOIDCBackend` (login-time group sync) |
| `erp/authentication.py` | DRF Bearer auth (per-request group sync) |
| `erp/permissions.py` | `DepartmentScopedModelPermissions`, `AcmAuditPermission` |
| `erp/views.py` | `InvoiceViewSet` (Layer-2 scoping), `AcmViewSet` (audit) |
| `erp/admin.py` | One screen to edit a group's profile + ACM matrix (the "no deploy" UI) |
| `acm_matrix.json` | The Keycloak ACM, as importable data |
| `erp/management/commands/*` | `import_acm`, `sync_keycloak`, `seed_demo` |

---

## 10. The three things that bite people (footguns)

1. **Codename collisions across apps.** `view_comment` can exist in three
   apps. A bare `codename__in` filter would attach *every* collision, silently
   over-granting. This project resolves resources through `ACM_RESOURCE_MODELS`
   → `ContentType`, so a codename can only resolve to the intended model.

2. **Django caches permissions on the user instance.** The first `has_perm`
   populates `user._perm_cache`; subsequent checks in the same request reuse it.
   Across requests this is harmless (each request builds a fresh user). It only
   lies *within one long-lived process* that both mutates permissions and
   re-checks them. Fix:
   ```python
   for attr in ("_perm_cache", "_user_perm_cache", "_group_perm_cache"):
       user.__dict__.pop(attr, None)
   ```
   `sync_user_from_claims` does exactly this after changing groups.

3. **Mutating shared input data.** `import_matrix` must not
   `resources.pop("grants")` — that would destroy the caller's dict (a test
   caught this exact bug). Read with `.get()`, never mutate the input.

---

## 11. Run it / try it

```bash
source .venv/bin/activate
python manage.py migrate
python manage.py seed_demo                     # admin@erp.test / admin
python manage.py import_acm acm_matrix.json    # ACM → Django auth
python manage.py runserver                     # http://localhost:8000/api/

# login via Django admin, then:
curl -u admin@erp.test http://localhost:8000/api/invoices/   # org-wide: 2 rows
```

**Watch this to "get" the whole design:** open `/admin/`, edit the
`pune-coordinator` group, untick `change_invoice`, save, and hit the API as a
coordinator — the very next request no longer allows updates. You changed a
**row**, not a deploy.

---

## 12. Exercises to test your understanding

1. **Add a new resource.** Create a `Booking` model, register
   `"booking": "erp.booking"` in `ACM_RESOURCE_MODELS`, grant roles in
   `acm_matrix.json`, re-import. Why does nothing in the views/perms change?
2. **Make a row-scoped view without reading the answer first.** Write a new
   viewset; the *only* changes you should need are `permission_classes = [DepartmentScopedModelPermissions]`
   and a scoped `get_queryset`. Explain why Layer 1 alone is insufficient.
3. **Draw the request path** for a `DELETE /api/invoices/42/` as a coordinator
   from a different OrgUnit. Which layer returns what, and why?
4. **Explain the cache footgun** to someone else using the "one long-lived
   process" scenario (a Celery worker granting then checking).
5. **Why can't Keycloak answer "may Priya delete invoice #42"?** Where does
   that opinion live instead?

---

## 13. Glossary

- **OIDC** — OpenID Connect; the identity layer on top of OAuth2 that this
  project uses to log users in via Keycloak.
- **RBAC** — Role-Based Access Control; access decided by *roles* a user holds.
- **ACM** — Access Control Matrix; the full subject × object × action grid.
- **Scope** — the set of rows a subject may touch (here: their departments).
- **has_perm / codename** — Django's permission lookup; `erp.view_invoice` =
  app `erp`, model `invoice`, action `view`.
- **Materialise** — writing an `AcmEntry` (or matrix cell) out to the actual
  `auth_group_permissions` join rows that `has_perm` reads.