# Hualian Ordering | 华联服装辅料在线接单

English · [简体中文](README.zh-CN.md)

An AI-assisted B2B ordering prototype for Hualian Garment Accessories, Fuzhou. It combines self-hosted Qwen3-8B, a 100-SKU catalogue, procurement-text parsing, a multi-item cart and a sales dashboard to turn purchasing requests into validated order drafts.

面向华联服装辅料福州分公司的接单试用项目：结合自部署模型、商品检索、智能粘贴、采购车和销售后台，将采购需求整理为可核对的订单。

**Status:** deployed for trial use. Availability, final prices and actual delivery dates require sales confirmation. This is a prototype, with no claim of production-scale operation.

## Features

- **Use-case recommendations:** retrieve products by purpose, category, material, colour and dimensions. Qwen receives a bounded set of catalogue descriptions and asks for missing information.
- **Drafts and carts:** editable quantities, per-item delivery dates and notes of up to 100 characters. A completed draft is automatically saved to the cart when the customer starts another product inquiry; incomplete drafts are retained with a reminder. Includes bulk date editing and browser-session recovery.
- **Smart paste:** parse procurement text or rows copied from Excel. Rules run first; Qwen assistance is enabled by default and can help split complex text when a GPU is available. Model failures preserve rule-based results for review. Supports shared dates and separate per-item dates.
- **Buyer and guest workflows:** internally issued buyer accounts see only their own orders and can reorder. New customers submit an inquiry without an account and provide at least one contact method.
- **Sales and administration:** sales staff review inquiries and buyer orders, update four order statuses, undo the latest status change and export Excel. Staff can remove orders and inquiries with a second confirmation; records and deletion audits remain stored, linked records are preserved, and unsent notifications stop for removed orders. Only administrators manage accounts and bound customer names.
- **Order notifications:** China-time timestamps and daily sequential order IDs, Excel workbooks and a retryable SMTP outbox. Sales can pause notifications and change the recipient.
- **Deployment and validation:** one SSH command validates, backs up, uploads, restarts and checks the application, with an attempted source rollback on failure. The current regression suite contains 60 tests.

## Stack and request flow

Python 3.9+ standard library, SQLite, HTML/CSS/JavaScript, Ollama and Qwen3-8B. The web application has no third-party Python package requirements; the model runs as a separate service.

```text
Browser: AI chat / smart paste / manual selection
  -> Python API: catalogue retrieval and field extraction
  -> Ollama / Qwen3-8B: constrained structured output
  -> Server validation: products, quantities, units, dates and permissions
  -> Item draft -> cart -> SQLite
  -> Excel export and SMTP notification queue
```

Retrieval uses local SKU, category, purpose, keyword and dimension matching. The model receives relevant records rather than the entire catalogue on every turn. Model output is checked against catalogue values and server-side order validation.

## Run locally

These credentials are for a local demonstration. Demo mode uses deterministic rules, so the UI and order workflow can be tested while the GPU server is offline.

```sh
DEMO_MODE=1 ORDERS_DB=./data/demo.sqlite3 \
PREVIEW_USER=preview PREVIEW_PASSWORD=preview \
SALES_USER=admin SALES_PASSWORD=admin \
python3 server.py
```

- Buyer entry: <http://127.0.0.1:8765/>, local demo credentials `preview` / `preview`.
- Guest inquiry: <http://127.0.0.1:8765/?guest=1>.
- Administrator entry: <http://127.0.0.1:8765/admin/login>, local demo credentials `admin` / `admin`.
- Sales entry: <http://127.0.0.1:8765/sales/login>; an administrator can issue sales accounts.

Try `我要2000套黑色四合扣` (2,000 sets of black snap fasteners), select T5 and enter a delivery date at least seven days ahead. Add it manually or start an inquiry about a different product to save the completed draft automatically.

For model-backed chat, use `DEMO_MODE=0`. The defaults are `OLLAMA_BASE_URL=http://127.0.0.1:11434` and `OLLAMA_MODEL=qwen3:8b`. GPU integration tests and rule-based local tests are separate.

## Catalogue and provenance

`catalog.json` contains **100 SKUs across 10 categories**, including model, size and colour variants from public listings: 65 have public model/specification information, 29 still have specifications to confirm, and six retain legacy records awaiting sales review. It does not provide live stock or sales prices.

The import used public KAM/Hualian supplier listings. Taobao's dynamic pages did not yield importable item details, so the dataset is not described as 100 scraped Taobao products. Sources are retained internally and omitted from customer product cards.

| File | Purpose |
| --- | --- |
| `catalog.json` | Runtime products, descriptions, materials, keywords and selection notes |
| `catalog_search.py` | Local retrieval, material/dimension checks and bounded model context |
| `tools/catalog-baseline.json` | Reproducible legacy baseline preserving historical SKU/unit/colour keys |
| `research/public-products.json` | Saved public product facts |
| `research/catalog-import.json` | Import counts, verification status and source limitations |

```sh
python3 tools/build_catalog.py
```

The rebuild uses saved facts and the versioned baseline, backs up the current catalogue and does not access the network or edit orders. Unknown models remain unselected; missing dimensions are not guessed; package and set quantities are not silently converted.

## Tests

```sh
for test in tests/test_*.py; do
  PYTHONDONTWRITEBYTECODE=1 python3 "$test" || exit 1
done
```

The current **60 tests** cover retrieval, draft transitions, invalid quantities/dates, paste parsing, order ownership, role permissions, status changes, record deletion and its permission/notification boundaries, notification settings and deployment transport. They use temporary databases and mocked mail delivery, without submitting live orders or sending real email. See the [test notes](tests/README.md).

## Cloud deployment

Configure the local connection files in `.deploy/`, then run:

```sh
./deploy --dry-run  # Validate local sources only
./deploy            # Upload, back up, restart and check
```

See the [deployment guide](ops/DEPLOYMENT.md) and [SSH troubleshooting record](ops/SSH-REPAIR.md). The existing Compshare instance starts Ollama and the website at boot; full model-backed chat uses GPU mode. Source deployments preserve the server's order database, private configuration and model weights.

## Order, email and access rules

- Each item requires a catalogue SKU, an integer quantity from 1 to 1,000,000 and a delivery date at least seven days ahead. An order has at most 100 lines. Actual delivery remains subject to sales confirmation.
- Identical SKUs and dates may remain separate lines. Request IDs prevent network retries from creating duplicate orders.
- Reordering copies available products, quantities and notes from the current buyer's own order, clears old delivery dates and requires review. It does not submit automatically.
- A guest inquiry starts unconfirmed. Moving it to a later status creates a linked formal order in one database transaction. Undoing a status does not delete the order or recall email.
- SMTP credentials are configured privately on the server. Sales can change the recipient and pause notifications; settings survive restarts. Pausing mail does not block order storage, and eligible queued jobs resume when enabled.
- Customer names are bound to buyer accounts and enforced by the backend. Passwords use PBKDF2; failed logins trigger CAPTCHA and throttling. See the [access-control notes](ops/SECURITY.md) for the implemented limits.

## Repository scope and project overview

Business names, product information, source records and work logs are retained as written. Runtime databases, model weights, deployment private keys and SMTP credentials remain in local/server configuration and are managed separately.

[English project overview](https://jinghua-xi.github.io/projects/hualian-ordering.html) · [Personal website](https://jinghua-xi.github.io/)
