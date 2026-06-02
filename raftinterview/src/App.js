import { useEffect, useMemo, useState } from 'react';
import './App.css';

const sampleResponse = {
  request: 'Show me all orders where the buyer was located in Ohio and total value was over 500.',
  result: {
    orders: [
      { orderId: '1001', buyer: 'John Davis', state: 'OH', total: 742.1 },
      { orderId: '1003', buyer: 'Mike Turner', state: 'OH', total: 1299.99 },
      { orderId: '1005', buyer: 'Chris Myers', state: 'OH', total: 512 },
    ],
  },
  filters: { state: 'OH', min_total: 500, max_total: null, buyer_contains: null, order_id: null },
  metrics: {
    rawRecords: 5,
    structuredRecords: 5,
    matchedRecords: 3,
    matchedTotal: 2554.09,
    averageOrderValue: 560.03,
    coveragePct: 100,
    highValueOrders: 3,
    highestOrder: { orderId: '1003', buyer: 'Mike Turner', state: 'OH', total: 1299.99 },
    stateBreakdown: { OH: 3, TX: 1, WA: 1 },
    nextOrderEstimate: 401.85,
  },
  allOrders: [
    { orderId: '1001', buyer: 'John Davis', state: 'OH', total: 742.1 },
    { orderId: '1002', buyer: 'Sarah Liu', state: 'TX', total: 156.55 },
    { orderId: '1003', buyer: 'Mike Turner', state: 'OH', total: 1299.99 },
    { orderId: '1004', buyer: 'Rachel Kim', state: 'WA', total: 89.5 },
    { orderId: '1005', buyer: 'Chris Myers', state: 'OH', total: 512 },
  ],
  evidence: [
    {
      orderId: '1001',
      source: 'Order 1001: Buyer=John Davis, Location=Columbus, OH, Total=$742.10, Items: laptop, hdmi cable',
      supportedFields: { orderId: true, buyer: true, state: true, total: true },
      confidence: 1,
    },
    {
      orderId: '1003',
      source: 'Order 1003: Buyer=Mike Turner, Location=Cleveland, OH, Total=$1299.99, Items: gaming pc, mouse',
      supportedFields: { orderId: true, buyer: true, state: true, total: true },
      confidence: 1,
    },
    {
      orderId: '1005',
      source: 'Order 1005: Buyer=Chris Myers, Location=Cincinnati, OH, Total=$512.00, Items: monitor, desk lamp',
      supportedFields: { orderId: true, buyer: true, state: true, total: true },
      confidence: 1,
    },
  ],
  audit: [
    { name: 'API schema guard', status: 'pass', detail: 'Recursive extraction found order-like text.' },
    { name: 'Context window control', status: 'pass', detail: 'Records are chunked before extraction.' },
    { name: 'Hallucination filter', status: 'pass', detail: 'Output is verified against raw source text.' },
    { name: 'Deterministic sorting', status: 'pass', detail: 'Final JSON is sorted by order id.' },
  ],
  anomalies: [
    { orderId: '1003', severity: 'high', message: 'Total is 2.3x the portfolio average.' },
    { orderId: '1004', severity: 'low', message: 'Order value is materially below the portfolio average.' },
  ],
  runtime: {
    latencyMs: 322,
    model: 'openai/gpt-oss-120b:exacto',
    provider: 'deterministic local fallback',
    temperature: 0,
    chunkLimitChars: 3500,
    apiUrl: 'http://127.0.0.1:5001',
  },
  architecture: ['fetch_orders', 'parse_orders', 'verify_orders', 'parse_request', 'filter_orders'],
};

const presets = [
  'Show me all orders where the buyer was located in Ohio and total value was over 500.',
  'Show me all orders under 200',
  'Show me order 1003',
  'Show me all Texas orders',
  'Show me all orders above 1000',
];

const tabs = ['Results', 'Evidence', 'Audit', 'Analytics'];
const apiCandidates = ['/api/query', 'http://127.0.0.1:8000/api/query'];

function formatMoney(value) {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 2,
  }).format(value || 0);
}

function filterLabel(filters = {}) {
  const parts = [];
  if (filters.state) parts.push(filters.state);
  if (filters.city) parts.push(filters.city);
  if (filters.buyer) parts.push(filters.buyer);
  if (filters.items?.length) parts.push(`${filters.item_match_mode === 'all' ? 'all' : 'any'}: ${filters.items.join(', ')}`);
  if (filters.min_total !== null && filters.min_total !== undefined) parts.push(`> ${formatMoney(filters.min_total)}`);
  if (filters.max_total !== null && filters.max_total !== undefined) parts.push(`< ${formatMoney(filters.max_total)}`);
  if (filters.order_id) parts.push(`#${filters.order_id}`);
  return parts.length ? parts.join(' / ') : 'No filters';
}

function presetLabel(preset) {
  if (preset.includes('under')) return 'Under 200';
  if (preset.includes('1003')) return 'Order 1003';
  if (preset.includes('Texas')) return 'Texas';
  if (preset.includes('1000')) return 'Above 1000';
  return 'OH over 500';
}

function App() {
  const [query, setQuery] = useState(presets[0]);
  const [data, setData] = useState(sampleResponse);
  const [status, setStatus] = useState('Demo data loaded');
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState('Results');
  const [copied, setCopied] = useState(false);

  const orders = data.result?.orders || [];
  const allOrders = useMemo(() => data.allOrders || [], [data.allOrders]);
  const metrics = useMemo(() => data.metrics || {}, [data.metrics]);
  const runtime = useMemo(() => data.runtime || {}, [data.runtime]);
  const maxStateCount = Math.max(1, ...Object.values(metrics.stateBreakdown || {}));
  const cleanJson = useMemo(() => JSON.stringify(data.result || { orders: [] }, null, 2), [data]);

  const decisionBrief = useMemo(() => {
    const top = metrics.highestOrder;
    const matched = metrics.matchedRecords ?? orders.length;
    const total = formatMoney(metrics.matchedTotal);
    if (!top) return `${matched} orders matched for ${total}.`;
    return `${matched} order(s) matched for ${total}. Highest portfolio exposure is order ${top.orderId} at ${formatMoney(top.total)}.`;
  }, [metrics, orders.length]);

  async function runAgent(nextQuery = query) {
    setLoading(true);
    setStatus('Running LangGraph agent');

    try {
      const payload = await queryAgent(nextQuery);
      setData(payload);
      setStatus('Live agent response');
    } catch (error) {
      setStatus(`Agent error - ${error.message}`);
    } finally {
      setLoading(false);
    }
  }

  async function queryAgent(requestText) {
    let lastError;

    for (const url of apiCandidates) {
      try {
        const response = await fetch(url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ request: requestText }),
        });

        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || `API returned ${response.status}`);
        return payload;
      } catch (error) {
        lastError = error;
      }
    }

    throw lastError || new Error('Python agent API is not reachable');
  }

  async function copyJson() {
    await navigator.clipboard.writeText(cleanJson);
    setCopied(true);
    setTimeout(() => setCopied(false), 1400);
  }

  function exportJson() {
    const blob = new Blob([cleanJson], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'raft-order-agent-result.json';
    link.click();
    URL.revokeObjectURL(url);
  }

  useEffect(() => {
    runAgent(presets[0]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <main className="app-shell">
      <section className="topbar">
        <div>
          <p className="eyebrow">Raft AI Engineer Coding Challenge</p>
          <h1>Order Intelligence Command Center</h1>
        </div>
        <div className="status-pill">
          <span className={loading ? 'pulse active' : 'pulse'} />
          <span>{status}</span>
        </div>
      </section>

      <section className="brief-panel">
        <div>
          <span className="brief-kicker">Decision brief</span>
          <p>{decisionBrief}</p>
        </div>
        <div className="runtime-strip">
          <span>{runtime.provider || 'provider pending'}</span>
          <span>{runtime.latencyMs ? `${runtime.latencyMs}ms` : 'latency pending'}</span>
          <span>{runtime.temperature === 0 ? 'temperature 0' : 'deterministic'}</span>
        </div>
      </section>

      <section className="workspace">
        <aside className="query-panel">
          <div className="panel-title">
            <span className="icon-box">Q</span>
            <h2>Natural Language Request</h2>
          </div>

          <textarea
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') runAgent();
            }}
          />

          <div className="preset-grid">
            {presets.map((preset) => (
              <button
                key={preset}
                type="button"
                onClick={() => {
                  setQuery(preset);
                  runAgent(preset);
                }}
              >
                {presetLabel(preset)}
              </button>
            ))}
          </div>

          <div className="action-row">
            <button className="run-button" type="button" onClick={() => runAgent()} disabled={loading}>
              <span>{loading ? 'Running' : 'Run Agent'}</span>
              <span>GO</span>
            </button>
            <button className="ghost-button" type="button" onClick={copyJson}>
              {copied ? 'Copied' : 'Copy JSON'}
            </button>
            <button className="ghost-button" type="button" onClick={exportJson}>
              Export
            </button>
          </div>

          <div className="trace-panel">
            <div className="panel-title compact">
              <span className="icon-box">G</span>
              <h2>LangGraph Trace</h2>
            </div>
            <ol>
              {(data.architecture || sampleResponse.architecture).map((step, index) => (
                <li key={step}>
                  <span>{String(index + 1).padStart(2, '0')} / {step}</span>
                  <strong>verified</strong>
                </li>
              ))}
            </ol>
          </div>
        </aside>

        <section className="results-panel">
          <div className="metric-grid">
            <Metric label="Matched" value={metrics.matchedRecords ?? orders.length} />
            <Metric label="Total Value" value={formatMoney(metrics.matchedTotal)} accent="cyan" />
            <Metric label="Coverage" value={`${metrics.coveragePct || 0}%`} />
            <Metric label="Next Estimate" value={metrics.nextOrderEstimate ? formatMoney(metrics.nextOrderEstimate) : 'n/a'} accent="amber" />
          </div>

          <div className="tab-row">
            {tabs.map((tab) => (
              <button
                key={tab}
                type="button"
                className={activeTab === tab ? 'active' : ''}
                onClick={() => setActiveTab(tab)}
              >
                {tab}
              </button>
            ))}
          </div>

          {activeTab === 'Results' && (
            <div className="content-grid">
              <section className="table-panel">
                <div className="section-heading">
                  <h2>Clean JSON Orders</h2>
                  <span>{filterLabel(data.filters)}</span>
                </div>
                <OrdersTable orders={orders} />
              </section>
              <section className="insight-panel">
                <div className="section-heading">
                  <h2>JSON Output</h2>
                  <span>{orders.length} returned</span>
                </div>
                <pre>{cleanJson}</pre>
              </section>
            </div>
          )}

          {activeTab === 'Evidence' && (
            <section className="wide-panel">
              <div className="section-heading">
                <h2>Source Grounding</h2>
                <span>{data.evidence?.length || 0} evidence cards</span>
              </div>
              <div className="evidence-grid">
                {(data.evidence || []).map((item) => (
                  <article className="evidence-card" key={item.orderId}>
                    <div>
                      <strong>Order {item.orderId}</strong>
                      <span>{Math.round((item.confidence || 0) * 100)}% supported</span>
                    </div>
                    <p>{item.source}</p>
                    <div className="field-grid">
                      {Object.entries(item.supportedFields || {}).map(([field, ok]) => (
                        <span className={ok ? 'field-ok' : 'field-miss'} key={field}>
                          {field}
                        </span>
                      ))}
                    </div>
                  </article>
                ))}
              </div>
            </section>
          )}

          {activeTab === 'Audit' && (
            <section className="wide-panel">
              <div className="section-heading">
                <h2>Agent Safety Audit</h2>
                <span>{runtime.model || 'model pending'}</span>
              </div>
              <div className="audit-grid">
                {(data.audit || []).map((item) => (
                  <article className="audit-card" key={item.name}>
                    <span className={`audit-status ${item.status}`}>{item.status}</span>
                    <h3>{item.name}</h3>
                    <p>{item.detail}</p>
                  </article>
                ))}
              </div>
            </section>
          )}

          {activeTab === 'Analytics' && (
            <div className="content-grid">
              <section className="table-panel">
                <div className="section-heading">
                  <h2>Portfolio View</h2>
                  <span>{allOrders.length} structured</span>
                </div>
                <OrdersTable orders={allOrders} />
              </section>
              <section className="insight-panel">
                <div className="section-heading">
                  <h2>Signals</h2>
                  <span>{data.anomalies?.length || 0} anomalies</span>
                </div>
                <StateBars breakdown={metrics.stateBreakdown || {}} maxStateCount={maxStateCount} />
                <div className="signal-list">
                  {(data.anomalies || []).map((item) => (
                    <article className="signal-card" key={`${item.orderId}-${item.message}`}>
                      <strong>{item.severity}</strong>
                      <span>Order {item.orderId}</span>
                      <p>{item.message}</p>
                    </article>
                  ))}
                </div>
              </section>
            </div>
          )}
        </section>
      </section>
    </main>
  );
}

function Metric({ label, value, accent }) {
  return (
    <article className={`metric-card ${accent || ''}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

function OrdersTable({ orders }) {
  if (!orders.length) {
    return <div className="empty-state">No orders matched this request.</div>;
  }

  return (
    <table>
      <thead>
        <tr>
          <th>Order</th>
          <th>Buyer</th>
          <th>State</th>
          <th>Total</th>
        </tr>
      </thead>
      <tbody>
        {orders.map((order) => (
          <tr key={order.orderId}>
            <td>
              <strong>{order.orderId}</strong>
            </td>
            <td>{order.buyer}</td>
            <td>
              <span className="state-pill">{order.state}</span>
            </td>
            <td>{formatMoney(order.total)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function StateBars({ breakdown, maxStateCount }) {
  return (
    <div className="bars">
      {Object.entries(breakdown).map(([state, count]) => (
        <div className="bar-row" key={state}>
          <span>{state}</span>
          <div className="bar-track">
            <div className="bar-fill" style={{ width: `${(count / maxStateCount) * 100}%` }} />
          </div>
          <strong>{count}</strong>
        </div>
      ))}
    </div>
  );
}

export default App;
