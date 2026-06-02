# Raft Order Intelligence Agent

An AI agent for the Raft AI Engineer Coding Challenge. It accepts a natural-language order request, fetches messy customer API text, structures the orders, verifies model output against source evidence, and returns deterministic clean JSON.

## What this project delivers

- Natural language query support for customer orders
- Structured JSON output with `orderId`, `buyer`, `state`, and `total`
- API schema drift protection when the customer API returns unpredictable JSON
- Model hallucination filtering and local verification
- A fallback path when the API key or LangGraph dependency is unavailable
- A local React UI for demoing the agent and a CLI mode for one-command execution

## Architecture

```text
React UI
  -> Flask agent API (/api/query)
    -> fetch_orders: call dummy customer API and extract order-like text
    -> parse_orders: chunk raw text, parse with OpenRouter or deterministic fallback, verify model output
    -> parse_request: extract filters from natural language
    -> filter_orders: apply deterministic filters and format JSON
    -> finalize: add audit, metrics, anomalies, runtime diagnostics
```

- `main.py` uses `langgraph` only if installed. When it is not installed, the code runs sequentially with the same core agent pipeline.
- The requested model is `openai/gpt-oss-120b:exacto` with `temperature=0` to reduce randomness.

## Key design decisions

- `fetch_orders()` recursively scans API JSON for any string containing the word `order` so the code does not depend on a fixed `raw_orders` key.
- `chunk_records()` protects the model from context window overflow by splitting raw order text into chunks.
- `parse_orders()` first attempts an OpenRouter model call and then verifies every returned field against the original raw source.
- `verify_or_fallback()` ensures only supported model outputs are accepted and falls back to deterministic regex extraction if needed.
- `parse_request()` uses fuzzy matching, aliases, and regex extraction to convert natural language into deterministic filters.
- `filter_orders()` applies exact state, buyer, city, total, order id, and item filters locally, with optional ranking by highest/lowest total.
- Final output is sorted and shaped in local code, preventing model-driven output ordering errors.

## Setup

From the repo root:

```bash
pip install -r requirements.txt
cd raftinterview
npm install
```

Create `.env` in the repo root:

```bash
OPENROUTER_API_KEY=your_openrouter_key
```

If you do not have an OpenRouter API key yet, the app can still run because the code includes a deterministic fallback path.

## Run

From `raftinterview/`:

```bash
npm start
```

- This launches the Flask agent API on `http://127.0.0.1:8000`
- It also launches the React UI on `http://localhost:3000`
- The React dev server proxy forwards requests to the agent API

From the repo root in CLI mode:

```bash
python3 main.py "Show me all orders where the buyer was located in Ohio and total value was over 500."
```

## Example expected output

```json
{
  "orders": [
    { "orderId": "1001", "buyer": "John Davis", "state": "OH", "total": 742.1 },
    {
      "orderId": "1003",
      "buyer": "Mike Turner",
      "state": "OH",
      "total": 1299.99
    },
    { "orderId": "1005", "buyer": "Chris Myers", "state": "OH", "total": 512.0 }
  ]
}
```

## Edge case handling

- Context overflow: `chunk_records()` limits chunk size and preserves order data.
- Hallucination control: `verify_or_fallback()` checks returned model fields against the original raw string.
- API schema drift: `extract_strings()` traverses nested JSON and finds order-like text without assuming a fixed schema.
- Determinism: model calls use `temperature=0`, and final output is filtered, normalized, and sorted in local code.

## Notes

- The dummy customer API is located at `raftinterview/dummy_customer_api.py`.
- The front-end is optional but convenient for demoing agent behavior.
- The main CLI entry point is `main.py` in the repo root.

## Interview prep

- A companion interview prep file is available at `../INTERVIEW_PREP.md`.
- It includes talking points, sample answers, design explanations, and what to say for likely questions.
