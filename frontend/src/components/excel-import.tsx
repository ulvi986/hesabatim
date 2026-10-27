'use client';
import { useEffect, useRef, useState } from 'react';
import { Check, FileSpreadsheet, X } from 'lucide-react';
import { request, Row, money, units } from '@/lib/api';
import { ErrorBox, Spinner } from './shared';
export const spreadsheetTypes = '.xlsx,.xlsm,.csv';
export const isSpreadsheet = (f: File) => /\.(xlsx|xlsm|xls|csv)$/i.test(f.name);
const fieldLabels: Row = { name: 'Məhsul', sku: 'Kod', quantity: 'Miqdar', unit: 'Vahid', purchase_price: 'Alış qiyməti', selling_price: 'Satış qiyməti', category: 'Kateqoriya', description: 'Təsvir', low_stock_threshold: 'Min. stok' };
export async function importSpreadsheet(base: string, file: File, commit: boolean) { const form = new FormData(); form.append('file', file); return (await request(`${base}/inventory/import?dry_run=${!commit}`, { method: 'POST', body: form })).json(); }
export function ImportPreview({ preview, currency }: { preview: Row; currency: string }) {
  const [filter, setFilter] = useState('all');
  const rows: Row[] = preview.rows || [], shown = filter === 'all' ? rows : rows.filter(r => filter === 'error' ? r.status === 'error' : r.action === filter && r.status === 'ok');
  const unit = (u?: string) => u ? units[u] || u : '';
  return <div className="import-preview">
    <div className="row wrap chip-row import-chips">{([['all', 'Hamısı', rows.length], ['create', 'Yeni', preview.created], ['update', 'Yenilənən', preview.updated], ['error', 'Səhv', preview.errors]] as [string, string, number][]).map(([key, label, count]) => <button key={key} className={`chip${filter === key ? ' active' : ''}${key === 'error' && count ? ' danger' : ''}`} onClick={() => setFilter(key)}>{label} <b>{count}</b></button>)}</div>
    {preview.columns?.length > 0 && <p className="import-columns"><FileSpreadsheet size={14} />{preview.guessed ? 'Başlıq tapılmadı, sütunlar təxmin edildi: ' : 'Tanınan sütunlar: '}{preview.columns.map((c: Row) => <span key={c.column + c.field}>{c.column} → <b>{fieldLabels[c.field] || c.field}</b></span>)}{preview.ignored?.length > 0 && <small className="muted"> · nəzərə alınmadı: {preview.ignored.join(', ')}</small>}</p>}
    <div className="table-scroll import-table"><table><thead><tr><th>Sətir</th><th>Məhsul</th><th>Miqdar</th><th>Alış</th><th>Satış</th><th>Kateqoriya</th><th>Nəticə</th></tr></thead><tbody>{shown.map(r => <tr key={r.row} className={r.status === 'error' ? 'row-error' : ''}><td className="muted">{r.row}</td><td><strong>{r.name || '—'}</strong>{r.sku && <small className="block muted">{r.sku}</small>}</td><td>{r.old_quantity != null && r.old_quantity !== r.quantity ? <><span className="muted">{r.old_quantity} → </span><strong>{r.quantity ?? '—'}</strong></> : r.quantity ?? <span className="muted">—</span>} {unit(r.unit)}</td><td>{r.purchase_price ? money(r.purchase_price, currency) : <span className="muted">—</span>}</td><td>{r.selling_price ? money(r.selling_price, currency) : <span className="muted">—</span>}</td><td>{r.category || <span className="muted">—</span>}</td><td>{r.status === 'error' ? <span className="status-badge danger">{String(r.error).replace(/^(\w+):/, (m, k) => fieldLabels[k] ? `${fieldLabels[k]}:` : m)}</span> : <span className={`status-badge ${r.action}`}>{r.action === 'create' ? 'Yeni məhsul' : 'Yenilənəcək'}</span>}</td></tr>)}</tbody></table>{!shown.length && <p className="muted center">Bu filtrdə sətir yoxdur.</p>}</div>
  </div>;
}
/** Chat card for a dropped spreadsheet: checks it on the server (dry run), shows every product, saves on confirm. */
export function ExcelImportCard({ base, file, currency, done }: { base: string; file: File; currency: string; done: () => void }) {
  const [preview, setPreview] = useState<Row | null>(null), [state, setState] = useState<'checking' | 'ready' | 'saving' | 'saved' | 'cancelled'>('checking'), [error, setError] = useState(''); const lock = useRef(false);
  useEffect(() => { let active = true; importSpreadsheet(base, file, false).then(r => { if (active) { setPreview(r); setState('ready'); } }).catch(e => { if (active) { setError((e as Error).message); setState('cancelled'); } }); return () => { active = false; }; }, [base, file]);
  async function save() { if (lock.current) return; lock.current = true; setState('saving'); setError(''); try { setPreview(await importSpreadsheet(base, file, true)); setState('saved'); done(); } catch (e) { setError((e as Error).message); setState('ready'); } finally { lock.current = false; } }
  const valid = preview ? preview.created + preview.updated : 0;
  return <div className={`action-card import-card ${state === 'saved' ? 'executed' : state === 'cancelled' ? 'rejected' : 'pending'}`}><div className="action-heading"><span><FileSpreadsheet size={17} /> {file.name}</span><span className="status-badge">{state === 'checking' ? 'Yoxlanılır…' : state === 'saved' ? 'Yadda saxlanıldı' : state === 'cancelled' ? (error ? 'Oxunmadı' : 'Ləğv edildi') : 'Təsdiq gözləyir'}</span></div>
    {state === 'checking' ? <Spinner /> : preview && <><h4>{state === 'saved' ? `${preview.created} yeni məhsul əlavə edildi, ${preview.updated} məhsul yeniləndi.` : `Faylda ${preview.rows.length} məhsul tapdım: ${preview.created} yeni, ${preview.updated} mövcud${preview.errors ? `, ${preview.errors} sətirdə səhv var` : ''}.`}</h4><ImportPreview preview={preview} currency={currency} />{state !== 'saved' && preview.updated > 0 && <p className="notice">Mövcud məhsulların (eyni kod və ya ad) miqdarı fayldakı rəqəmlə əvəz olunacaq. Dəyişiklik tarixçədə görünəcək.</p>}</>}
    <ErrorBox error={error} />
    {(state === 'ready' || state === 'saving') && <div className="action-buttons"><button className="primary" disabled={state === 'saving' || !valid} onClick={save}><Check size={16} />{state === 'saving' ? 'Saxlanılır…' : `${valid} məhsulu təsdiqlə`}</button><button className="secondary" disabled={state === 'saving'} onClick={() => setState('cancelled')}><X size={16} />Ləğv et</button></div>}
    {state === 'saved' && <p className="success-line"><Check size={16} />Məhsullar inventara yazıldı. İndi onlar haqqında sual verə bilərsiniz.</p>}
  </div>;
}
