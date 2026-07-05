import "./globals.css";
import Nav from "./Nav";

export const metadata = {
  title: "NST Wiki — 국가전략기술 지식베이스",
  description: "국가전략기술(NEXT) 정책 지식을 질의·탐색하는 LLM Wiki",
};

export default function RootLayout({ children }) {
  return (
    <html lang="ko">
      <body>
        <header className="site-header">
          <div className="inner">
            <a href="/" className="brand">
              <span className="mark">NST</span>
              <span className="name">
                국가전략기술 지식베이스
                <small>NEXT · 정책 인텔리전스</small>
              </span>
            </a>
            <Nav />
          </div>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
