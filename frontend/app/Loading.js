// 로딩 표시 — Suspense fallback과 데이터 대기 상태 공용
export default function Loading({ label = "불러오는 중…" }) {
  return (
    <div className="empty-state">
      <span className="spinner" /> {label}
    </div>
  );
}
