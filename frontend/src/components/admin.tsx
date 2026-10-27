'use client';
import { useCallback, useEffect, useState } from 'react';
import { ShieldCheck, LogOut, Search, Database, ShieldAlert } from 'lucide-react';
import { ApiError, Row } from '@/lib/api';
import { Empty, ErrorBox, Field, Spinner } from './shared';

// Read-only admin panel, served only at the secret /<ADMIN_PATH> to allowed IPs (src/proxy.ts). The secret is
// never compiled into the bundle: the API lives at /api/v1/<the same segment>, read from the address bar.
const adminApiBase = () => '/api/v1/' + window.location.pathname.split('/')[1];

async function request(path: string, init: RequestInit = {}) {
  const headers = new Headers(init.headers);
  if (init.body) headers.set('Content-Type', 'application/json');
  let response: Response;
  try { response = await fetch(path, { ...init, headers, credentials: 'include', cache: 'no-store' }); }
  catch { throw new ApiError('not_reachable', 404); }
  if (!response.ok) { const data = await response.json().catch(() => ({})); throw new ApiError(data.error?.message || 'Xəta', response.status); }
  return response;
}
const names: Record<string, string> = { profiles: 'İstifadəçilər', businesses: 'Bizneslər', business_members: 'Üzvlüklər', products: 'Məhsullar', inventory: 'İnventar', inventory_events: 'Stok hərəkətləri', sales: 'Satışlar', sale_items: 'Satış sətirləri', customers: 'Müştərilər', loyalty_events: 'Sadiqlik', categories: 'Kateqoriyalar', conversations: 'Söhbətlər', messages: 'Mesajlar', ai_actions: 'AI əməliyyatları', audit_logs: 'Audit jurnalı', local_credentials: 'Giriş məlumatları', idempotency_keys: 'Idempotency', product_aliases: 'Məhsul adları' };

async function adminApi(path: string, method = 'GET', data?: unknown) {
  const response = await request(adminApiBase() + path, { method, body: data === undefined ? undefined : JSON.stringify(data) });
  return response.status === 204 ? null : response.json();
}
const cell = (v: unknown) => v == null ? '—' : typeof v === 'object' ? JSON.stringify(v) : String(v);

export default function Admin() {
  const [admin, setAdmin] = useState<string | null>(null), [loading, setLoading] = useState(true);
  useEffect(() => { adminApi('/session').then(s => setAdmin(s.username)).catch(() => {}).finally(() => setLoading(false)); }, []);
  if (loading) return <Spinner />;
  return admin ? <Panel admin={admin} logout={() => adminApi('/logout', 'POST').finally(() => setAdmin(null))} /> : <Login done={setAdmin} />;
}

function Login({ done }: { done: (name: string) => void }) {
  const [username, setUsername] = useState(''), [password, setPassword] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  async function submit(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError('');
    try { done((await adminApi('/login', 'POST', { username, password })).username); }
    catch (err) { setError(err instanceof Error ? err.message : 'Xəta'); setPassword(''); }
    finally { setBusy(false); }
  }
  return <div className="auth-page"><section className="auth-story"><div className="brand"><span className="brand-icon">h<span>•</span></span>hesabatım.</div><div><span className="eyebrow">ADMİN PANELİ</span><h1>Bütün məlumatlar,<br /><em>yalnız oxumaq üçün.</em></h1><p>Bu paneldən heç bir məlumat dəyişdirilə və ya silinə bilməz.</p></div><small><ShieldCheck size={14} /> Hər giriş cəhdi jurnala yazılır.</small></section>
    <section className="auth-form"><div><span className="eyebrow">TƏHLÜKƏSİZ GİRİŞ</span><h2>Admin girişi</h2><p className="muted">Bir neçə yanlış cəhddən sonra bu ünvan bloklanır.</p><ErrorBox error={error} />
      <form onSubmit={submit}><Field label="İstifadəçi adı"><input value={username} onChange={e => setUsername(e.target.value)} autoComplete="username" required /></Field><Field label="Şifrə"><input type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete="current-password" required /></Field><button className="primary wide" disabled={busy}>{busy ? 'Yoxlanılır…' : 'Daxil ol'}</button></form></div></section></div>;
}

function Panel({ admin, logout }: { admin: string; logout: () => void }) {
  const [overview, setOverview] = useState<Row | null>(null), [table, setTable] = useState('profiles'), [data, setData] = useState<Row | null>(null);
  const [page, setPage] = useState(1), [q, setQ] = useState(''), [search, setSearch] = useState(''), [error, setError] = useState('');
  useEffect(() => { adminApi('/overview').then(setOverview).catch(e => setError(e.message)); }, []);
  const load = useCallback(() => { setData(null); adminApi(`/tables/${table}?page=${page}&size=50&q=${encodeURIComponent(search)}`).then(setData).catch(e => setError(e.message)); }, [table, page, search]);
  useEffect(load, [load]);
  const pages = data ? Math.max(1, Math.ceil(data.total / data.size)) : 1;
  return <div className="content" style={{ maxWidth: 1400, margin: '0 auto', padding: 24 }}>
    <div className="page-heading"><div><span className="eyebrow">ADMİN · YALNIZ OXUMAQ</span><h1>Bütün məlumatlar</h1></div><div className="row"><span className="tag"><ShieldCheck size={14} /> {admin}</span><button className="secondary" onClick={logout}><LogOut size={16} /> Çıxış</button></div></div>
    <ErrorBox error={error} />
    {overview && <div className="stats-grid">{Object.entries(overview.tables as Record<string, number>).map(([name, count]) => <div className={'stat-card clickable' + (name === table ? ' active' : '')} key={name} onClick={() => { setTable(name); setPage(1); setQ(''); setSearch(''); }}><div><span>{names[name] || name}</span><Database size={18} /></div><strong>{count}</strong><small className="mono">{name}</small></div>)}</div>}
    <div className="panel"><div className="panel-heading"><h3>{names[table] || table} <span className="muted">({data?.total ?? '…'})</span></h3>
      <form className="row" onSubmit={e => { e.preventDefault(); setPage(1); setSearch(q); }}><input className="search-input" placeholder="Axtar…" value={q} onChange={e => setQ(e.target.value)} /><button className="secondary"><Search size={16} /></button></form></div>
      {!data ? <Spinner /> : data.rows.length ? <div className="table-scroll"><table><thead><tr>{data.columns.map((c: string) => <th key={c}>{c}</th>)}</tr></thead><tbody>{data.rows.map((r: Row, i: number) => <tr key={i}>{data.columns.map((c: string) => <td key={c} className="mono" style={{ maxWidth: 320, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={cell(r[c])}>{cell(r[c])}</td>)}</tr>)}</tbody></table></div> : <Empty title="Qeyd yoxdur" />}
      <div className="pagination row"><button className="secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>Əvvəlki</button><span className="muted">{page} / {pages}</span><button className="secondary" disabled={page >= pages} onClick={() => setPage(page + 1)}>Növbəti</button></div></div>
    {overview && <div className="panel"><div className="panel-heading"><h3><ShieldAlert size={16} /> Son təhlükəsizlik hadisələri</h3></div>{overview.security_events.length ? <div className="table-scroll"><table><thead><tr><th>Hadisə</th><th>IP</th><th>Vaxt</th><th>Təfərrüat</th></tr></thead><tbody>{overview.security_events.map((s: Row, i: number) => <tr key={i}><td>{s.action}</td><td className="mono">{s.ip}</td><td>{s.at}</td><td className="mono" style={{ maxWidth: 420, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={cell(s.details)}>{cell(s.details)}</td></tr>)}</tbody></table></div> : <p className="muted">Hadisə yoxdur.</p>}</div>}
  </div>;
}
