"use client";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// 질의 응답·위키 문서 공용 마크다운 렌더. 스타일은 globals.css의 .prose.
export default function Markdown({ children }) {
  return (
    <div className="prose">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>
    </div>
  );
}
