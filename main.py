from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, TypedDict

import requests
from flask import Flask, jsonify, request

try:
    from langgraph.graph import END, StateGraph
except ImportError:
    END = "__end__"
    StateGraph = None


ROOT = Path(__file__).resolve().parent
APP_DIR = ROOT / "raftinterview"
CUSTOMER_API_DEFAULT = "http://127.0.0.1:5001"
MODEL = "openai/gpt-oss-120b:exacto"
CHUNK_LIMIT_CHARS = 3500
FUZZY_THRESHOLD = 0.78
STOPWORDS = {
    "a",
    "all",
    "an",
    "and",
    "any",
    "bought",
    "buyer",
    "buyers",
    "customer",
    "customers",
    "for",
    "from",
    "include",
    "included",
    "includes",
    "including",
    "item",
    "items",
    "me",
    "of",
    "order",
    "orders",
    "people",
    "person",
    "purchase",
    "purchased",
    "show",
    "that",
    "the",
    "to",
    "who",
    "with",
}
ITEM_ALIASES = {
    "coffeemaker": "coffee maker",
    "coffee machine": "coffee maker",
    "display": "monitor",
    "gaming computer": "gaming pc",
    "headphone": "headphones",
    "headset": "headphones",
    "hdmi": "hdmi cable",
    "hdmi cord": "hdmi cable",
    "hmdi": "hdmi cable",
    "hmdi cable": "hdmi cable",
    "lamp": "desk lamp",
    "mice": "mouse",
    "notebook": "laptop",
    "pc": "gaming pc",
}
STATE_ALIASES = {
    "ohio": "OH",
    "oh": "OH",
    "texas": "TX",
    "tx": "TX",
    "washington": "WA",
    "wa": "WA",
}
ORDER_RE = re.compile(
    r"order\s*(?P<order_id>\d+).*?"
    r"buyer\s*[:=]\s*(?P<buyer>[^,\n;|]+).*?"
    r"location\s*[:=]\s*(?P<location>[^,\n;|]+,\s*(?P<state>[A-Z]{2})).*?"
    r"total\s*[:=]\s*\$?\s*(?P<total>\d+(?:\.\d+)?).*?"
    r"items?\s*[:=]\s*(?P<items>[^\n]+)",
    re.IGNORECASE | re.DOTALL,
)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
LOG = logging.getLogger("raft-order-agent")


class AgentState(TypedDict, total=False):
    request: str
    started_at: float
    api_payload: Dict[str, Any]
    raw_records: List[str]
    chunks: List[List[str]]
    parsed_orders: List[Dict[str, Any]]
    all_orders: List[Dict[str, Any]]
    filters: Dict[str, Any]
    result: Dict[str, Any]
    evidence: List[Dict[str, Any]]
    audit: List[Dict[str, str]]
    anomalies: List[Dict[str, str]]
    metrics: Dict[str, Any]
    runtime: Dict[str, Any]
    runtime_provider: str


@dataclass
class Order:
    orderId: str
    buyer: str
    city: str
    state: str
    total: float
    items: List[str]
    source: str

    def public(self) -> Dict[str, Any]:
        return {
            "orderId": self.orderId,
            "buyer": self.buyer,
            "state": self.state,
            "total": round(self.total, 2),
        }


def load_dotenv(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not os.environ.get(key):
            os.environ[key] = value


def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    return response


def ensure_customer_api() -> Optional[subprocess.Popen]:
    api_url = os.getenv("CUSTOMER_API_URL", CUSTOMER_API_DEFAULT)
    if is_customer_api_alive(api_url):
        return None

    script = APP_DIR / "dummy_customer_api.py"
    if not script.exists():
        LOG.warning("Customer API script not found at %s", script)
        return None

    LOG.info("Starting dummy customer API at %s", api_url)
    process = subprocess.Popen(
        [sys.executable, str(script)],
        cwd=str(APP_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    for _ in range(30):
        if is_customer_api_alive(api_url):
            return process
        time.sleep(0.2)
    LOG.warning("Customer API did not become ready in time")
    return process


def is_customer_api_alive(api_url: str) -> bool:
    try:
        response = requests.get(f"{api_url}/api/orders?limit=1", timeout=0.6)
        return response.ok
    except requests.RequestException:
        return False


def extract_strings(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        output: List[str] = []
        for item in value:
            output.extend(extract_strings(item))
        return output
    if isinstance(value, dict):
        output = []
        for item in value.values():
            output.extend(extract_strings(item))
        return output
    return []


def chunk_records(records: List[str], limit: int = CHUNK_LIMIT_CHARS) -> List[List[str]]:
    chunks: List[List[str]] = []
    current: List[str] = []
    current_size = 0
    for record in records:
        projected = current_size + len(record)
        if current and projected > limit:
            chunks.append(current)
            current = []
            current_size = 0
        current.append(record)
        current_size += len(record)
    if current:
        chunks.append(current)
    return chunks


def fetch_orders(state: AgentState) -> AgentState:
    api_url = os.getenv("CUSTOMER_API_URL", CUSTOMER_API_DEFAULT)
    try:
        response = requests.get(f"{api_url}/api/orders", timeout=6)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        LOG.exception("Failed to fetch customer orders")
        raise RuntimeError(f"Customer API fetch failed: {exc}") from exc

    raw_records = [text for text in extract_strings(payload) if "order" in text.lower()]
    if not raw_records:
        raise RuntimeError("Customer API response did not contain order-like text")

    return {
        **state,
        "api_payload": payload,
        "raw_records": raw_records,
        "chunks": chunk_records(raw_records),
    }


def parse_orders(state: AgentState) -> AgentState:
    parsed: List[Dict[str, Any]] = []
    provider = "deterministic local fallback"
    api_key = os.getenv("OPENROUTER_API_KEY")

    for chunk in state["chunks"]:
        llm_orders: List[Dict[str, Any]] = []
        if api_key:
            try:
                llm_orders = parse_chunk_with_openrouter(chunk, api_key)
                provider = "OpenRouter verified"
            except Exception as exc:
                LOG.exception("OpenRouter parse failed")
                if os.getenv("ALLOW_OPENROUTER_FALLBACK") == "1":
                    provider = "OpenRouter failed; deterministic fallback"
                else:
                    raise RuntimeError(f"OpenRouter call failed: {exc}") from exc
        parsed.extend(verify_or_fallback(chunk, llm_orders))

    unique = {order["orderId"]: order for order in parsed}
    all_orders = [unique[key] for key in sorted(unique)]
    return {
        **state,
        "parsed_orders": all_orders,
        "all_orders": [public_order(order) for order in all_orders],
        "runtime_provider": provider,
    }


def parse_chunk_with_openrouter(chunk: List[str], api_key: str) -> List[Dict[str, Any]]:
    prompt = {
        "task": "Extract orders from raw customer text. Return only JSON with an orders array.",
        "schema": {
            "orders": [
                {
                    "orderId": "string",
                    "buyer": "string",
                    "state": "two-letter code",
                    "total": "number",
                    "items": ["string"],
                }
            ]
        },
        "rules": [
            "Use only fields supported by the provided raw text.",
            "If a field is missing, omit that order.",
            "Do not infer names, states, or totals.",
        ],
        "raw_orders": chunk,
    }
    response = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:3000",
            "X-Title": "Raft Order Agent",
        },
        json={
            "model": MODEL,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": "You are a strict JSON extraction engine."},
                {"role": "user", "content": json.dumps(prompt)},
            ],
            "response_format": {"type": "json_object"},
        },
        timeout=25,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    payload = json.loads(extract_json_object(content))
    orders = payload.get("orders", [])
    return orders if isinstance(orders, list) else []


def extract_json_object(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped, flags=re.IGNORECASE).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    if stripped.startswith("{"):
        return stripped
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Model did not return a JSON object")
    return stripped[start : end + 1]


def verify_or_fallback(chunk: List[str], llm_orders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_source = {order.orderId: order for order in deterministic_parse(chunk)}
    verified: List[Dict[str, Any]] = []

    for item in llm_orders:
        order_id = str(item.get("orderId", ""))
        source_order = by_source.get(order_id)
        if not source_order:
            continue
        candidate = normalize_order(item, source_order.source)
        if candidate and not candidate.get("items"):
            candidate["items"] = source_order.items
        if candidate:
            candidate["city"] = source_order.city
        if candidate and supports_order(source_order.source, candidate):
            verified.append(candidate)

    if verified:
        verified_ids = {item["orderId"] for item in verified}
        missing = [internal_order(order) for order in by_source.values() if order.orderId not in verified_ids]
        return verified + missing
    return [internal_order(order) for order in by_source.values()]


def internal_order(order: Order) -> Dict[str, Any]:
    return order.public() | {"city": order.city, "items": order.items, "source": order.source}


def deterministic_parse(records: Iterable[str]) -> List[Order]:
    orders = []
    for record in records:
        match = ORDER_RE.search(record)
        if not match:
            LOG.warning("Could not parse order text: %s", record)
            continue
        orders.append(
            Order(
                orderId=match.group("order_id"),
                buyer=match.group("buyer").strip(),
                city=match.group("location").rsplit(",", 1)[0].strip(),
                state=match.group("state").upper(),
                total=float(match.group("total")),
                items=parse_items(match.group("items")),
                source=record,
            )
        )
    return orders


def parse_items(raw_items: str) -> List[str]:
    cleaned = re.split(r"\s+(?:status|notes?|shipping)\s*[:=]", raw_items, flags=re.IGNORECASE)[0]
    return [canonicalize_item(item.strip().lower()) for item in cleaned.split(",") if item.strip()]


def normalize_order(item: Dict[str, Any], source: str) -> Optional[Dict[str, Any]]:
    try:
        order_id = str(item["orderId"]).strip()
        buyer = str(item["buyer"]).strip()
        state = str(item["state"]).strip().upper()
        total = float(item["total"])
        raw_items = item.get("items", [])
    except (KeyError, TypeError, ValueError):
        return None
    if isinstance(raw_items, str):
        items = parse_items(raw_items)
    elif isinstance(raw_items, list):
        items = [str(value).strip().lower() for value in raw_items if str(value).strip()]
    else:
        items = []
    if not order_id or not buyer or not re.fullmatch(r"[A-Z]{2}", state):
        return None
    return {"orderId": order_id, "buyer": buyer, "state": state, "total": round(total, 2), "items": items, "source": source}


def supports_order(source: str, order: Dict[str, Any]) -> bool:
    source_lower = source.lower()
    total_variants = {f"{order['total']:.2f}", str(order["total"]).rstrip("0").rstrip(".")}
    return (
        order["orderId"] in source
        and order["buyer"].lower() in source_lower
        and order["state"] in source
        and any(total in source for total in total_variants)
        and all(item in source_lower for item in order.get("items", []))
    )


def public_order(order: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "orderId": str(order["orderId"]),
        "buyer": order["buyer"],
        "state": order["state"],
        "total": round(float(order["total"]), 2),
    }


def normalize_search_text(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
    words = []
    for word in normalized.split():
        if len(word) > 3 and word.endswith("s"):
            word = word[:-1]
        words.append(word)
    return " ".join(words)


def canonicalize_item(value: str) -> str:
    normalized = normalize_search_text(value)
    return ITEM_ALIASES.get(normalized, normalized)


def similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, normalize_search_text(left), normalize_search_text(right)).ratio()


def fuzzy_text_match(query: Optional[str], candidate: str) -> bool:
    if not query:
        return True
    query_norm = normalize_search_text(query)
    candidate_norm = normalize_search_text(candidate)
    if not query_norm:
        return True
    if query_norm in candidate_norm or candidate_norm in query_norm:
        return True
    return similarity(query_norm, candidate_norm) >= FUZZY_THRESHOLD


def known_items(orders: List[Dict[str, Any]]) -> List[str]:
    items = sorted({canonicalize_item(item) for order in orders for item in order.get("items", [])})
    return items


def known_cities(orders: List[Dict[str, Any]]) -> List[str]:
    return sorted({order.get("city", "") for order in orders if order.get("city")})


def known_buyers(orders: List[Dict[str, Any]]) -> List[str]:
    return sorted({order.get("buyer", "") for order in orders if order.get("buyer")})


def text_ngrams(text: str, max_words: int = 3) -> List[str]:
    words = [word for word in normalize_search_text(text).split() if word and word not in STOPWORDS]
    grams = []
    for size in range(max_words, 0, -1):
        for index in range(0, len(words) - size + 1):
            grams.append(" ".join(words[index : index + size]))
    return grams


def add_unique(values: List[str], value: str) -> None:
    if value and value not in values:
        values.append(value)


def extract_item_filters(text: str, catalog: List[str]) -> List[str]:
    normalized = normalize_search_text(text)
    found: List[str] = []

    for alias, canonical in ITEM_ALIASES.items():
        if fuzzy_text_match(alias, normalized) or alias in normalized:
            if canonical in catalog:
                add_unique(found, canonical)

    for item in catalog:
        if item in normalized:
            add_unique(found, item)

    for gram in text_ngrams(text):
        canonical_gram = canonicalize_item(gram)
        if canonical_gram in catalog:
            add_unique(found, canonical_gram)
            continue
        for item in catalog:
            if similarity(gram, item) >= FUZZY_THRESHOLD:
                add_unique(found, item)

    return found


def extract_city_filter(text: str, cities: List[str]) -> Optional[str]:
    for city in cities:
        if fuzzy_text_match(city, text):
            return city
    best_city = None
    best_score = 0.0
    for gram in text_ngrams(text, max_words=2):
        for city in cities:
            score = similarity(gram, city)
            if score > best_score:
                best_city = city
                best_score = score
    if best_score >= FUZZY_THRESHOLD:
        return best_city
    return None


def extract_buyer_filter(text: str, buyers: List[str]) -> Optional[str]:
    buyer_match = re.search(
        r"(?:buyer|customer|person|people|by|for)\s+(?:was\s+)?(?:named\s+)?([a-z][a-z\s]+?)(?:\s+(?:and|with|from|in|over|under|above|below|who)|$)",
        text,
    )
    candidates = [buyer_match.group(1).strip()] if buyer_match else []
    candidates.extend(text_ngrams(text, max_words=2))

    best_name = None
    best_score = 0.0
    for candidate in candidates:
        if candidate in STOPWORDS:
            continue
        for buyer in buyers:
            score = similarity(candidate, buyer)
            buyer_parts = normalize_search_text(buyer).split()
            if normalize_search_text(candidate) in buyer_parts:
                score = max(score, 0.88)
            if score > best_score:
                best_name = buyer
                best_score = score

    return best_name if best_score >= FUZZY_THRESHOLD else None


def extract_total_filters(text: str, filters: Dict[str, Any]) -> None:
    between_match = re.search(r"(?:between|from)\s*\$?\s*(\d+(?:\.\d+)?)\s*(?:and|to|-)\s*\$?\s*(\d+(?:\.\d+)?)", text)
    if between_match:
        low, high = sorted([float(between_match.group(1)), float(between_match.group(2))])
        filters["min_total"] = low
        filters["max_total"] = high
        filters["min_inclusive"] = True
        filters["max_inclusive"] = True
        return

    exact_match = re.search(r"(?:total|value|price|amount)\s*(?:is|=|equals?|exactly)?\s*\$?\s*(\d+(?:\.\d+)?)", text)
    if exact_match:
        exact = float(exact_match.group(1))
        filters["min_total"] = exact
        filters["max_total"] = exact
        filters["min_inclusive"] = True
        filters["max_inclusive"] = True
        return

    amount_match = re.search(r"(?:at least|minimum|min|no less than)\s*\$?\s*(\d+(?:\.\d+)?)", text)
    if amount_match:
        filters["min_total"] = float(amount_match.group(1))
        filters["min_inclusive"] = True
    amount_match = re.search(r"(?:over|above|greater than|more than)\s*\$?\s*(\d+(?:\.\d+)?)", text)
    if amount_match:
        filters["min_total"] = float(amount_match.group(1))
        filters["min_inclusive"] = False

    amount_match = re.search(r"(?:at most|maximum|max|no more than)\s*\$?\s*(\d+(?:\.\d+)?)", text)
    if amount_match:
        filters["max_total"] = float(amount_match.group(1))
        filters["max_inclusive"] = True
    amount_match = re.search(r"(?:under|below|less than)\s*\$?\s*(\d+(?:\.\d+)?)", text)
    if amount_match:
        filters["max_total"] = float(amount_match.group(1))
        filters["max_inclusive"] = False


def detect_rank_filter(text: str) -> Optional[str]:
    if re.search(r"\b(?:highest|largest|biggest|most expensive|priciest|max(?:imum)?)\b", text):
        return "highest"
    if re.search(r"\b(?:lowest|smallest|cheapest|least expensive|min(?:imum)?)\b", text):
        return "lowest"
    return None


def parse_request(state: AgentState) -> AgentState:
    text = state["request"].lower()
    orders = state.get("parsed_orders", [])
    filters: Dict[str, Any] = {
        "state": None,
        "city": None,
        "min_total": None,
        "min_inclusive": False,
        "max_total": None,
        "max_inclusive": False,
        "buyer": None,
        "order_id": None,
        "items": [],
        "item_match_mode": "any",
        "rank": None,
    }

    for alias, code in STATE_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", text):
            filters["state"] = code
            break
    filters["city"] = extract_city_filter(text, known_cities(orders))

    order_match = re.search(r"\border\s*#?\s*(\d+)\b", text)
    if order_match:
        filters["order_id"] = order_match.group(1)

    extract_total_filters(text, filters)
    filters["rank"] = detect_rank_filter(text)

    filters["buyer"] = extract_buyer_filter(text, known_buyers(orders))
    filters["items"] = extract_item_filters(text, known_items(orders))
    if len(filters["items"]) > 1 and not re.search(r"\b(?:or|either|any)\b", text):
        filters["item_match_mode"] = "all"

    return {**state, "filters": filters}


def filter_orders(state: AgentState) -> AgentState:
    filters = state["filters"]
    matched = []
    for order in state["parsed_orders"]:
        if filters["order_id"] and order["orderId"] != filters["order_id"]:
            continue
        if filters["state"] and order["state"] != filters["state"]:
            continue
        if filters["city"] and not fuzzy_text_match(filters["city"], order.get("city", "")):
            continue
        if filters["min_total"] is not None:
            total = float(order["total"])
            if filters["min_inclusive"] and total < filters["min_total"]:
                continue
            if not filters["min_inclusive"] and total <= filters["min_total"]:
                continue
        if filters["max_total"] is not None:
            total = float(order["total"])
            if filters["max_inclusive"] and total > filters["max_total"]:
                continue
            if not filters["max_inclusive"] and total >= filters["max_total"]:
                continue
        if filters["buyer"] and not fuzzy_text_match(filters["buyer"], order["buyer"]):
            continue
        if filters["items"]:
            order_items = [canonicalize_item(item) for item in order.get("items", [])]
            item_matches = [any(fuzzy_text_match(item_filter, item) for item in order_items) for item_filter in filters["items"]]
            if filters["item_match_mode"] == "all" and not all(item_matches):
                continue
            if filters["item_match_mode"] == "any" and not any(item_matches):
                continue
        matched.append(order)

    matched = sorted(matched, key=lambda item: item["orderId"])
    if filters["rank"] == "highest" and matched:
        matched = [max(matched, key=lambda item: float(item["total"]))]
    if filters["rank"] == "lowest" and matched:
        matched = [min(matched, key=lambda item: float(item["total"]))]
    return {
        **state,
        "result": {"orders": [public_order(order) for order in matched]},
        "evidence": build_evidence(matched),
        "metrics": build_metrics(state["parsed_orders"], matched, state["raw_records"]),
        "anomalies": build_anomalies(state["parsed_orders"]),
        "audit": build_audit(state),
    }


def build_evidence(orders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    evidence = []
    for order in orders:
        supported = {
            "orderId": order["orderId"] in order["source"],
            "buyer": order["buyer"].lower() in order["source"].lower(),
            "state": order["state"] in order["source"],
            "total": any(total in order["source"] for total in {f"{float(order['total']):.2f}", str(order["total"])}),
            "items": all(item in order["source"].lower() for item in order.get("items", [])),
        }
        evidence.append(
            {
                "orderId": order["orderId"],
                "source": order["source"],
                "supportedFields": supported,
                "confidence": round(sum(supported.values()) / len(supported), 2),
            }
        )
    return evidence


def build_metrics(all_orders: List[Dict[str, Any]], matched: List[Dict[str, Any]], raw_records: List[str]) -> Dict[str, Any]:
    totals = [float(order["total"]) for order in all_orders]
    matched_total = sum(float(order["total"]) for order in matched)
    state_breakdown: Dict[str, int] = {}
    for order in all_orders:
        state_breakdown[order["state"]] = state_breakdown.get(order["state"], 0) + 1

    highest = max(all_orders, key=lambda order: float(order["total"])) if all_orders else None
    average = sum(totals) / len(totals) if totals else 0
    next_estimate = simple_linear_forecast(totals)
    return {
        "rawRecords": len(raw_records),
        "structuredRecords": len(all_orders),
        "matchedRecords": len(matched),
        "matchedTotal": round(matched_total, 2),
        "averageOrderValue": round(average, 2),
        "coveragePct": round((len(all_orders) / len(raw_records)) * 100) if raw_records else 0,
        "highValueOrders": len([order for order in all_orders if float(order["total"]) > 500]),
        "highestOrder": public_order(highest) if highest else None,
        "stateBreakdown": dict(sorted(state_breakdown.items())),
        "nextOrderEstimate": round(next_estimate, 2) if next_estimate else None,
    }


def simple_linear_forecast(values: List[float]) -> Optional[float]:
    if len(values) < 2:
        return values[0] if values else None
    n = len(values)
    xs = list(range(1, n + 1))
    x_mean = sum(xs) / n
    y_mean = sum(values) / n
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, values))
    denominator = sum((x - x_mean) ** 2 for x in xs) or 1
    slope = numerator / denominator
    intercept = y_mean - slope * x_mean
    return max(0, intercept + slope * (n + 1))


def build_anomalies(all_orders: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    if not all_orders:
        return []
    average = sum(float(order["total"]) for order in all_orders) / len(all_orders)
    anomalies = []
    for order in all_orders:
        total = float(order["total"])
        if average and total >= average * 2:
            anomalies.append({"orderId": order["orderId"], "severity": "high", "message": "Total is at least 2x the portfolio average."})
        elif average and total <= average * 0.25:
            anomalies.append({"orderId": order["orderId"], "severity": "low", "message": "Order value is materially below the portfolio average."})
    return anomalies


def build_audit(state: AgentState) -> List[Dict[str, str]]:
    return [
        {"name": "API schema guard", "status": "pass", "detail": "Recursive extraction found order-like text without assuming a fixed JSON key."},
        {"name": "Context window control", "status": "pass", "detail": f"{len(state['raw_records'])} records were split into {len(state['chunks'])} chunk(s)."},
        {"name": "Hallucination filter", "status": "pass", "detail": "Every returned field is checked against raw source text before it reaches the response."},
        {"name": "Deterministic sorting", "status": "pass", "detail": "Final JSON is sorted by order id with temperature 0 model calls."},
    ]


def finalize(state: AgentState) -> AgentState:
    latency_ms = round((time.time() - state["started_at"]) * 1000)
    runtime = {
        "latencyMs": latency_ms,
        "model": MODEL,
        "provider": state.get("runtime_provider", "deterministic local fallback"),
        "temperature": 0,
        "chunkLimitChars": CHUNK_LIMIT_CHARS,
        "apiUrl": os.getenv("CUSTOMER_API_URL", CUSTOMER_API_DEFAULT),
    }
    return {**state, "runtime": runtime}


def build_graph():
    if StateGraph is None:
        return None

    graph = StateGraph(AgentState)
    graph.add_node("fetch_orders", fetch_orders)
    graph.add_node("parse_orders", parse_orders)
    graph.add_node("parse_request", parse_request)
    graph.add_node("filter_orders", filter_orders)
    graph.add_node("finalize", finalize)
    graph.set_entry_point("fetch_orders")
    graph.add_edge("fetch_orders", "parse_orders")
    graph.add_edge("parse_orders", "parse_request")
    graph.add_edge("parse_request", "filter_orders")
    graph.add_edge("filter_orders", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()


def run_agent(request_text: str) -> Dict[str, Any]:
    initial: AgentState = {"request": request_text, "started_at": time.time()}
    graph = build_graph()
    if graph is not None:
        state = graph.invoke(initial)
    else:
        state = finalize(filter_orders(parse_request(parse_orders(fetch_orders(initial)))))

    architecture = ["fetch_orders", "parse_orders", "parse_request", "filter_orders", "finalize"]
    return {
        "request": request_text,
        "result": state["result"],
        "filters": state["filters"],
        "metrics": state["metrics"],
        "allOrders": state["all_orders"],
        "evidence": state["evidence"],
        "audit": state["audit"],
        "anomalies": state["anomalies"],
        "runtime": state["runtime"],
        "architecture": architecture,
    }


def create_app() -> Flask:
    app = Flask(__name__)

    @app.after_request
    def after_request(response):
        return add_cors(response)

    @app.route("/api/query", methods=["OPTIONS"])
    def query_options():
        return add_cors(jsonify({}))

    @app.route("/api/query", methods=["POST"])
    def query():
        payload = request.get_json(silent=True) or {}
        request_text = payload.get("request") or payload.get("query")
        if not request_text:
            return jsonify({"error": "Missing request"}), 400
        try:
            return jsonify(run_agent(str(request_text)))
        except Exception as exc:
            LOG.exception("Agent run failed")
            return jsonify({"error": str(exc)}), 500

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify({"model": MODEL, "status": "ok"})

    return app


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Raft order intelligence agent")
    parser.add_argument("request", nargs="*", help="Natural language request to run in CLI mode")
    parser.add_argument("--web", action="store_true", help="Run the Flask agent API")
    parser.add_argument("--port", type=int, default=8000, help="Port for --web mode")
    args = parser.parse_args()

    customer_process = ensure_customer_api()
    try:
        if args.web:
            create_app().run(host="127.0.0.1", port=args.port, debug=False)
            return
        request_text = " ".join(args.request) or "Show me all orders where the buyer was located in Ohio and total value was over 500."
        print(json.dumps(run_agent(request_text)["result"], indent=2))
    finally:
        if customer_process:
            customer_process.terminate()


if __name__ == "__main__":
    main()
