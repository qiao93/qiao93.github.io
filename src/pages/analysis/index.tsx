import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Helmet } from 'react-helmet-async';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import Layout from '@/components/Layout';
import { useTheme } from '@/hooks/useTheme';

interface ReportSummary {
  date: string;
  filename: string;
  distance: number;
  duration: string;
}

/** Parse one bullet line from index.md of the form:
 *      `- [2026-05-04](2026-05-04_08-02km.md) — 8.02km / 48:55`
 */
function parseRow(line: string): ReportSummary | null {
  const m = line.match(
    /^\s*-\s*\[(\d{4}-\d{2}-\d{2})\]\(([^)]+)\)\s+—\s+([\d.]+)km\s+\/\s+(.+?)\s*$/
  );
  if (!m) return null;
  return {
    date: m[1],
    filename: m[2],
    distance: parseFloat(m[3]),
    duration: m[4],
  };
}

const AnalysisListPage = () => {
  const { theme } = useTheme();
  const [reports, setReports] = useState<ReportSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [raw, setRaw] = useState<string>('');

  useEffect(() => {
    const html = document.documentElement;
    html.setAttribute('data-theme', theme);
  }, [theme]);

  useEffect(() => {
    let aborted = false;
    (async () => {
      try {
        const r = await fetch(`${import.meta.env.BASE_URL}analyses/index.md`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const text = await r.text();
        if (aborted) return;
        setRaw(text);
        // JS: `splitlines` is Python-only. Use regex split on \r?\n.
        const rows = text
          .split(/\r?\n/)
          .map(parseRow)
          .filter((x): x is ReportSummary => x !== null);
        setReports(rows);
      } catch (e) {
        if (!aborted) setError(String(e));
      }
    })();
    return () => {
      aborted = true;
    };
  }, []);

  return (
    <Layout>
      <Helmet>
        <html lang="zh" data-theme={theme} />
      </Helmet>
      <div className="mx-auto w-full max-w-3xl px-4 py-8">
        <h1 className="mb-2 inline-block border-b-2 border-red-400 pb-2 text-4xl font-extrabold italic text-zinc-900 dark:border-red-500/70 dark:text-zinc-50">
          跑步分析报告
        </h1>
        <p className="mb-6 text-sm text-zinc-500 dark:text-zinc-400">
          按日期倒序。每次跑步后由 <code>run_page/analysis</code> 流水线生成。
        </p>

        {error && (
          <div className="mb-6 rounded-lg border border-red-300 bg-red-50/60 p-4 text-sm text-red-800 dark:border-red-800/60 dark:bg-red-950/30 dark:text-red-200">
            ⚠️ 加载分析报告失败：{error}
            <br />
            提示：先跑 <code>pnpm analyze</code> 生成报告。
          </div>
        )}

        {reports.length === 0 && !error && (
          <div className="rounded-lg border border-zinc-200 p-4 text-sm text-zinc-500 dark:border-zinc-700 dark:text-zinc-400">
            还没有任何分析报告。跑一次 <code>pnpm analyze</code> 即可生成。
          </div>
        )}

        {reports.length > 0 && (
          <div className="overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-700">
            <table className="w-full border-collapse text-left text-sm">
              <thead className="bg-zinc-50 text-zinc-700 dark:bg-zinc-800/50 dark:text-zinc-200">
                <tr>
                  <th className="border-b border-zinc-200 px-3 py-2 font-semibold dark:border-zinc-700">日期</th>
                  <th className="border-b border-zinc-200 px-3 py-2 font-semibold dark:border-zinc-700">距离</th>
                  <th className="border-b border-zinc-200 px-3 py-2 font-semibold dark:border-zinc-700">时长</th>
                  <th className="border-b border-zinc-200 px-3 py-2 font-semibold dark:border-zinc-700"></th>
                </tr>
              </thead>
              <tbody>
                {reports.map((r) => (
                  <tr
                    key={r.filename}
                    className="border-t border-zinc-100 transition-colors hover:bg-red-50/40 even:bg-zinc-50/40 dark:border-zinc-800 dark:hover:bg-red-950/20 dark:even:bg-zinc-800/20"
                  >
                    <td className="px-3 py-3 text-zinc-800 dark:text-zinc-200">{r.date}</td>
                    <td className="px-3 py-3 font-mono text-zinc-700 dark:text-zinc-300">{r.distance.toFixed(2)} km</td>
                    <td className="px-3 py-3 font-mono text-zinc-700 dark:text-zinc-300">{r.duration}</td>
                    <td className="px-3 py-3">
                      <Link
                        to={`/analysis/${r.filename.replace(/\.md$/, '')}`}
                        className="inline-flex items-center gap-1 rounded-full border border-red-300 px-3 py-1 text-sm text-red-600 transition-colors hover:bg-red-50 dark:border-red-700/60 dark:text-red-400 dark:hover:bg-red-950/30"
                      >
                        查看报告 →
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {import.meta.env.DEV && raw && (
          <details className="mt-10 text-xs text-gray-500">
            <summary>index.md raw（dev only）</summary>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{raw}</ReactMarkdown>
          </details>
        )}
      </div>
    </Layout>
  );
};

export default AnalysisListPage;
