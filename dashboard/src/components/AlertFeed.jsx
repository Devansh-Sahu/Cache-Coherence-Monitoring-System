import { Bell, AlertTriangle, AlertCircle } from 'lucide-react';

function formatMs(ms) {
  if (!ms) return '—';
  if (ms >= 60000) return `${(ms / 60000).toFixed(1)}m`;
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
  return `${ms}ms`;
}

function timeAgo(ts) {
  const diff = Math.floor((Date.now() - new Date(ts)) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

// Generate synthetic alerts from dashboard data until real alert API exists
function syntheticAlerts(keys) {
  return (keys || [])
    .filter((k) => k.status === 'critical' || k.status === 'warning')
    .slice(0, 20)
    .map((k) => ({
      id: k.key_name,
      key_name: k.key_name,
      owning_service: k.owning_service,
      staleness_ms: k.current_staleness_ms,
      sla_ms: k.sla_ms,
      breach_pct: k.sla_breach_pct,
      status: k.status,
      timestamp: k.last_checked || new Date().toISOString(),
      llm_summary: null,
    }));
}

export default function AlertFeed({ keys }) {
  const alerts = syntheticAlerts(keys);

  return (
    <div className="card card-elevated">
      <div className="card-header">
        <div className="card-title">
          <div className="card-title-icon" style={{ background: 'rgba(239,68,68,0.15)' }}>
            <Bell size={14} color="var(--red)" />
          </div>
          Active Alerts
          {alerts.length > 0 && (
            <span style={{
              background: 'var(--red-bg)',
              color: 'var(--red)',
              borderRadius: 100,
              padding: '1px 8px',
              fontSize: '0.7rem',
              fontWeight: 700,
            }}>
              {alerts.length}
            </span>
          )}
        </div>
      </div>

      {alerts.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-icon">✅</div>
          <div className="empty-state-text">All keys within SLA</div>
        </div>
      ) : (
        <div className="alert-list" id="alert-feed-list">
          {alerts.map((alert) => (
            <div
              key={alert.id}
              className={`alert-item ${alert.status}`}
              id={`alert-${alert.key_name.replace(/[^a-zA-Z0-9]/g, '-')}`}
            >
              <div className="alert-header">
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  {alert.status === 'critical'
                    ? <AlertCircle size={13} color="var(--red)" />
                    : <AlertTriangle size={13} color="var(--yellow)" />
                  }
                  <span className="alert-key">{alert.key_name}</span>
                </div>
                <span className="alert-time">{timeAgo(alert.timestamp)}</span>
              </div>
              <div className="alert-summary">
                <span style={{ color: 'var(--text-muted)', fontSize: '0.72rem' }}>
                  {alert.owning_service} ·{' '}
                </span>
                <span style={{
                  fontFamily: 'JetBrains Mono',
                  color: alert.status === 'critical' ? 'var(--red)' : 'var(--yellow)',
                  fontSize: '0.78rem',
                }}>
                  {formatMs(alert.staleness_ms)}
                </span>
                <span style={{ color: 'var(--text-muted)', fontSize: '0.72rem' }}>
                  {' '}/ SLA {formatMs(alert.sla_ms)}
                </span>
                {alert.breach_pct > 0 && (
                  <span style={{
                    marginLeft: 6,
                    fontWeight: 700,
                    color: alert.status === 'critical' ? 'var(--red)' : 'var(--yellow)',
                    fontSize: '0.72rem',
                  }}>
                    (+{(alert.breach_pct * 100).toFixed(0)}% over SLA)
                  </span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
