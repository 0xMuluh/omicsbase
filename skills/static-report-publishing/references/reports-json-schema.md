# reports.json Schema

Use this reference when editing a static report portal registry.

## Required Fields

Each report entry should include:

- `key`: stable unique identifier, lowercase letters, digits, and hyphens
- `title`: official report title
- `summary`: concise scientific summary
- `visibility`: `Public` or `Protected`
- `updated`: `YYYY-MM-DD`
- `area`: research area
- `authors`: array of author labels
- `tags`: array of searchable tags
- `reportUrl`: report route or URL

Optional fields:

- `status`: publication or collaboration state
- `githubUrl`: source repository link
- `manuscript`: object with `label` and optional `url`, or `null`
- `draft`: boolean; `true` hides the entry in production-facing listings when the portal supports it

## Rules

- Keep `key` unique.
- Keep `updated` ISO-formatted.
- Do not change `visibility` from `Protected` to `Public` without explicit user instruction.
- Keep `reportUrl` aligned with the hosting route.
- Do not leave template/example entries visible unless intentionally published.
