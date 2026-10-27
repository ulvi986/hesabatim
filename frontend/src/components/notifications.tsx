'use client';
import { useState } from 'react';
import { BellRing, Cake, CheckCheck, Gift, PackageX, TrendingDown, UserPlus } from 'lucide-react';
import { api, Row } from '@/lib/api';
import { Empty, ErrorBox, Field } from './shared';

const kinds: Record<string, { label: string; icon: typeof BellRing }> = {
  out_of_stock: { label: 'Stok bitib', icon: PackageX }, low_stock: { label: 'Stok azalır', icon: TrendingDown },
  reward_ready: { label: 'Hədiyyə hazırdır', icon: Gift }, birthday: { label: 'Ad günü', icon: Cake }, new_customer: { label: 'Yeni müştəri', icon: UserPlus },
};

// Live alerts (stock, loyalty, birthdays, QR sign-ups). The workspace polls the same endpoint for the bell badge.
export default function Notifications({ base, data, changed, navigate }: { base: string; data: Row | null; changed: (d: Row) => void; navigate: (tab: string) => void }) {
  const [error, setError] = useState(''), [busy, setBusy] = useState(false), [filter, setFilter] = useState('');
  async function run(work: () => Promise<Row | null>) { if (busy) return; setBusy(true); setError(''); try { const r = await work(); changed(r || await api(`${base}/notifications`)); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } }
  const items: Row[] = data?.items || [], s: Row = data?.settings || {};
  const counts = items.reduce((m: Row, n) => ({ ...m, [n.type]: (m[n.type] || 0) + 1 }), {});
  const shown = filter ? items.filter(n => n.type === filter) : items;
  const save = (patch: Row) => run(() => api(`${base}/notifications/settings`, 'PATCH', patch));
  return <><div className="page-heading"><div><span className="eyebrow">VAXTINDA XƏBƏR TUTUN</span><h1>Bildirişlər</h1><p className="muted">Stok azalanda, hədiyyə hazır olanda və yeni müştəri gələndə burada görünür.</p></div>{data?.unread > 0 && <button className="secondary" disabled={busy} onClick={() => run(async () => { await api(`${base}/notifications/seen`, 'POST'); return null; })}><CheckCheck size={17} />Hamısını oxunmuş et</button>}</div><ErrorBox error={error} />
    <div className="settings-grid"><section className="panel"><div className="row wrap chip-row" style={{ marginBottom: 12 }}><button className={`chip ${filter === '' ? 'active' : ''}`} onClick={() => setFilter('')}>Hamısı<b>{items.length}</b></button>{Object.entries(kinds).filter(([k]) => counts[k]).map(([k, v]) => <button key={k} className={`chip ${filter === k ? 'active' : ''}`} onClick={() => setFilter(k)}>{v.label}<b>{counts[k]}</b></button>)}</div>
      {!data ? <p className="muted">Yüklənir…</p> : shown.length ? <ul className="notification-list">{shown.map(n => { const Icon = kinds[n.type]?.icon || BellRing; return <li key={n.key} className={`notification ${n.severity}${n.unread ? ' unread' : ''}`}><Icon size={18} /><div><strong>{n.title}</strong><small className="block muted">{n.body}</small></div><button className="text-button" onClick={() => navigate(n.target)}>{n.target === 'inventory' ? 'İnventara keç' : 'Müştərilərə keç'}</button></li>; })}</ul> : <Empty title="Hər şey qaydasındadır">Hazırda diqqət tələb edən bir şey yoxdur.</Empty>}</section>
      <section className="panel"><h2><BellRing size={19} /> Nə zaman xəbər verək?</h2>
        <label className="check-label"><input type="checkbox" checked={!!s.low_stock} disabled={busy} onChange={e => save({ low_stock: e.target.checked })} />Stok azalanda</label>
        {s.low_stock && <div className="stack" style={{ paddingLeft: 26, display: 'grid', gap: 8 }}>
          <label className="check-label"><input type="radio" name="threshold-mode" checked={s.low_stock_threshold == null} disabled={busy} onChange={() => save({ use_product_thresholds: true })} />Hər məhsulun öz minimumuna görə</label>
          <label className="check-label"><input type="radio" name="threshold-mode" checked={s.low_stock_threshold != null} disabled={busy} onChange={() => save({ low_stock_threshold: 5 })} />Bütün məhsullar üçün eyni hədd</label>
          {s.low_stock_threshold != null && <Field label="Stok bu saya və ya daha aza düşəndə"><form className="row" onSubmit={e => { e.preventDefault(); const v = Number(new FormData(e.currentTarget).get('t')); save({ low_stock_threshold: v }); }}><input key={s.low_stock_threshold} name="t" type="number" min={0} defaultValue={s.low_stock_threshold} style={{ width: 110 }} /><button className="secondary" disabled={busy}>Saxla</button></form></Field>}
        </div>}
        <label className="check-label"><input type="checkbox" checked={!!s.out_of_stock} disabled={busy} onChange={e => save({ out_of_stock: e.target.checked })} />Stok tam bitəndə</label>
        <label className="check-label"><input type="checkbox" checked={!!s.reward_ready} disabled={busy} onChange={e => save({ reward_ready: e.target.checked })} />Müştərinin hədiyyəsi hazır olanda</label>
        <label className="check-label"><input type="checkbox" checked={!!s.birthdays} disabled={busy} onChange={e => save({ birthdays: e.target.checked })} />Müştərinin ad günü yaxınlaşanda ({s.birthday_days ?? 7} gün qalmış)</label>
        <label className="check-label"><input type="checkbox" checked={!!s.new_customers} disabled={busy} onChange={e => save({ new_customers: e.target.checked })} />QR kod ilə yeni müştəri qeydiyyatdan keçəndə</label>
        <BrowserAlerts />
      </section></div>
  </>;
}

function BrowserAlerts() {
  const supported = typeof window !== 'undefined' && 'Notification' in window;
  const [permission, setPermission] = useState(supported ? Notification.permission : 'denied');
  if (!supported) return null;
  return <p className="muted" style={{ marginTop: 12 }}>{permission === 'granted' ? 'Brauzer bildirişləri açıqdır: sayt açıq olanda yeni xəbərdarlıq gələrsə, ekranda görünəcək.' : permission === 'denied' ? 'Brauzer bildirişləri bloklanıb. Brauzerin sayt ayarlarından icazə verə bilərsiniz.' : <button className="secondary" onClick={async () => setPermission(await Notification.requestPermission())}><BellRing size={16} />Brauzer bildirişlərini aç</button>}</p>;
}
