"use client";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

const LINKS = [
  { href: "/", label: "질문하기" },
  { href: "/wiki", label: "위키" },
  { href: "/data", label: "데이터" },
];

// 관리 대시보드는 api 오리진(:8000)이 직접 서빙 — 공개 프록시로 노출하지 않는다.
// 배포시 NEXT_PUBLIC_ADMIN_URL로 덮어쓰고, 없으면 현재 호스트에서 유추.
export default function Nav() {
  const path = usePathname();
  const active = (href) => (href === "/" ? path === "/" : path.startsWith(href));
  const [adminUrl, setAdminUrl] = useState(process.env.NEXT_PUBLIC_ADMIN_URL || "#");
  useEffect(() => {
    if (!process.env.NEXT_PUBLIC_ADMIN_URL) {
      setAdminUrl(`${location.protocol}//${location.hostname}:8000/`);
    }
  }, []);
  return (
    <nav className="main">
      {LINKS.map((l) => (
        <a key={l.href} href={l.href} className={active(l.href) ? "active" : ""}>
          {l.label}
        </a>
      ))}
      <a href={adminUrl} target="_blank" rel="noreferrer" className="ext">
        승인 대시보드
      </a>
    </nav>
  );
}
