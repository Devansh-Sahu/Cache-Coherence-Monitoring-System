import { useState, useCallback, useEffect } from 'react';
import { fetchExplain, fetchEvents } from '../api';
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine,
} from 'recharts';
import { X, Brain, Clock, Server, Tag, BookOpen, Loader } from 'lucide-react';

function formatMs(ms) {
  if (ms === null || ms === undefined) return '—';
  if (ms >= 60000) return `${(ms / 60000).toFixed(1)}m`;
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
  return `${ms}ms`;
}

function StatusBadge({ status }) {
  return <span className={`badge badge-${status}`}>{status}</span>;
}

const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null;
  return (
    <div style={{
      background: 'var(--bg-secondary)',
      border: '1px solid var(--border)',
      borderRadius: 'var(--radius-sm)',
      padding: '8px 12px',
      fontSize: '0.75rem',
    }}>
      <div style={{ color: 'var(--text-muted)', marginBottom: 4 }}>{label}</div>
      <div style={{ color: '#6366f1', fontFamily: 'JetBrains Mono' }}>
        Staleness: {formatMs(payload[0]?.value)}
      </div>
      {payload[1] && (
        <div style={{ color: 'var(--yellow)', fontFamily: 'JetBrains Mono' }}>
          SLA: {formatMs(payload[1]?.value)}
        </div>
      )}
    </div>
  );
};

export default function KeyDetailPanel({ keyData, onClose }) {
  const [explaining, setExplaining] = useState(false);
  const [explanation, setExplanation] = useState(null);
  const [history, setHistory] = useState(null);
  const [historyLoading, setHistoryLoading] = useState(false);

  const loadHistory = useCallback(async () => {
    if (history) return;
    setHistoryLoading(true);
    try {
      const { data } = await fetchEvents(keyData.key_name, 24);
      setHistory(data);
    } catch {
      setHistory({ events: [] });
    } finally {
      setHistoryLoading(false);
    }
  }, [keyData.key_name, history]);

  // Load history when panel opens
  useEffect(() => { loadHistory(); }, [loadHistory]);

  const handleExplain = async () => {
    setExplaining(true);
    setExplanation(null);
    try {
      const { data } = await fetchExplain(keyData.key_name);
      setExplanation(data);
    } catch (err) {
      setExplanation({ explanation: 'No breach events found to explain, or LLM is unavailable.', error: true });
    } finally {
      setExplaining(false);
    }
  };

  // Prepare chart data
  const chartData = (history?.events || [])
    .slice(0, 50)
    .reverse()
    .map((e) => ({
      time: new Date(e.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      staleness: e.staleness_ms,
      sla: keyData.sla_ms,
    }));

  const status = keyData.status || 'unknown';

  return (
    <div className="detail-panel" id="key-detail-panel">
      <div className="detail-header">
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
          <div>
            <div className="detail-key">{keyData.key_name}</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 8 }}>
              <StatusBadge status={status} />
              <span style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
                SLA: {formatMs(keyData.sla_ms)}
              </span>
            </div>
          </div>
          <button className="detail-close" onClick={onClose} id="detail-close-btn">
            <X size={18} />
          </button>
        </div>
      </div>

      <div className="detail-body">
        {/* Meta info */}
        <div className="detail-section">
          <div className="detail-section-title">Metadata</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            {[
              { icon: <Server size={13} />, label: 'Service', value: keyData.owning_service },
              { icon: <Clock size={13} />, label: 'Current Staleness', value: formatMs(keyData.current_staleness_ms) },
              { icon: <Clock size={13} />, label: 'Avg 24h', value: formatMs(keyData.avg_staleness_ms_24h) },
              { icon: <Clock size={13} />, label: 'Breaches 24h', value: keyData.breach_count_24h ?? 0 },
            ].map(({ icon, label, value }) => (
              <div key={label} style={{
                background: 'var(--bg-card)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius-sm)',
                padding: '10px 14px',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: 'var(--text-muted)', fontSize: '0.7rem', marginBottom: 4 }}>
                  {icon} {label}
                </div>
                <div style={{ fontFamily: 'JetBrains Mono', fontSize: '0.85rem', color: 'var(--text-primary)' }}>
                  {value ?? '—'}
                </div>
              </div>
            ))}
          </div>
          {keyData.tags?.length > 0 && (
            <div style={{ marginTop: 12, display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
              <Tag size={12} color="var(--text-muted)" />
              {keyData.tags.map(t => <span key={t} className="tag">{t}</span>)}
            </div>
          )}
        </div>

        {/* Sparkline chart */}
        <div className="detail-section">
          <div className="detail-section-title">Staleness Last 24h</div>
          {historyLoading ? (
            <div className="empty-state"><span className="spinner" /></div>
          ) : chartData.length > 0 ? (
            <ResponsiveContainer width="100%" height={140}>
              <LineChart data={chartData}>
                <XAxis dataKey="time" tick={{ fontSize: 9, fill: 'var(--text-muted)' }} tickLine={false} axisLine={false} interval="preserveStartEnd" />
                <YAxis tick={{ fontSize: 9, fill: 'var(--text-muted)' }} tickLine={false} axisLine={false} tickFormatter={(v) => formatMs(v)} width={45} />
                <Tooltip content={<CustomTooltip />} />
                <ReferenceLine y={keyData.sla_ms} stroke="var(--yellow)" strokeDasharray="4 2" strokeWidth={1.5} />
                <Line type="monotone" dataKey="staleness" stroke="var(--accent-light)" strokeWidth={2} dot={false} />
                <Line type="monotone" dataKey="sla" stroke="transparent" dot={false} />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <div className="empty-state">
              <div className="empty-state-icon">📊</div>
              <div className="empty-state-text">No history yet. Worker may not be running.</div>
            </div>
          )}
        </div>

        {/* LLM Explain */}
        <div className="detail-section">
          <div className="detail-section-title">AI Anomaly Explanation</div>
          <button
            id="explain-btn"
            className="explain-btn"
            onClick={handleExplain}
            disabled={explaining}
          >
            {explaining ? <span className="spinner" /> : <Brain size={15} />}
            {explaining ? 'Analyzing with AI…' : 'Explain This Breach'}
          </button>

          {explanation && (
            <div className="explain-result" id="explain-result">
              {explanation.error ? (
                <span style={{ color: 'var(--text-muted)' }}>{explanation.explanation}</span>
              ) : (
                <>
                  <div style={{ whiteSpace: 'pre-wrap' }}>{explanation.explanation}</div>
                  {explanation.relevant_runbook && (
                    <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--border)' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: 'var(--text-muted)', fontSize: '0.72rem', marginBottom: 6 }}>
                        <BookOpen size={12} /> Runbook: <span style={{ color: 'var(--accent-light)' }}>{explanation.relevant_runbook}</span>
                      </div>
                      {explanation.runbook_excerpt && (
                        <div style={{ fontSize: '0.78rem', color: 'var(--text-secondary)', fontStyle: 'italic' }}>
                          "{explanation.runbook_excerpt.substring(0, 200)}…"
                        </div>
                      )}
                    </div>
                  )}
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
