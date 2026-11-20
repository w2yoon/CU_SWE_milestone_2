## Conventions (apply to every endpoint below)

- Base `http://127.0.0.1:8000` (nodes `:8000 :8001 :8002` all serve the same data). Routes under `/api/...`, JSON in/out.
- Auth header `Authorization: Bearer <token>`. Missing/invalid token on an authenticated route → `401`.
- Success: `200` read/update, `201` create. Errors: `400` validation, `401` auth, `403` forbidden, `404` unknown id, `409` conflict, `413` upload too large, `423` archived channel write.
- Responses use one top-level resource key (`user`, `message`, `file`, `group`, `groups`, `reactions`, `channel`, ...).
- Check order for every route: **401 (auth) → 404 (unknown id) → 403 (permission) → 400 (body validation)**.
- Timestamps: UTC ISO-8601 with `Z`, e.g. `2026-05-16T08:07:34.123Z`.
- ALL state lives in the shared SQLite file (or files under `/app/data`), never in process memory: any node may be killed at any moment.
