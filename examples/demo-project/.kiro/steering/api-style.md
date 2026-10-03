---
inclusion: fileMatch
fileMatchPattern: "src/api/**"
---
# API style

- One module per resource under `src/api/`.
- Validate input at the route; return 422 with the field name on bad input.
