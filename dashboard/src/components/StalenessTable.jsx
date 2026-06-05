import { useState, useMemo } from 'react';
import { ArrowUpDown, ArrowUp, ArrowDown, ChevronRight } from 'lucide-react';

function formatMs(ms) {
  if (ms === null || ms === undefined) return '—';
  if (ms >= 60000) return `${(ms / 60000).toFixed(1)}m`;
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
  return `${ms}ms`;
}

function StatusBadge({ status }) {
  const dots = { healthy: '●', warning: '●', critical: '●', unknown: '○' };
  return (
    <span className={`badge badge-${status}`}>
      {dots[status] || '○'} {status}
    </span>
  );
}

function StalenessBar({ staleness, threshold }) {
  if (staleness === null || threshold === null || threshold === 0) return <span style={{ color: 'var(--text-muted)' }}>—</span>;
  const pct = Math.min((staleness / threshold) * 100, 200);
  const color = pct <= 80 ? 'var(--green)' : pct <= 100 ? 'var(--yellow)' : 'var(--red)';
  return (
    <div className="staleness-bar-wrap">
      <code style={{ fontSize: '0.75rem', color, minWidth: 56, textAlign: 'right' }}>
        {formatMs(staleness)}
      </code>
      <div className="staleness-bar">
        <div
          className="staleness-bar-fill"
          style={{ width: `${Math.min(pct, 100)}%`, background: color }}
        />
      </div>
    </div>
  );
}

const COLS = [
  { key: 'key_name', label: 'Key' },
  { key: 'owning_service', label: 'Service' },
  { key: 'status', label: 'Status' },
  { key: 'current_staleness_ms', label: 'Staleness' },
  { key: 'sla_ms', label: 'SLA' },
  { key: 'sla_breach_pct', label: 'Breach %' },
  { key: 'breach_count_24h', label: '24h Breaches' },
];

export default function StalenessTable({ keys, onSelect }) {
  const [sortCol, setSortCol] = useState('sla_breach_pct');
  const [sortDir, setSortDir] = useState('desc');
  const [filter, setFilter] = useState('');

  const handleSort = (col) => {
    if (sortCol === col) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortCol(col);
      setSortDir('desc');
    }
  };

  const sorted = useMemo(() => {
    let data = [...(keys || [])];
    if (filter) {
      const q = filter.toLowerCase();
      data = data.filter(
        (k) =>
          k.key_name?.toLowerCase().includes(q) ||
          k.owning_service?.toLowerCase().includes(q) ||
          k.status?.toLowerCase().includes(q)
      );
    }
    data.sort((a, b) => {
      const av = a[sortCol] ?? '';
      const bv = b[sortCol] ?? '';
      if (typeof av === 'number' && typeof bv === 'number') {
        return sortDir === 'asc' ? av - bv : bv - av;
      }
      return sortDir === 'asc'
        ? String(av).localeCompare(String(bv))
        : String(bv).localeCompare(String(av));
    });
    return data;
  }, [keys, sortCol, sortDir, filter]);

  const SortIcon = ({ col }) => {
    if (sortCol !== col) return <ArrowUpDown size={12} style={{ opacity: 0.4 }} />;
    return sortDir === 'asc'
      ? <ArrowUp size={12} style={{ color: 'var(--accent-light)' }} />
      : <ArrowDown size={12} style={{ color: 'var(--accent-light)' }} />;
  };

  return (
    <div className="card card-elevated">
      <div className="card-header">
        <div className="card-title">
          <div className="card-title-icon" style={{ background: 'rgba(16,185,129,0.15)' }}>
            🗝
          </div>
          Cache Key Monitor
          <span style={{
            background: 'var(--bg-input)',
            border: '1px solid var(--border)',
            borderRadius: 100,
            padding: '1px 8px',
            fontSize: '0.7rem',
            color: 'var(--text-muted)',
          }}>
            {sorted.length} keys
          </span>
        </div>
        <input
          id="table-filter-input"
          type="text"
          placeholder="Filter keys…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          style={{
            background: 'var(--bg-input)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius-sm)',
            padding: '6px 12px',
            fontSize: '0.8rem',
            color: 'var(--text-primary)',
            outline: 'none',
            width: 180,
          }}
        />
      </div>

      <div className="table-wrapper">
        {sorted.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-icon">🔍</div>
            <div className="empty-state-text">No keys found. Run <code>make seed</code> to add example data.</div>
          </div>
        ) : (
          <table id="staleness-table">
            <thead>
              <tr>
                {COLS.map((col) => (
                  <th key={col.key} onClick={() => handleSort(col.key)}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                      {col.label} <SortIcon col={col.key} />
                    </div>
                  </th>
                ))}
                <th />
              </tr>
            </thead>
            <tbody>
              {sorted.map((key) => (
                <tr
                  key={key.key_name}
                  onClick={() => onSelect(key)}
                  id={`row-${key.key_name.replace(/[^a-zA-Z0-9]/g, '-')}`}
                >
                  <td className="key-name">{key.key_name}</td>
                  <td>
                    <span style={{
                      background: 'var(--bg-input)',
                      borderRadius: 'var(--radius-sm)',
                      padding: '2px 8px',
                      fontSize: '0.75rem',
                      color: 'var(--text-secondary)',
                    }}>
                      {key.owning_service}
                    </span>
                  </td>
                  <td><StatusBadge status={key.status || 'unknown'} /></td>
                  <td className="staleness-cell">
                    <StalenessBar staleness={key.current_staleness_ms} threshold={key.sla_ms} />
                  </td>
                  <td style={{ color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', fontSize: '0.78rem' }}>
                    {formatMs(key.sla_ms)}
                  </td>
                  <td>
                    {key.sla_breach_pct > 0 ? (
                      <span style={{
                        color: key.sla_breach_pct > 0.5 ? 'var(--red)' : 'var(--yellow)',
                        fontFamily: 'JetBrains Mono',
                        fontSize: '0.78rem',
                        fontWeight: 600,
                      }}>
                        +{(key.sla_breach_pct * 100).toFixed(0)}%
                      </span>
                    ) : (
                      <span style={{ color: 'var(--green)', fontSize: '0.78rem' }}>✓</span>
                    )}
                  </td>
                  <td style={{ textAlign: 'center' }}>
                    {key.breach_count_24h > 0 ? (
                      <span style={{ color: 'var(--red)', fontSize: '0.78rem', fontWeight: 600 }}>
                        {key.breach_count_24h}
                      </span>
                    ) : (
                      <span style={{ color: 'var(--text-muted)', fontSize: '0.78rem' }}>0</span>
                    )}
                  </td>
                  <td>
                    <ChevronRight size={15} color="var(--text-muted)" />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
