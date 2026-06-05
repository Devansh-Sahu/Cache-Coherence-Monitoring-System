import { useState, useEffect, useCallback } from 'react';
import { fetchDashboard } from './api';
import StalenessTable from './components/StalenessTable';
import KeyDetailPanel from './components/KeyDetailPanel';
import AskBar from './components/AskBar';
import AlertFeed from './components/AlertFeed';
import ServiceComparisonChart from './components/ServiceComparisonChart';
import { RefreshCw, Radio, AlertCircle } from 'lucide-react';

const POLL_INTERVAL_MS = 30_000;

function StatCard({ label, value, color, prefix = '' }) {
  return (
    <div className="stat-card" style={{ '--accent-gradient': color }}>
      <div className="stat-label">{label}</div>
      <div className="stat-value" style={{ color: color?.split(',')[1]?.trim() || 'var(--text-primary)' }}>
        {prefix}{value ?? '—'}
      </div>
    </div>
  );
}

export default function App() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [selectedKey, setSelectedKey] = useState(null);
  const [lastRefresh, setLastRefresh] = useState(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async (showRefreshing = false) => {
    if (showRefreshing) setRefreshing(true);
    try {
      const { data: dash } = await fetchDashboard();
      setData(dash);
      setError('');
      setLastRefresh(new Date());
    } catch (err) {
      setError('Cannot reach API server. Make sure FastAPI is running on port 8000.');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    load();
    const interval = setInterval(() => load(), POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [load]);

  const timeAgo = () => {
    if (!lastRefresh) return '';
    const diff = Math.floor((Date.now() - lastRefresh) / 1000);
    if (diff < 5) return 'just now';
    return `${diff}s ago`;
  };

  return (
    <div className="app">
      {/* Top Bar */}
      <header className="topbar">
        <div className="topbar-brand">
          <div className="topbar-logo">⚡</div>
          <div>
            <div className="topbar-title">Cache Staleness Monitor</div>
            <div className="topbar-subtitle">Redis · DynamoDB · Claude AI · PGVector</div>
          </div>
        </div>
        <div className="topbar-right">
          {lastRefresh && (
            <div className="status-indicator">
              <div className="status-dot" />
              Updated {timeAgo()}
            </div>
          )}
          <button
            id="refresh-btn"
            onClick={() => load(true)}
            disabled={refreshing}
            style={{
              background: 'var(--bg-card)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-sm)',
              padding: '7px 14px',
              color: 'var(--text-secondary)',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              fontSize: '0.8rem',
              transition: 'var(--transition)',
            }}
          >
            <RefreshCw size={13} style={{ animation: refreshing ? 'spin 0.7s linear infinite' : 'none' }} />
            Refresh
          </button>
        </div>
      </header>

      {/* Main */}
      <main className="main-content">
        {/* Error Banner */}
        {error && (
          <div style={{
            background: 'var(--red-bg)',
            border: '1px solid rgba(239,68,68,0.3)',
            borderRadius: 'var(--radius-md)',
            padding: '12px 16px',
            marginBottom: 20,
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            color: 'var(--red)',
            fontSize: '0.85rem',
          }}>
            <AlertCircle size={16} />
            {error}
          </div>
        )}

        {/* Stat Cards */}
        <div className="stats-grid">
          <StatCard
            label="Total Keys"
            value={loading ? '…' : data?.total_keys ?? 0}
            color="linear-gradient(90deg, #6366f1, #8b5cf6)"
          />
          <StatCard
            label="Healthy"
            value={loading ? '…' : data?.healthy_keys ?? 0}
            color="linear-gradient(90deg, #10b981, #34d399)"
          />
          <StatCard
            label="Warning"
            value={loading ? '…' : data?.warning_keys ?? 0}
            color="linear-gradient(90deg, #f59e0b, #fbbf24)"
          />
          <StatCard
            label="Critical"
            value={loading ? '…' : data?.critical_keys ?? 0}
            color="linear-gradient(90deg, #ef4444, #f87171)"
          />
        </div>

        {/* NL Ask Bar */}
        <AskBar />

        {/* Main Grid */}
        <div className="content-grid">
          {/* Left Column */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
            <ServiceComparisonChart keys={data?.keys ?? []} />
            <StalenessTable
              keys={data?.keys ?? []}
              onSelect={setSelectedKey}
            />
          </div>

          {/* Right Column */}
          <AlertFeed keys={data?.keys ?? []} />
        </div>
      </main>

      {/* Detail Panel (slide-in) */}
      {selectedKey && (
        <KeyDetailPanel
          keyData={selectedKey}
          onClose={() => setSelectedKey(null)}
        />
      )}
    </div>
  );
}
