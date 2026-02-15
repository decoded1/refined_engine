# Navigating phemex_v5_api.json

## Structure

A single JSON array. The hierarchy IS the data — no separate lookup needed.

```
Array of 15 categories (H1)
  └── .items[] — endpoints or subsections (H2/H3) with full content inline
        └── .items[] — deeper subsections (if any)
```

Every endpoint node can have: `name`, `id`, `http_method`, `http_path`, `description`, `request_parameters`, `response_parameters`, `examples`, `notes`, `extra_tables`, `extra_text`.

Parameter tables are `{headers, rows}` where each row is an object keyed by header name.

Companion file: **`phemex_index.txt`** — 249-line flat grep-able index for fast keyword search.

---

## Quick recipes

### Discovery (grep the flat index first)

```bash
grep -i "balance" phemex_index.txt
grep -i "cancel" phemex_index.txt
grep -i "/g-orders" phemex_index.txt
grep -i "websocket" phemex_index.txt
```

### Browse the tree

```bash
# All categories
jq '.[] | .name' phemex_v5_api.json

# Endpoints under a category
jq '.[] | select(.name == "USDⓈ-M Perpetual Rest API") | .items[] | .name' phemex_v5_api.json

# Endpoints under a sub-category
jq '.[] | select(.name == "Overview") | .items[] | select(.name == "REST API Standards") | .items[] | .name' phemex_v5_api.json
```

### Get endpoint details

```bash
# By index path (fastest — zero search)
jq '.[3].items[2]' phemex_v5_api.json  # USDⓈ-M > Place order (PUT)

# By ID (recursive search)
jq '.. | objects | select(.id? == "place-order-http-put-prefered-2")' phemex_v5_api.json

# By API path
jq '.. | objects | select(.http_path? == "/g-orders/create")' phemex_v5_api.json
```

### Pull specific fields

```bash
# Summary
jq '.. | objects | select(.id? == "ID") | {name, http_method, http_path, description}' phemex_v5_api.json

# Request parameters
jq '.. | objects | select(.id? == "ID") | .request_parameters.rows[]' phemex_v5_api.json

# Response examples
jq -r '.. | objects | select(.id? == "ID") | .examples[] | select(.label == "Response sample") | .code' phemex_v5_api.json

# Notes/caveats
jq '.. | objects | select(.id? == "ID") | .notes[]' phemex_v5_api.json
```

### Cross-cutting searches

```bash
# All PUT endpoints
jq '[.. | objects | select(.http_method? == "PUT")] | .[] | .name + " " + .http_path' phemex_v5_api.json

# All DELETE endpoints
jq '[.. | objects | select(.http_method? == "DELETE")] | .[] | .name + " " + .http_path' phemex_v5_api.json

# Find endpoints by path prefix
jq '[.. | objects | select(.http_path? // "" | startswith("/g-orders"))] | .[].name' phemex_v5_api.json

# Search descriptions
jq -r '.. | objects | select(.description? // "" | test("margin"; "i")) | .name' phemex_v5_api.json
```

---

## Workflow

1. `grep -i "keyword" phemex_index.txt` → find the ID
2. `jq '.. | objects | select(.id? == "THE-ID")' phemex_v5_api.json` → get everything
3. Narrow to the field you need (`.request_parameters.rows`, `.examples[0].code`, etc.)
