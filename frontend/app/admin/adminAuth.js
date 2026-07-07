// 관리자 키 저장·검증 단일 창구. 콘솔 게이트(layout)와 각 하위 페이지가 공유한다.
const STORAGE = "adminkey";

export const getKey = () =>
  typeof localStorage !== "undefined" ? localStorage.getItem(STORAGE) || "" : "";
export const setKey = (k) => localStorage.setItem(STORAGE, k);
export const clearKey = () => localStorage.removeItem(STORAGE);

export async function verifyKey(key) {
  try {
    return (await fetch("/api/v1/admin/verify", { headers: { "X-Admin-Key": key } })).ok;
  } catch {
    return false;
  }
}

// 관리 API 호출 — 저장된 키를 X-Admin-Key로 자동 주입
export function adminFetch(url, opts = {}) {
  return fetch(url, { ...opts, headers: { ...opts.headers, "X-Admin-Key": getKey() } });
}
