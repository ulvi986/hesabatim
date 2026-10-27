import { Suspense } from 'react';
import type { Metadata } from 'next';
import PublicCard from '@/components/public-card';
export const metadata: Metadata = { title: 'Sadiqlik kartım', description: 'Möhürlərinizi toplayın, hədiyyə qazanın.' };
export default function Page({ params }: { params: Promise<{ token: string }> }) {
  return <Suspense fallback={null}><PublicCard params={params} /></Suspense>;
}
