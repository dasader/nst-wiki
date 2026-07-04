import "./globals.css";

export const metadata = { title: "NST Wiki" };

export default function RootLayout({ children }) {
  return (
    <html lang="ko">
      <body>
        <nav>
          <a href="/">질의</a>
          <a href="/wiki">위키</a>
          <a href="/data">데이터</a>
          <a href="http://localhost:8000/" target="_blank">승인 대시보드</a>
        </nav>
        <main>{children}</main>
      </body>
    </html>
  );
}
