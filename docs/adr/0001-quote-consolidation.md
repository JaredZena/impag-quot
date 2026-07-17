# ADR 0001 — One quote spine, one costing engine

Status: Accepted (2026-07-16)

## Context

The app grew **three overlapping "quote" concepts** with no foreign keys between
them and free-text `customer_name`/`customer_phone` scattered across all of them:

1. **`Quote` / `QuoteItem`** — the trackable, customer-facing quote: lifecycle
   timestamps (`sent_at`/`viewed_at`/`accepted_at`), `payment_status`,
   `access_token` (shareable link), `customer_phone`, `quote_number` (`TEC-2026-…`).
2. **`Quotation`** — the RAG/LLM output: a Markdown dual quotation produced by the
   quotation chat, stored as text.
3. **`Balance` / `BalanceItem`** — a de-facto **bill-of-materials / costing** tool:
   line items against `supplier_product`, already carrying the 4 freight-leg
   columns and margin.

Meanwhile the Google Sheets use `COT-IMPAG-{seq}{MMYY}DGO-…` for quotes and
`NOT-IMPAG-…` for notas/sales. Left unconsolidated, every new feature
(customers, projects, sales ledger, AR, follow-up) would fork foreign keys three
ways and then need rewiring.

## Decision

Declare one home per concern; **all new revenue features target only these**:

- **`Quote` = the single trackable revenue spine.** It already owns lifecycle,
  payment, access token, phone. Customer/lead links (`customer_id`), the folio,
  and receivables hang off `Quote`.
- **`Balance` / `BalanceItem` = the single costing / BOM engine.** It already
  models a material list against `supplier_product` with freight legs. The
  **`project` primitive extends `Balance`** (adds `customer_id`, quote/folio link,
  `cost_category`, crew) — it is **not** a new 5th line-item table. `project_item`
  is a thin view/wrapper over `BalanceItem`.
- **`Quotation` (Markdown) = a *rendering* of a `Quote`,** not a separate source of
  truth. Chat-generated quotes should materialize (or link to) a `Quote`.
- **Folios:** `COT-IMPAG…` (quote) and `NOT-IMPAG…` (nota/sale) normalize onto a
  parsed folio primitive that maps to `Quote.quote_number` (store the sheet-scheme
  folio; keep `TEC-` as legacy/alias). The folio is the reconciliation key across
  the sales, cash, and AR ledgers.

## Consequences

- No new "quote" concept is ever added; a would-be new line-item table becomes a
  view over `Balance`.
- New foreign keys (`customer_id`, folio) land on `Quote` and `Balance` only.
- The quotation chat's output is tied back to a `Quote` so tracking, follow-up,
  and AR all key off one entity.
- This is a **decision + thin adapter**, not a rewrite: existing rows are
  reconciled incrementally as each downstream feature is built (per the
  strangler-fig migration plan).
