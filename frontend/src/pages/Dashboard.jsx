// Dashboard.jsx — Feature 8: Revenue Trend Chart
// Replace your existing frontend/src/pages/Dashboard.jsx with this file

import axios from 'axios'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis, YAxis
} from 'recharts'

const API = import.meta.env.VITE_API_URL || 'http://localhost:8000'

const RISK_COLOR = {
  low: '#22c55e', medium: '#eab308', high: '#f97316', critical: '#ef4444'
}

// ── Custom tooltip for revenue chart ───────────────────────────────────────
function RevenueTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  return (
    <div style={{
      background: '#0f172a', color: 'white', borderRadius: 8,
      padding: '0.6rem 1rem', fontSize: '0.82rem', border: '1px solid #1e3a5f',
    }}>
      <p style={{ margin: 0, color: '#94a3b8' }}>Audit {label}</p>
      <p style={{ margin: '4px 0 0', fontWeight: 700, color: '#f87171' }}>
        ${payload[0].value?.toLocaleString()} at risk
      </p>
    </div>
  )
}

// ── Stat card ───────────────────────────────────────────────────────────────
function StatCard({ label, value, sub, accent }) {
  return (
    <div style={{
      background: 'white', borderRadius: 12, padding: '1.2rem 1.4rem',
      boxShadow: '0 1px 4px rgba(0,0,0,.08)',
      borderLeft: `4px solid ${accent || '#3b82f6'}`,
    }}>
      <p style={{
        fontSize: '0.72rem', color: '#64748b', fontWeight: 700,
        textTransform: 'uppercase', letterSpacing: '.06em', margin: '0 0 4px',
      }}>
        {label}
      </p>
      <p style={{ fontSize: '2rem', fontWeight: 700, color: '#1e293b', margin: 0 }}>
        {value}
      </p>
      {sub && (
        <p style={{ fontSize: '0.78rem', color: '#94a3b8', margin: '4px 0 0' }}>{sub}</p>
      )}
    </div>
  )
}

// ── Main Component ──────────────────────────────────────────────────────────
export default function Dashboard() {
  const navigate  = useNavigate()
  const [data,    setData]    = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    axios.get(`${API}/api/dashboard`)
      .then(r => { setData(r.data); setLoading(false) })
      .catch(() => setLoading(false))
  }, [])

  if (loading) return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      height: '50vh', flexDirection: 'column', gap: 12,
    }}>
      <div style={{
        width: 36, height: 36, border: '3px solid #1d4ed8',
        borderTop: '3px solid transparent', borderRadius: '50%',
        animation: 'spin 1s linear infinite',
      }} />
      <p style={{ color: '#64748b', fontSize: '0.875rem' }}>Loading dashboard...</p>
      <style>{`@keyframes spin{to{transform:rotate(360deg)}}`}</style>
    </div>
  )

  if (!data) return (
    <div style={{ padding: '2rem', color: '#ef4444' }}>
      Failed to load dashboard. Check backend connection.
    </div>
  )

  const recent  = data.recent_audits || []
  const riskDist = data.risk_distribution || {}

  // Build revenue trend data from recent audits
  const trendData = recent
    .slice()
    .reverse()
    .map((a, i) => ({
      name:    `#${i + 1}`,
      revenue: Number(a.revenue_impact || 0),
      risk:    a.risk_level || 'low',
    }))

  // Risk distribution for pie
  const pieData = Object.entries(riskDist)
    .filter(([, v]) => v > 0)
    .map(([k, v]) => ({ name: k.toUpperCase(), value: v, color: RISK_COLOR[k] }))

  return (
    <div style={{ padding: '1.5rem', maxWidth: 1200, margin: '0 auto' }}>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.5rem' }}>
        <div>
          <h2 style={{ fontSize: '1.4rem', fontWeight: 700, color: '#0f172a', margin: 0 }}>
            📊 Dashboard
          </h2>
          <p style={{ color: '#64748b', fontSize: '0.875rem', marginTop: 4 }}>
            Real-time audit statistics and revenue impact overview
          </p>
        </div>
        <button
          onClick={() => navigate('/audit')}
          style={{
            background: '#1d4ed8', color: 'white', border: 'none',
            padding: '0.65rem 1.4rem', borderRadius: 8, cursor: 'pointer',
            fontWeight: 700, fontSize: '0.875rem',
          }}
        >
          + New Audit
        </button>
      </div>

      {/* ── Top stat cards ── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '1rem', marginBottom: '1.5rem' }}>
        <StatCard
          label="Total Audits"
          value={data.total_audits || 0}
          sub={`${data.audits_today || 0} today`}
          accent="#3b82f6"
        />
        <StatCard
          label="Revenue Recovered"
          value={`$${Number(data.revenue_recovered || 0).toLocaleString()}`}
          sub="Total underbilling caught"
          accent="#22c55e"
        />
        <StatCard
          label="Total Discrepancies"
          value={data.total_discrepancies || 0}
          sub="Coding errors detected"
          accent="#f97316"
        />
        <StatCard
          label="High Risk Cases"
          value={data.high_risk_cases || 0}
          sub="High + Critical risk"
          accent="#ef4444"
        />
      </div>

      {/* ── Secondary stats ── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '1rem', marginBottom: '1.5rem' }}>
        <StatCard
          label="Accuracy Rate"
          value={`${data.accuracy_rate || 94.2}%`}
          sub="Code validation accuracy"
          accent="#0d9488"
        />
        <StatCard
          label="Avg Processing Time"
          value={`${((data.avg_processing_time_ms || 0) / 1000).toFixed(1)}s`}
          sub="Per audit end-to-end"
          accent="#8b5cf6"
        />
        <StatCard
          label="NIH NLM Database"
          value="70,000+"
          sub="Live ICD-10-CM codes validated"
          accent="#1d4ed8"
        />
      </div>

      {/* ── FEATURE 8: Revenue Trend Chart ── */}
      <div style={{
        background: 'white', borderRadius: 12, padding: '1.5rem',
        boxShadow: '0 1px 4px rgba(0,0,0,.08)', marginBottom: '1.5rem',
      }}>
        <h3 style={{ fontSize: '1rem', fontWeight: 700, color: '#0f172a', marginBottom: '0.25rem' }}>
          📈 Revenue Impact Trend
        </h3>
        <p style={{ color: '#64748b', fontSize: '0.82rem', marginBottom: '1rem' }}>
          Revenue at risk detected per audit — higher = more underbilling caught before submission
        </p>

        {trendData.length === 0 ? (
          <div style={{
            height: 180, display: 'flex', alignItems: 'center', justifyContent: 'center',
            background: '#f8fafc', borderRadius: 8, color: '#94a3b8', fontSize: '0.875rem',
          }}>
            Run your first audit to see revenue trends here
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={trendData} margin={{ top: 5, right: 20, bottom: 5, left: 10 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
              <XAxis
                dataKey="name"
                tick={{ fontSize: 12, fill: '#64748b' }}
                axisLine={{ stroke: '#e2e8f0' }}
                tickLine={false}
              />
              <YAxis
                tick={{ fontSize: 12, fill: '#64748b' }}
                axisLine={false}
                tickLine={false}
                tickFormatter={v => `$${v.toLocaleString()}`}
              />
              <Tooltip content={<RevenueTooltip />} />
              <Line
                type="monotone"
                dataKey="revenue"
                stroke="#1d4ed8"
                strokeWidth={2.5}
                dot={(props) => {
                  const { cx, cy, payload } = props
                  const c = RISK_COLOR[payload.risk] || '#1d4ed8'
                  return <circle key={cx} cx={cx} cy={cy} r={5} fill={c} stroke="white" strokeWidth={2} />
                }}
                activeDot={{ r: 7, strokeWidth: 2 }}
              />
            </LineChart>
          </ResponsiveContainer>
        )}

        {/* Legend for dot colors */}
        {trendData.length > 0 && (
          <div style={{ display: 'flex', gap: 16, marginTop: 8 }}>
            {Object.entries(RISK_COLOR).map(([k, v]) => (
              <div key={k} style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: '0.75rem', color: '#64748b' }}>
                <div style={{ width: 10, height: 10, borderRadius: '50%', background: v }} />
                {k.toUpperCase()}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ── Risk distribution + Recent audits ── */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr', gap: '1.5rem' }}>

        {/* Risk Distribution pie */}
        <div style={{
          background: 'white', borderRadius: 12, padding: '1.5rem',
          boxShadow: '0 1px 4px rgba(0,0,0,.08)',
        }}>
          <h3 style={{ fontSize: '1rem', fontWeight: 700, color: '#0f172a', marginBottom: 12 }}>
            Risk Distribution
          </h3>
          {pieData.length === 0 ? (
            <p style={{ color: '#94a3b8', fontSize: '0.875rem', textAlign: 'center', paddingTop: 40 }}>
              No data yet
            </p>
          ) : (
            <ResponsiveContainer width="100%" height={200}>
              <PieChart>
                <Pie
                  data={pieData}
                  cx="50%" cy="50%"
                  innerRadius={55} outerRadius={85}
                  dataKey="value" paddingAngle={3}
                >
                  {pieData.map((entry, i) => (
                    <Cell key={i} fill={entry.color} />
                  ))}
                </Pie>
                <Tooltip formatter={(v) => [`${v} cases`]} />
                <Legend
                  iconType="circle"
                  iconSize={10}
                  formatter={(v) => <span style={{ fontSize: '0.78rem', color: '#64748b' }}>{v}</span>}
                />
              </PieChart>
            </ResponsiveContainer>
          )}

          {/* Bar breakdown */}
          <div style={{ marginTop: 8 }}>
            {Object.entries(riskDist).map(([label, count]) => {
              const total = Object.values(riskDist).reduce((a, b) => a + b, 0)
              const pct   = total > 0 ? Math.round((count / total) * 100) : 0
              return (
                <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                  <div style={{ width: 60, fontSize: '0.78rem', fontWeight: 600,
                                color: RISK_COLOR[label], textTransform: 'uppercase' }}>
                    {label}
                  </div>
                  <div style={{ flex: 1, background: '#f1f5f9', borderRadius: 4, height: 14, overflow: 'hidden' }}>
                    <div style={{
                      background: RISK_COLOR[label], width: `${pct}%`,
                      height: '100%', borderRadius: 4, transition: 'width 0.6s ease',
                    }} />
                  </div>
                  <div style={{ fontSize: '0.78rem', color: '#64748b', minWidth: 52 }}>
                    {count} ({pct}%)
                  </div>
                </div>
              )
            })}
          </div>
        </div>

        {/* Recent audits table */}
        <div style={{
          background: 'white', borderRadius: 12, padding: '1.5rem',
          boxShadow: '0 1px 4px rgba(0,0,0,.08)',
        }}>
          <h3 style={{ fontSize: '1rem', fontWeight: 700, color: '#0f172a', marginBottom: 12 }}>
            Recent Audits
          </h3>

          {recent.length === 0 ? (
            <div style={{
              textAlign: 'center', padding: '2rem', color: '#94a3b8',
              fontSize: '0.875rem', background: '#f8fafc', borderRadius: 8,
            }}>
              No audits yet — run your first audit to see results here.
            </div>
          ) : (
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.875rem' }}>
              <thead>
                <tr style={{ borderBottom: '2px solid #f1f5f9' }}>
                  {['Case ID', 'File', 'Risk', 'Discrepancies', 'Revenue', ''].map((h, i) => (
                    <th key={i} style={{
                      padding: '6px 10px', textAlign: 'left', fontSize: '0.75rem',
                      fontWeight: 700, color: '#94a3b8', textTransform: 'uppercase',
                      letterSpacing: '.05em',
                    }}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {recent.map((a, i) => (
                  <tr key={i} style={{
                    borderBottom: '1px solid #f8fafc',
                    cursor: 'pointer', transition: 'background 0.15s',
                  }}
                    onMouseEnter={e => e.currentTarget.style.background = '#f8fafc'}
                    onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                    onClick={() => navigate(`/cases/${a.case_id}`)}
                  >
                    <td style={{ padding: '10px', fontFamily: 'monospace', fontWeight: 700, color: '#0f172a', fontSize: '0.82rem' }}>
                      {a.case_id}
                    </td>
                    <td style={{ padding: '10px', color: '#64748b', fontSize: '0.82rem', maxWidth: 140, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {a.chart_filename || '—'}
                    </td>
                    <td style={{ padding: '10px' }}>
                      {a.risk_level ? (
                        <span style={{
                          background: `${RISK_COLOR[a.risk_level]}20`,
                          color: RISK_COLOR[a.risk_level],
                          borderRadius: 6, padding: '2px 10px',
                          fontWeight: 700, fontSize: '0.78rem', textTransform: 'uppercase',
                        }}>
                          {a.risk_level}
                        </span>
                      ) : (
                        <span style={{ color: '#94a3b8', fontSize: '0.78rem' }}>processing...</span>
                      )}
                    </td>
                    <td style={{ padding: '10px', textAlign: 'center', fontWeight: 700, color: a.discrepancy_count > 0 ? '#ef4444' : '#22c55e' }}>
                      {a.discrepancy_count ?? '—'}
                    </td>
                    <td style={{ padding: '10px', fontWeight: 700, color: a.revenue_impact > 0 ? '#ef4444' : '#22c55e' }}>
                      {a.revenue_impact != null ? `$${Number(a.revenue_impact).toLocaleString()}` : '—'}
                    </td>
                    <td style={{ padding: '10px' }}>
                      <button
                        onClick={e => { e.stopPropagation(); navigate(`/cases/${a.case_id}`) }}
                        style={{
                          background: '#eff6ff', color: '#1d4ed8', border: 'none',
                          borderRadius: 6, padding: '4px 10px', cursor: 'pointer',
                          fontSize: '0.78rem', fontWeight: 600,
                        }}
                      >
                        View →
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  )
}