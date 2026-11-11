# Hualian Ordering | 华联服装辅料在线接单

English · [简体中文](README.zh-CN.md)

An AI-assisted B2B ordering prototype for Hualian Garment Accessories, Fuzhou. It combines a self-hosted Ollama model, a 100-SKU catalogue, procurement-text parsing, a multi-item cart and a sales dashboard to turn purchasing requests into validated orders.

面向华联服装辅料福州分公司的接单试用项目：结合自部署模型、商品检索、智能粘贴、采购车和销售后台，将采购需求整理为可核对的订单。

**Status:** deployed for trial use. Availability, final prices and actual delivery dates require sales confirmation. This is a prototype, with no claim of production-scale operation.

## Features

- **AI consultation:** ask general questions, search public references or get product suggestions by purpose, category, material, colour and dimensions. Product suggestions come from the catalogue. Chat has a daily allowance of 150 messages and leaves purchase fields and the cart to the buyer.
- **Drafts and carts:** find products by SKU or name in a single search-and-select field. Edit quantities, per-item delivery dates and notes of up to 100 characters, or update delivery dates in bulk. Drafts can be recovered from the browser session.
- **Smart paste:** parse procurement text or rows copied from Excel. Rules run first; Qwen assistance is enabled by default and can help split complex text when a GPU is available. Model failures preserve rule-based results for review. Supports shared dates and separate per-item dates, with searchable product selection in each review row.
- **Buyer and guest workflows:** internally issued buyer accounts see only their own orders and can reorder. New customers submit an inquiry without an account and provide at least one contact method.
- **Sales and administration:** sales staff filter and review inquiries and buyer orders, update order status, undo the latest status change and export Excel. Staff can remove orders and inquiries with a second confirmation and an audit record. Administrators manage products, accounts and bound customer names.
- **Order notifications:** China-time timestamps and daily sequential order IDs, Excel workbooks and a retryable SMTP outbox. Sales can pause notifications, change the recipient or clear pending messages with confirmation.
- **Deployment and validation:** one SSH command validates, backs up, uploads, restarts and checks the application, with an attempted source rollback on failure.

## Stack and request flow

Python 3.9+ standard library, SQLite, HTML/CSS/JavaScript and Ollama. The model runs as a separate service, with Qwen3-8B as the application default.

```text
Consultation: browser -> Python API -> catalogue / Ollama / public web search -> answer
Ordering: product search or smart paste -> buyer review -> cart -> server validation -> SQLite
Fulfilment: sales dashboard -> status updates, Excel export and SMTP notification queue
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

Search for `T5` in the product field, select the black 12.4 mm snap fastener, enter 2,000 sets and choose a delivery date at least seven days ahead. Add the item to the cart and review it before submitting.

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

The regression suite covers catalogue and chat routing, quota limits, purchase validation, paste parsing, order ownership, role permissions, product administration, status changes, record deletion, mail settings and deployment transport. It uses temporary databases and mocked mail delivery. See the [test notes](tests/README.md).

## Cloud deployment

Configure the local connection files in `.deploy/`, then run:

```sh
./deploy --dry-run  # Validate local sources only
./deploy            # Upload, back up, restart and check
```

See the [deployment guide](ops/DEPLOYMENT.md) and [SSH troubleshooting record](ops/SSH-REPAIR.md). The existing Compshare instance starts Ollama and the website at boot; full model-backed chat uses GPU mode. Source deployments preserve the server's order database, private configuration and model weights.

## Upgrading the Ollama model

The default is `qwen3:8b`. `qwen3.8:27b` is a candidate upgrade with Apache-2.0 open weights. The Ollama package is about 18 GB on disk. The current RTX 4090 has 24 GB of VRAM, so check GPU placement, memory usage and latency with the application's 8,192-token context before switching. [Ollama model page](https://ollama.com/library/qwen3.8:27b) · [Ollama context and GPU checks](https://docs.ollama.com/context-length)

1. Open a terminal on the cloud GPU instance and test the candidate alongside the installed 8B model.

   ```sh
   ollama pull qwen3.8:27b
   ollama run qwen3.8:27b 'Explain the difference between snap fasteners and sew-on buttons in Chinese.'
   ollama ps
   ```

   Check `PROCESSOR` in `ollama ps`. `100% GPU` means the loaded model is fully on the GPU for that run. In a trial configuration, compare the same catalogue queries and smart-paste examples on both models. Check returned SKUs, quantities, delivery dates and response time.

2. When the candidate passes, add or replace this line in the server's `/workspace/ordering-preview/preview.env` and restart the ordering service. The usual `./deploy` command from an approved local checkout also restarts it and runs health checks.

   ```sh
   export OLLAMA_MODEL=qwen3.8:27b
   ```

3. Keep `qwen3:8b` installed for rollback and for the existing deployment check. To revert, set `OLLAMA_MODEL=qwen3:8b` in the same file and restart the service again.

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
