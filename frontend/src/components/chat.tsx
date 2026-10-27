'use client';
import { useEffect, useRef, useState } from 'react';
import { ArrowUp, Boxes, ChartNoAxesCombined, Check, FileSpreadsheet, MessageSquarePlus, Paperclip, ReceiptText, Sparkles, X } from 'lucide-react';
import { api, request, Row, money, units } from '@/lib/api';
import { ErrorBox, Field, Spinner } from './shared';
import { ExcelImportCard, isSpreadsheet, spreadsheetTypes } from './excel-import';
const starters = [{ icon: Boxes, title: 'İnventarımı qur', text: 'Məndə 500 qapı, 300 taxta və 150 masa var.' }, { icon: ReceiptText, title: 'Satışımı qeyd et', text: 'Bu gün 20 qapını 150 manatdan satdım.' }, { icon: ChartNoAxesCombined, title: 'Biznesimə bax', text: 'Bu ay nə qədər gəlir əldə etmişəm?' }];
export default function Chat({ business, status, refresh }: { business: Row; status: Row; refresh: () => void }) {
  const [conversations, setConversations] = useState<Row[]>([]), [conv, setConv] = useState(''), [messages, setMessages] = useState<Row[]>([]);
  const [input, setInput] = useState(''), [busy, setBusy] = useState(false), [loading, setLoading] = useState(true), [error, setError] = useState('');
  const bottom = useRef<HTMLDivElement>(null), textarea = useRef<HTMLTextAreaElement>(null), locked = useRef(false), fileRef = useRef<HTMLInputElement>(null);
  // Spreadsheets dropped into the chat live only in this tab: the card itself checks and imports the file.
  const [uploads, setUploads] = useState<Row[]>([]), [dragging, setDragging] = useState(false), uploadSeq = useRef(0);
  const base = `/businesses/${business.id}`;
  const shown = [...messages, ...uploads.filter(u => u.conv === conv)].map((m, i) => ({ m, i, t: m.created_at ? Date.parse(m.created_at) : Infinity })).sort((a, b) => a.t - b.t || a.i - b.i).map(x => x.m);
  function attach(files: FileList | null | undefined) {
    const list = Array.from(files || []); if (!list.length) return; setError('');
    const bad = list.find(f => !isSpreadsheet(f)); if (bad) { setError(`«${bad.name}» Excel faylı deyil. .xlsx və ya .csv faylı atın.`); return; }
    const now = new Date().toISOString();
    setUploads(u => [...u, ...list.flatMap(file => { const n = ++uploadSeq.current; return [{ id: `upload-${n}`, conv, role: 'user', content: `📎 ${file.name}`, blocks: [], created_at: now }, { id: `upload-${n}-card`, conv, role: 'assistant', content: 'Faylı yoxladım. Məhsullara baxın və təsdiq edin:', blocks: [{ type: 'excel_import', file }], created_at: now }]; })]);
  }
  async function list() { const result = await api(`/ai/conversations?business_id=${business.id}`); setConversations(result.items); return result.items; }
  async function open(id: string) { setLoading(true); setError(''); try { const result = await api(`/ai/conversations/${id}`); setConv(id); setMessages(result.messages); } catch (e) { setError((e as Error).message); } finally { setLoading(false); } }
  useEffect(() => { list().then(rows => { if (rows.length) return open(rows[0].id); }).catch(e => setError(e.message)).finally(() => setLoading(false)); }, [business.id]);
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }); }, [messages, uploads, busy]);
  async function send() {
    if (!input.trim() || locked.current) return;
    const text = input.trim(); locked.current = true; setBusy(true); setError(''); setInput('');
    let id = conv;
    try {
      if (!id) { const created = await api('/ai/conversations', 'POST', { business_id: business.id }); id = created.id; setConv(id); setUploads(u => u.map(x => x.conv === '' ? { ...x, conv: created.id } : x)); }
      setMessages(m => [...m, { id: 'sending-user', role: 'user', content: text, blocks: [] }, { id: 'streaming', role: 'assistant', content: '', blocks: [] }]);
      const response = await request(`/ai/conversations/${id}/messages`, { method: 'POST', body: JSON.stringify({ content: text }) });
      const reader = response.body!.getReader(), decoder = new TextDecoder(); let buffer = '';
      while (true) {
        const { value, done } = await reader.read(); buffer += decoder.decode(value, { stream: !done });
        const chunks = buffer.split('\n\n'); buffer = chunks.pop() || '';
        for (const chunk of chunks) for (const line of chunk.split('\n')) if (line.startsWith('data: ')) {
          const event = JSON.parse(line.slice(6));
          if (event.type === 'token') setMessages(m => m.map(x => x.id === 'streaming' ? { ...x, content: x.content + event.text } : x));
          if (event.type === 'block') setMessages(m => m.map(x => x.id === 'streaming' ? { ...x, blocks: [...x.blocks, event.block] } : x));
          if (event.type === 'error') setError(event.message);
        }
        if (done) break;
      }
      await open(id); await list(); refresh();
    } catch (e) { setError((e as Error).message); if (id) await open(id).catch(() => {}); }
    finally { setBusy(false); locked.current = false; textarea.current?.focus(); }
  }
  return <div className={`chat-layout${dragging ? ' dragging' : ''}`} onDragOver={e => { if (e.dataTransfer.types.includes('Files')) { e.preventDefault(); setDragging(true); } }} onDragLeave={e => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setDragging(false); }} onDrop={e => { e.preventDefault(); setDragging(false); if (!busy) attach(e.dataTransfer.files); }}>{dragging && <div className="drop-overlay"><FileSpreadsheet size={40} /><strong>Excel faylını buraxın</strong><span>Məhsulları oxuyub təsdiqinizə göstərəcəyəm</span></div>}<div className="chat-toolbar"><div><span className="assistant-avatar small"><Sparkles size={18} /></span><strong>Biznes köməkçiniz</strong><span className="muted">· {business.name}</span></div><div className="row"><select aria-label="Söhbət tarixçəsi" disabled={busy} value={conv} onChange={e => open(e.target.value)}><option value="" disabled>Yeni söhbət</option>{conversations.map(c => <option key={c.id} value={c.id}>{c.title}</option>)}</select><button className="icon-button" aria-label="Yeni söhbət" disabled={busy} onClick={() => { setConv(''); setMessages([]); setError(''); setUploads(u => u.filter(x => x.conv !== '')); }}><MessageSquarePlus size={20} /></button></div></div>
    <div className="chat-scroll">{loading ? <Spinner /> : shown.length === 0 ? <div className="chat-welcome"><span className="eyebrow">AZ İŞ. DAHA ÇOX AYDINLIQ.</span><div className="assistant-orb"><Sparkles size={33} /></div><h1>Salam! Biznesinizdən<br /><span>danışaq.</span></h1><p>Mən sizin AI inventar köməkçinizəm. Hansı məhsullarınız var?<br />Sadəcə yazın, qalanını birlikdə həll edək.</p><div className="starter-grid">{starters.map(({ icon: Icon, title, text }) => <button key={title} onClick={() => { setInput(text); textarea.current?.focus(); }}><Icon size={21} /><strong>{title}</strong><span>{text}</span><span className="starter-arrow">↗</span></button>)}</div><button className="excel-drop" onClick={() => fileRef.current?.click()}><FileSpreadsheet size={20} /><span><strong>Excel ilə başla</strong>Məhsul siyahınızı (.xlsx, .csv) bura sürükləyin və ya seçin — hamısını bir dəfəyə əlavə edim.</span></button><div className="welcome-foot"><Check size={14} /> Hər dəyişiklikdən əvvəl sizin təsdiqinizi alacağam.</div></div> : <div className="messages">{shown.map(m => <article key={m.id} className={`message ${m.role}`}><span className={m.role === 'assistant' ? 'assistant-avatar' : 'user-avatar'}>{m.role === 'assistant' ? <Sparkles size={17} /> : 'S'}</span><div className="message-body"><small>{m.role === 'assistant' ? 'Hesabatım' : 'Siz'}</small><div className="message-text">{m.content || (busy ? 'Düşünürəm…' : '')}</div>{(m.blocks || []).map((block: Row, index: number) => <Block key={block.action?.id || index} block={block} base={base} imported={refresh} currency={business.currency} disabled={busy} changed={async () => { await open(conv); refresh(); }} />)}</div></article>)}</div>}
    <div ref={bottom} /></div><div className="composer-wrap"><ErrorBox error={error} /><form className="composer" onSubmit={e => { e.preventDefault(); send(); }}><textarea ref={textarea} aria-label="AI köməkçiyə mesaj" placeholder="Məsələn, məndə 500 qapı var…" value={input} maxLength={4000} disabled={busy} onChange={e => setInput(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }} rows={2} /><div className="composer-bottom"><span className="row"><button type="button" className="icon-button attach-button" aria-label="Excel faylı əlavə et" title="Excel faylı əlavə et (.xlsx, .csv)" disabled={busy} onClick={() => fileRef.current?.click()}><Paperclip size={17} /></button><input hidden ref={fileRef} type="file" multiple accept={spreadsheetTypes} onChange={e => { attach(e.target.files); e.target.value = ''; }} /><span><Sparkles size={14} /> {status.mode === 'offline' ? 'Oflayn parser · Məhdud dil dəstəyi' : 'AI köməkçi'}</span></span><button className="send-button" disabled={busy || !input.trim()} aria-label="Göndər"><ArrowUp size={20} /></button></div></form><small className="composer-note">Məlumatları yoxlayın. Əməliyyatlar yalnız təsdiqinizdən sonra yadda saxlanır.</small></div></div>;
}
function Block({ block, base, imported, currency, changed, disabled }: { block: Row; base: string; imported: () => void; currency: string; changed: () => Promise<void>; disabled: boolean }) {
  if (block.type === 'excel_import') return <ExcelImportCard base={base} file={block.file} currency={currency} done={imported} />;
  if (block.type === 'action') return <ActionCard action={block.action} changed={changed} disabled={disabled} />;
  if (block.type === 'notice') return <div className="notice">{block.text}</div>;
  if (block.type === 'inventory_table') return <div className="result-card"><h4>{block.title} <span className="muted">({block.total})</span></h4><div className="table-scroll"><table><thead><tr><th>Məhsul</th><th>Miqdar</th><th>Satış qiyməti</th></tr></thead><tbody>{block.items.map((p: Row) => <tr key={p.id}><td>{p.name}</td><td>{p.quantity} {units[p.unit]}</td><td>{money(p.selling_price, currency)}</td></tr>)}</tbody></table></div></div>;
  if (block.type === 'analytics') return <div className="result-card"><h4>Satış hesabatı</h4>{block.data.period && <small>{block.data.period.from} — {block.data.period.to}</small>}{block.data.totals && <div className="mini-stats"><div><small>Xalis gəlir</small><strong>{money(block.data.totals.revenue, currency)}</strong></div><div><small>Satış sayı</small><strong>{block.data.totals.sales_count}</strong></div></div>}{block.data.items?.map((p: Row) => <div className="list-row" key={p.product_id || p.id}><span>{p.name}</span><strong>{p.units || p.quantity} {units[p.unit]}</strong></div>)}</div>;
  if (block.type === 'inventory_summary') return <div className="result-card mini-stats"><div><small>Məhsul</small><strong>{block.data.total_products}</strong></div><div><small>İnventar dəyəri (məlum alış qiymətləri)</small><strong>{money(block.data.inventory_value, currency)}</strong></div></div>;
  if (block.type === 'reorder') return <div className="result-card"><h4>Yenidən alış tövsiyələri</h4>{block.data.items.map((p: Row) => <div key={p.product_id} className="list-row"><span>{p.name}</span><strong>{p.suggested_quantity} {units[p.unit]}</strong></div>)}</div>;
  return null;
}
function ActionCard({ action, changed, disabled }: { action: Row; changed: () => Promise<void>; disabled: boolean }) {
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [overrides, setOverrides] = useState<Row>({}); const lock = useRef(false);
  async function decide(decision: string) { if (lock.current) return; lock.current = true; setBusy(true); setError(''); try { await api(`/ai/actions/${action.id}/${decision}`, 'POST', decision === 'confirm' ? { overrides } : undefined); await changed(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); lock.current = false; } }
  const labels: Row = { name: 'Məhsul adı', quantity: 'Miqdar', unit_price: 'Vahid qiyməti', selling_price: 'Satış qiyməti', purchase_price: 'Alış qiyməti', customer_name: 'Müştəri', unit: 'Vahid' };
  return <div className={`action-card ${action.status}`}><div className="action-heading"><span><ReceiptText size={17} /> {action.preview.title}</span><span className="status-badge">{action.status === 'pending' ? 'Təsdiq gözləyir' : action.status === 'executed' ? 'Yadda saxlanıldı' : action.status === 'rejected' ? 'Rədd edildi' : 'Uğursuz'}</span></div><h4>{action.preview.summary}</h4><div className="preview-fields">{action.preview.fields?.map((f: Row) => <div key={f.key}><small>{f.label}</small><strong>{f.value == null ? 'Məlum deyil' : units[f.value] || String(f.value)}</strong></div>)}</div>
    {action.status === 'pending' && <><details><summary>Məlumatları düzəlt</summary><div className="form-grid">{(action.preview.editable || []).map((key: string) => <Field key={key} label={labels[key] || key}>{key === 'unit' ? <select value={overrides[key] ?? action.arguments[key] ?? 'piece'} onChange={e => setOverrides(v => ({ ...v, [key]: e.target.value }))}>{Object.entries(units).map(([v, label]) => <option key={v} value={v}>{label}</option>)}</select> : <input type={['quantity', 'unit_price', 'selling_price', 'purchase_price'].includes(key) ? 'number' : 'text'} min="0" step="0.001" value={overrides[key] ?? action.arguments[key] ?? ''} onChange={e => setOverrides(v => ({ ...v, [key]: e.target.value === '' ? null : e.target.value }))} />}</Field>)}</div></details>{action.preview.warnings?.map((w: string) => <p className="notice" key={w}>{w}</p>)}<ErrorBox error={error} /><div className="action-buttons"><button className="primary" disabled={busy || disabled} onClick={() => decide('confirm')}><Check size={16} />{busy ? 'Gözləyin…' : 'Təsdiqlə'}</button><button className="secondary" disabled={busy || disabled} onClick={() => decide('reject')}><X size={16} />Rədd et</button></div></>}
    {action.status === 'executed' && <p className="success-line"><Check size={16} />{action.result?.sale ? `${action.result.sale.number} · ${action.result.sale.total} — satış qeydə alındı` : 'Əməliyyat uğurla tamamlandı'}</p>}
  </div>;
}
