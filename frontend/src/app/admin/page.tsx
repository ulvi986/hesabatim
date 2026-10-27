import type { Metadata } from 'next';
import Admin from '@/components/admin';
export const metadata: Metadata = { title: 'Hesabatım — Admin', robots: { index: false, follow: false } };
export default function Page() { return <Admin />; }
