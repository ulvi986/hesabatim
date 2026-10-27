import type { Metadata } from 'next';
import './globals.css';
export const metadata: Metadata = { title: 'Hesabatım — Ağıllı biznes köməkçiniz', description: 'İnventar, satış və biznes analitikası. Sadəcə söhbət edin.' };
export default function Layout({ children }: { children: React.ReactNode }) {
  return <html lang="az"><body>{children}</body></html>;
}
