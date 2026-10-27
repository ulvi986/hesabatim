'use client';
import { useEffect, useId, useRef } from 'react';
import { Gift, Stamp } from 'lucide-react';
import { QRCodeSVG } from 'qrcode.react';
import { Row } from '@/lib/api';
export const cardUrl = (token: string) => `${location.origin}/kart/${token}`;
export function StampGrid({ card }: { card: Row }) {
  return <div className="stamps" aria-label={`${card.stamps} / ${card.stamps_needed} möhür`}>{Array.from({ length: card.stamps_needed }, (_, i) => <span key={i} className={`stamp ${i < card.stamps ? 'on' : ''}`}>{i < card.stamps && <Stamp size={15} />}</span>)}<span className={`stamp free ${card.reward_ready ? 'ready' : ''}`}><Gift size={15} /></span></div>;
}
// The card colour (theme.card, else the brand colour) is also what the customer's phone shows on the card site.
export function LoyaltyCard({ card, name, business, qr = true, color }: { card: Row; name: string; business: Row; qr?: boolean; color?: string }) {
  const cardColor = color || business.theme?.card;
  return <div className="loyalty-card" style={cardColor ? { '--card': cardColor } as React.CSSProperties : undefined}><div className="loyalty-top"><div className="row">{business.logo ? <img src={business.logo} alt="" className="loyalty-logo" /> : <span className="loyalty-logo">{business.name.slice(0, 1)}</span>}<div><strong>{business.name}</strong><small>Sadiqlik kartı</small></div></div><span className="loyalty-count">{card.stamps}/{card.stamps_needed}</span></div>
    <StampGrid card={card} /><div className="loyalty-bottom"><div><small>Kart sahibi</small><strong>{name}</strong><small>{card.reward_ready ? `🎁 ${card.reward} hazırdır!` : `${card.reward} üçün ${card.stamps_needed - card.stamps} möhür qalıb`}</small></div>{qr && <span className="loyalty-qr"><QRCodeSVG value={cardUrl(card.token)} size={84} marginSize={1} /></span>}</div></div>;
}
// Opens the rear camera while mounted; html5-qrcode needs HTTPS or localhost.
export function QrScanner({ onScan, onError }: { onScan: (text: string) => void; onError: (message: string) => void }) {
  const id = `reader-${useId().replace(/:/g, '')}`, scan = useRef(onScan), fail = useRef(onError);
  useEffect(() => { scan.current = onScan; fail.current = onError; });
  useEffect(() => {
    let cancelled = false;
    // Loaded on demand: the library touches browser globals and is only needed while scanning.
    const started = import('html5-qrcode').then(async ({ Html5Qrcode }) => {
      const scanner = new Html5Qrcode(id, { verbose: false });
      await scanner.start({ facingMode: 'environment' }, { fps: 10, qrbox: { width: 230, height: 230 } }, text => scan.current(text), () => {});
      return scanner;
    }).catch(() => { if (!cancelled) fail.current('Kamera açılmadı. Brauzerə kamera icazəsi verin (sayt HTTPS və ya localhost olmalıdır).'); return null; });
    return () => { cancelled = true; started.then(scanner => scanner?.isScanning ? scanner.stop().then(() => scanner.clear()) : undefined).catch(() => {}); };
  }, [id]);
  return <div id={id} className="qr-reader" />;
}
