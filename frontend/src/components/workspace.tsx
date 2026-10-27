'use client';
import { useEffect, useRef, useState } from 'react';
import { Bell, Sparkles, LayoutDashboard, Boxes, ReceiptText, ChartNoAxesCombined, Settings, Users, Sun, Moon, LogOut, ArrowRight, PanelLeftClose, PanelLeft, ShieldCheck } from 'lucide-react';
import { api, ApiError, Row } from '@/lib/api';
import { ErrorBox, Field, Spinner } from './shared';
import Chat from './chat';
import Dashboard from './dashboard';
import Inventory from './inventory';
import Sales from './sales';
import Customers from './customers';
import { applyTheme, themeOf } from '@/lib/theme';
import Preferences from './settings';
import Notifications from './notifications';
const navigation = [{ id: 'assistant', name: 'AI köməkçi', icon: Sparkles }, { id: 'dashboard', name: 'İcmal', icon: LayoutDashboard }, { id: 'inventory', name: 'İnventar', icon: Boxes }, { id: 'sales', name: 'Satışlar', icon: ReceiptText }, { id: 'customers', name: 'Müştərilər', icon: Users }, { id: 'notifications', name: 'Bildirişlər', icon: Bell }, { id: 'analytics', name: 'Analitika', icon: ChartNoAxesCombined }, { id: 'settings', name: 'Tənzimləmələr', icon: Settings }];
export default function Workspace() {
  const [me, setMe] = useState<Row | null>(null), [loading, setLoading] = useState(true), [tab, setTab] = useState('assistant');
  const [revision, setRevision] = useState(0), [dark, setDark] = useState(false), [mobile, setMobile] = useState(false), [error, setError] = useState('');
  const [status, setStatus] = useState<Row>({ mode: 'offline' });
  const [alerts, setAlerts] = useState<Row | null>(null), lastUnread = useRef<Set<string> | null>(null);
  const alertsBase = me ? `/businesses/${me.memberships[0].business.id}` : null;
  // Bell badge: poll alerts every minute; when a new unread one appears, also raise a browser notification if allowed.
  useEffect(() => {
    if (!alertsBase) return;
    let active = true;
    const load = () => api(`${alertsBase}/notifications`).then((d: Row) => {
      if (!active) return;
      const unread = new Set<string>(d.items.filter((n: Row) => n.unread).map((n: Row) => n.key));
      const fresh = lastUnread.current ? d.items.filter((n: Row) => n.unread && !lastUnread.current!.has(n.key)) : [];
      lastUnread.current = unread; setAlerts(d);
      if (fresh.length && 'Notification' in window && Notification.permission === 'granted') new Notification(fresh[0].title, { body: fresh.length > 1 ? `${fresh[0].body} (+${fresh.length - 1} başqa)` : fresh[0].body, tag: 'hesabatim' });
    }).catch(() => {});
    load(); const timer = setInterval(load, 60000);
    return () => { active = false; clearInterval(timer); };
  }, [alertsBase, revision]);
  useEffect(() => { api('/me').then(setMe).catch(e => { if (!(e instanceof ApiError) || e.status !== 401) setError(e.message); }).finally(() => setLoading(false)); api('/ai/status').then(setStatus).catch(() => {}); setDark(localStorage.getItem('hesabatim-theme') === 'dark'); }, []);
  useEffect(() => { document.documentElement.dataset.theme = dark ? 'dark' : 'light'; localStorage.setItem('hesabatim-theme', dark ? 'dark' : 'light'); }, [dark]);
  useEffect(() => { const change = () => { const hash = location.hash.slice(1); if (navigation.some(n => n.id === hash)) setTab(hash); }; change(); window.addEventListener('hashchange', change); return () => window.removeEventListener('hashchange', change); }, []);
  const theme = me ? themeOf(me.memberships[0].business) : null;
  useEffect(() => { if (theme) applyTheme(theme); }, [theme?.primary, theme?.accent, theme?.sidebar]); // eslint-disable-line react-hooks/exhaustive-deps
  const navigate = (id: string) => { location.hash = id; setTab(id); setMobile(false); };
  if (loading) return <Spinner />;
  if (!me) return <Auth onSuccess={setMe} connectionError={error} />;
  const business = me.memberships[0].business;
  const base = `/businesses/${business.id}`;
  const refresh = () => setRevision(v => v + 1);
  const unread: number = alerts?.unread ?? 0;
  return <div className="workspace">
    <aside className={`sidebar ${mobile ? 'open' : ''}`}>
      <a href="#assistant" className="brand"><span className="brand-icon">h<span>•</span></span>hesabatım<span className="brand-dot">.</span></a>
      <div className="business-switch">{business.logo ? <img className="business-avatar" src={business.logo} alt={`${business.name} loqosu`} /> : <span className="business-avatar">{business.name.slice(0, 1)}</span>}<div><strong>{business.name}</strong><small>Biznes hesabı · {business.currency}</small></div><ShieldCheck size={17} /></div>
      <p className="nav-caption">İŞ SAHƏSİ</p>
      <nav aria-label="Əsas menyu">{navigation.map(({ id, name, icon: Icon }) => <button key={id} onClick={() => navigate(id)} className={tab === id ? 'active' : ''} aria-current={tab === id ? 'page' : undefined}><Icon size={19} /><span>{name}</span>{id === 'assistant' && <span className="tiny-tag">AI</span>}{id === 'notifications' && unread > 0 && <span className="nav-badge">{unread > 99 ? "99+" : unread}</span>}</button>)}</nav>
      <div className="sidebar-bottom"><div className="assistant-note"><Sparkles size={20} /><strong>Biznesinizdən danışın.</strong><p>Rəqəmləri hesabatlara çevirməyi bizə buraxın.</p><button onClick={() => navigate('assistant')}>Söhbətə başla <ArrowRight size={15} /></button></div>
      <div className="profile"><span className="user-avatar">{(me.full_name || me.email).slice(0, 1).toUpperCase()}</span><div><strong>{me.full_name || 'Hesabım'}</strong><small>{me.email}</small></div><button className="icon-button" title="Bütün cihazlardan çıxış" aria-label="Çıxış" onClick={async () => { try { await api('/auth/logout-all', 'POST'); setMe(null); } catch (e) { setError((e as Error).message); } }}><LogOut size={17} /></button></div></div>
    </aside>
    <div className="main-shell"><header className="topbar"><div className="row"><button className="icon-button mobile-toggle" aria-label="Menyunu aç" onClick={() => setMobile(!mobile)}>{mobile ? <PanelLeftClose size={20} /> : <PanelLeft size={20} />}</button><span>İş sahəsi</span><span className="breadcrumb">/</span><strong>{navigation.find(n => n.id === tab)?.name}</strong></div><div className="row"><button className="icon-button bell-button" aria-label={`Bildirişlər${alerts?.unread ? ` (${alerts.unread} oxunmamış)` : ''}`} onClick={() => navigate('notifications')}><Bell size={19} />{unread > 0 && <span className="nav-badge">{unread > 99 ? "99+" : unread}</span>}</button><span className="mode-pill"><i />{status.mode === 'offline' ? 'İnkişaf rejimi · Oflayn parser' : `AI · ${status.model}`}</span><button className="icon-button" aria-label={dark ? 'İşıqlı mövzu' : 'Qaranlıq mövzu'} onClick={() => setDark(!dark)}>{dark ? <Sun size={19} /> : <Moon size={19} />}</button></div></header>
      <main className={tab === 'assistant' ? 'content chat-content' : 'content'}><ErrorBox error={error} />
        {tab === 'assistant' && <Chat business={business} status={status} refresh={refresh} />}
        {(tab === 'dashboard' || tab === 'analytics') && <Dashboard key={`${tab}-${revision}`} base={base} currency={business.currency} analytics={tab === 'analytics'} navigate={navigate} />}
        {tab === 'inventory' && <Inventory base={base} currency={business.currency} refresh={refresh} />}
        {tab === 'sales' && <Sales base={base} business={business} refresh={refresh} />}
        {tab === 'customers' && <Customers base={base} business={business} refresh={refresh} />}
        {tab === 'notifications' && <Notifications base={base} data={alerts} changed={d => { lastUnread.current = new Set(d.items.filter((n: Row) => n.unread).map((n: Row) => n.key)); setAlerts(d); }} navigate={navigate} />}
        {tab === 'settings' && <Preferences me={me} business={business} status={status} update={setMe} />}
      </main></div>
  </div>;
}
function Auth({ onSuccess, connectionError }: { onSuccess: (user: Row) => void; connectionError: string }) {
  const [register, setRegister] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState(connectionError);
  async function submit(e: React.FormEvent<HTMLFormElement>) { e.preventDefault(); setBusy(true); setError(''); const fields = Object.fromEntries(new FormData(e.currentTarget)); try { const result = await api(`/auth/${register ? 'register' : 'login'}`, 'POST', fields); onSuccess(result.user); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } }
  return <div className="auth-page"><section className="auth-story"><div className="brand"><span className="brand-icon">h<span>•</span></span>hesabatım.</div><div><span className="eyebrow">BİZNESİNİZİN YENİ İŞ YOLDAŞI</span><h1>İnventarınızı<br />söhbət qədər<br /><em>sadələşdirin.</em></h1><p>Məhsullarınız, satışlarınız və hesabatlarınız — bir ağıllı iş sahəsində.</p><div className="demo-chat"><span>SİZ</span><p>Məndə 500 qapı var.</p><hr /><span><Sparkles size={14} /> HESABATIM</span><p>500 ədəd qapı. İnventarınıza əlavə edim?</p><small>Nümunə söhbət · Məlumat yalnız təsdiqdən sonra saxlanır</small></div></div><small>Sadə idarəetmə. Aydın rəqəmlər.</small></section><section className="auth-form"><div><span className="eyebrow">HESABATIM-A XOŞ GƏLMİSİNİZ</span><h2>{register ? 'Biznesiniz üçün yeni başlanğıc' : 'Yenidən xoş gəlmisiniz'}</h2><p className="muted">{register ? 'Hesabınızı yaradın, ilk məhsulunuzu söhbətlə əlavə edin.' : 'Biznesinizin qaldığı yerdən davam edin.'}</p><ErrorBox error={error} /><form onSubmit={submit}>
    {register && <><Field label="Adınız"><input name="full_name" autoComplete="name" required maxLength={200} /></Field><Field label="Biznesin adı"><input name="business_name" placeholder="Məsələn, Bakı Mebel" minLength={2} maxLength={200} required /></Field></>}
    <Field label="E-poçt"><input name="email" type="email" autoComplete="email" required placeholder="ad@sirket.az" /></Field><Field label="Şifrə"><input name="password" type="password" minLength={register ? 8 : 1} maxLength={128} autoComplete={register ? 'new-password' : 'current-password'} required placeholder="Ən azı 8 simvol" /></Field><button disabled={busy} className="primary wide">{busy ? 'Gözləyin…' : register ? 'Hesab yarat' : 'Daxil ol'}<ArrowRight size={18} /></button></form><p className="auth-switch">{register ? 'Artıq hesabınız var?' : 'Hələ hesabınız yoxdur?'} <button className="text-button" onClick={() => { setRegister(!register); setError(''); }}>{register ? 'Daxil olun' : 'Qeydiyyatdan keçin'}</button></p><div className="trust-line"><ShieldCheck size={16} /> Biznes məlumatlarınız yalnız sizə məxsusdur.</div></div></section></div>;
}
