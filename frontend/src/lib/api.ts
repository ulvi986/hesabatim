export const API = '/api/v1';
export class ApiError extends Error { constructor(message: string, public status: number) { super(message); } }
export async function request(path: string, init: RequestInit = {}) {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  const response = await fetch(API + path, { ...init, headers, credentials: 'include', cache: 'no-store' });
  if (!response.ok) { const data = await response.json().catch(() => ({})); throw new ApiError(data.error?.message || 'Xidmətə qoşulmaq mümkün olmadı', response.status); }
  return response;
}
export async function api(path: string, method = 'GET', data?: unknown, key?: string) {
  const response = await request(path, { method, body: data === undefined ? undefined : JSON.stringify(data), headers: key ? { 'Idempotency-Key': key } : {} });
  return response.status === 204 ? null : response.json();
}
export async function download(path: string, filename: string) {
  const response = await request(path); const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = filename; anchor.click(); URL.revokeObjectURL(url);
}
export const units: Record<string, string> = { piece: 'ədəd', m2: 'm²', m: 'metr', m3: 'm³', kg: 'kq', ton: 'ton', l: 'litr', pack: 'paket', set: 'dəst' };
export const money = (value: string | number | null | undefined, currency = 'AZN') => value == null ? 'Məlum deyil' : new Intl.NumberFormat('az-AZ', { style: 'currency', currency, maximumFractionDigits: 2 }).format(Number(value));
// True for strings produced by money(): digits plus a currency sign or code.
export const isMoney = (value: unknown) => typeof value === 'string' && /\d/.test(value) && /[₼$€₺₽£]|[A-Z]{3}/.test(value);
export const date = (value: string) => new Intl.DateTimeFormat('az-AZ', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'Asia/Baku' }).format(new Date(value));
// Decimal amounts remain strings at the boundary; numbers are used only for visual chart coordinates.
export type Row = Record<string, any>;
