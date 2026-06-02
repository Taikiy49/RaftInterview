# Raft Order Intelligence Agent

An AI agent for the Raft AI Engineer Coding Challenge. It accepts a natural-language order request, fetches messy customer API text, structures the orders, verifies model output against source evidence, and returns deterministic clean JSON.

## Architecture

```text
React UI
  -> Flask agent API (/api/query)
    -> fetch_orders: call dummy customer API
    -> parse_orders: chunk raw text, ask OpenRouter, verify against source
    -> parse_request: convert natural language into filters
    -> filter_orders: deterministic JSON response
    -> audit/metrics: evidence, coverage, anomaly and forecast signals
```

The graph is implemented with LangGraph when installed. A sequential fallback is present so local development still works before dependencies are installed.

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

The requested model is `openai/gpt-oss-120b:exacto` with temperature `0`.

## Run

One command from `raftinterview/` starts the dummy API, the agent API, and the React UI:

```bash
npm start
```

Open http://localhost:3000.

CLI mode from the repo root:

```bash
python3 main.py "Show me all orders where the buyer was located in Ohio and total value was over 500."
```

Expected clean output:

```json
{
  "orders": [
    { "orderId": "1001", "buyer": "John Davis", "state": "OH", "total": 742.1 },
    { "orderId": "1003", "buyer": "Mike Turner", "state": "OH", "total": 1299.99 },
    { "orderId": "1005", "buyer": "Chris Myers", "state": "OH", "total": 512.0 }
  ]
}
```

## Safety

- Context overflow: raw text is chunked before extraction.
- Hallucination control: every model field must be supported by the original source string.
- API schema drift: the agent recursively searches response JSON for order-like text instead of assuming `raw_orders`.
- Determinism: final filtering, sorting, and JSON shaping are local code.
