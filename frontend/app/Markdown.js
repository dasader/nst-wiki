"use client";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// 질의 응답·위키 문서 공용 마크다운 렌더. 스타일은 globals.css의 .prose.
// 표는 가로 스크롤 컨테이너로 감싸 본문 레이아웃이 밀리지 않게 한다.
const components = {
  table: ({ node, ...props }) => (
    <div className="table-scroll">
      <table {...props} />
    </div>
  ),
  // linkifyWiki가 실존하지 않는 대상을 #dead-page/#dead-data로 표시 → 클릭 불가 표식으로 렌더
  a: ({ node, href, children, ...props }) => {
    if (href === "#dead-page" || href === "#dead-data")
      return (
        <span className="dead-link">
          {children}
          <span className="dead-tag">{href === "#dead-data" ? "없는 데이터" : "없는 문서"}</span>
        </span>
      );
    return <a href={href} {...props}>{children}</a>;
  },
};

export default function Markdown({ children }) {
  return (
    <div className="prose">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {children}
      </ReactMarkdown>
    </div>
  );
}
