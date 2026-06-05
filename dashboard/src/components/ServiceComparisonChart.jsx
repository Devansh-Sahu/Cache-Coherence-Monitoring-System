import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from 'recharts';
import { BarChart2 } from 'lucide-react';

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
      <div style={{ color: 'var(--accent-light)', fontFamily: 'JetBrains Mono' }}>
        Avg: {payload[0]?.value?.toFixed(0)}ms
      </div>
    </div>
  );
};

const SERVICE_COLORS = [
  '#6366f1', '#8b5cf6', '#ec4899', '#f59e0b', '#10b981', '#3b82f6', '#ef4444',
];

function aggregateByService(keys) {
  const map = {};
  for (const k of keys || []) {
    const svc = k.owning_service || 'unknown';
    if (!map[svc]) map[svc] = { staleness: [], sla: k.sla_ms, breaches: 0 };
    if (k.current_staleness_ms !== null) map[svc].staleness.push(k.current_staleness_ms);
    if (k.status === 'critical' || k.status === 'warning') map[svc].breaches++;
  }
  return Object.entries(map).map(([service, d]) => ({
    service,
    avg_staleness: d.staleness.length
      ? Math.round(d.staleness.reduce((a, b) => a + b, 0) / d.staleness.length)
      : 0,
    breaches: d.breaches,
  }));
}

export default function ServiceComparisonChart({ keys }) {
  const data = aggregateByService(keys);

  if (data.length === 0) {
    return (
      <div className="card card-elevated">
        <div className="card-header">
          <div className="card-title">
            <div className="card-title-icon" style={{ background: 'rgba(99,102,241,0.15)' }}>
              <BarChart2 size={14} color="var(--accent-light)" />
            </div>
            Per-Service Comparison
          </div>
        </div>
        <div className="empty-state">
          <div className="empty-state-icon">📊</div>
          <div className="empty-state-text">No data available</div>
        </div>
      </div>
    );
  }

  return (
    <div className="card card-elevated">
      <div className="card-header">
        <div className="card-title">
          <div className="card-title-icon" style={{ background: 'rgba(99,102,241,0.15)' }}>
            <BarChart2 size={14} color="var(--accent-light)" />
          </div>
          Avg Staleness by Service
        </div>
        <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>Current snapshot</div>
      </div>
      <ResponsiveContainer width="100%" height={160}>
        <BarChart data={data} barCategoryGap="35%">
          <XAxis
            dataKey="service"
            tick={{ fontSize: 11, fill: 'var(--text-muted)' }}
            tickLine={false}
            axisLine={false}
          />
          <YAxis
            tick={{ fontSize: 10, fill: 'var(--text-muted)' }}
            tickLine={false}
            axisLine={false}
            tickFormatter={(v) => v >= 1000 ? `${(v/1000).toFixed(1)}s` : `${v}ms`}
            width={42}
          />
          <Tooltip content={<CustomTooltip />} />
          <Bar dataKey="avg_staleness" radius={[4, 4, 0, 0]}>
            {data.map((_, i) => (
              <Cell key={i} fill={SERVICE_COLORS[i % SERVICE_COLORS.length]} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
