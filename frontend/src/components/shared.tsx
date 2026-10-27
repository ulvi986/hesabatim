'use client';
import { useEffect, useRef } from 'react';
import { X, LoaderCircle, PackageOpen } from 'lucide-react';
export function Spinner() { return <div className="loading" role="status"><LoaderCircle className="spin" size={22} /> Yüklənir…</div>; }
export function Empty({ title, children }: { title: string; children?: React.ReactNode }) { return <div className="empty"><PackageOpen size={32} /><h3>{title}</h3><div>{children}</div></div>; }
export function Modal({ title, children, close, wide }: { title: string; children: React.ReactNode; close: () => void; wide?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => { ref.current?.showModal(); }, []);
  return <dialog ref={ref} className={wide ? 'wide' : undefined} onCancel={close} onClick={e => { if (e.target === ref.current) close(); }}><header><h2>{title}</h2><button className="icon-button" onClick={close} aria-label="Bağla"><X size={20} /></button></header>{children}</dialog>;
}
export function Field({ label, children }: { label: string; children: React.ReactNode }) { return <label className="field"><span>{label}</span>{children}</label>; }
export function ErrorBox({ error }: { error: string }) { return error ? <div role="alert" className="error-box">{error}</div> : null; }
