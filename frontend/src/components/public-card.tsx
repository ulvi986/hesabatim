'use client';
import { use, useEffect, useState } from 'react';
import { api, Row } from '@/lib/api';
import { applyTheme, themeOf } from '@/lib/theme';
import { LoyaltyCard } from './loyalty';
import { ErrorBox, Spinner } from './shared';
// The customer keeps this page on their phone; the staff scans its QR code. It refreshes itself so a new stamp shows up.
export default function PublicCard({ params }: { params: Promise<{ token: string }> }) {
  const { token } = use(params);
  const [card, setCard] = useState<Row | null>(null), [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    const load = () => api(`/public/cards/${encodeURIComponent(token)}`).then(c => { if (active) { setCard(c); setError(''); applyTheme(themeOf(c.business)); document.title = `${c.business.name} · Sadiqlik kartı`; } }).catch(e => active && setError(e.message));
    load(); const timer = setInterval(() => { if (document.visibilityState === 'visible') load(); }, 5000);
    return () => { active = false; clearInterval(timer); };
  }, [token]);
  return <main className="public-card">{card ? <><LoyaltyCard card={card} name={card.name} business={card.business} />
    <p className="public-hint">{card.reward_ready ? `🎉 Təbriklər! ${card.reward} sizi gözləyir — bu kodu kassada göstərin.` : 'Hər alışda bu QR kodu kassada göstərin.'}</p>
    <div className="public-stats"><div><strong>{card.total_stamps}</strong><small>cəmi möhür</small></div><div><strong>{card.rewards}</strong><small>alınmış hədiyyə</small></div></div>
    <p className="muted public-foot">Səhifəni telefonunuzun ana ekranına əlavə edin ki, kart həmişə əlinizin altında olsun.</p></> : error ? <ErrorBox error={error} /> : <Spinner />}</main>;
}
