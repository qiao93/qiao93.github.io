import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { Helmet } from 'react-helmet-async';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import Layout from '@/components/Layout';
import { useTheme } from '@/hooks/useTheme';

/**
 * 小红书 / RED 风格的 markdown 自定义渲染器。
 *
 * 设计原则:
 *  - 浅色用珊瑚红 accent;深色用偏粉的 coral 避免刺眼
 *  - 不用冷的蓝/灰,改用暖的 amber/zinc 系配合站点的 lime 品牌色
 *  - 表格行 zebra 浅色用 zinc-50/30;深色用 zinc-800/20 — 微差,不抢戏
 *  - blockquote 暖色底 (amber 浅 / amber-950 深)
 */
const components: Components = {
  h1: ({ children }) => (
    <h1 className="mb-3 mt-2 inline-block border-b-2 border-red-400 pb-2 text-3xl font-extrabold tracking-tight text-gray-900 dark:border-red-500/70 dark:text-gray-50">
      {children}
    </h1>
  ),
  h2: ({ children }) => (
    <h2 className="mb-3 mt-10 flex items-center gap-2 border-b border-zinc-200 pb-1 text-2xl font-bold text-zinc-900 dark:border-zinc-700 dark:text-zinc-100">
      {children}
    </h2>
  ),
  h3: ({ children }) => (
    <h3 className="mb-2 mt-6 text-lg font-bold text-zinc-900 dark:text-zinc-100">
      {children}
    </h3>
  ),
  blockquote: ({ children }) => (
    <blockquote className="my-4 rounded-r-lg border-l-4 border-amber-400 bg-amber-50/70 px-4 py-2.5 text-amber-900 dark:border-amber-500/70 dark:bg-amber-950/30 dark:text-amber-100">
      {children}
    </blockquote>
  ),
  table: ({ children }) => (
    <div className="my-4 overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-700">
      <table className="w-full border-collapse text-sm">{children}</table>
    </div>
  ),
  thead: ({ children }) => (
    <thead className="bg-zinc-50 dark:bg-zinc-800/50">
      {children}
    </thead>
  ),
  th: ({ children }) => (
    <th className="border-b-2 border-zinc-200 px-3 py-2 text-left font-semibold text-zinc-800 dark:border-zinc-700 dark:text-zinc-200">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border-t border-zinc-100 px-3 py-2 text-zinc-700 dark:border-zinc-800 dark:text-zinc-300">
      {children}
    </td>
  ),
  tr: ({ children }) => (
    <tr className="even:bg-zinc-50/50 dark:even:bg-zinc-800/20 transition-colors hover:bg-zinc-50 dark:hover:bg-zinc-800/40">
      {children}
    </tr>
  ),
  hr: () => (
    <hr className="my-6 border-zinc-200 dark:border-zinc-700" />
  ),
  code: ({ children }) => (
    <code className="rounded bg-zinc-100 px-1.5 py-0.5 font-mono text-[0.9em] text-pink-600 dark:bg-zinc-800 dark:text-pink-300">
      {children}
    </code>
  ),
  strong: ({ children }) => (
    <strong className="font-bold text-zinc-900 dark:text-zinc-50">
      {children}
    </strong>
  ),
  em: ({ children }) => (
    <em className="italic text-zinc-700 dark:text-zinc-300">{children}</em>
  ),
  ol: ({ children }) => (
    <ol className="my-4 list-decimal space-y-2 pl-6 text-zinc-700 dark:text-zinc-300">
      {children}
    </ol>
  ),
  ul: ({ children }) => (
    <ul className="my-4 list-disc space-y-1 pl-6 text-zinc-700 dark:text-zinc-300">
      {children}
    </ul>
  ),
  li: ({ children }) => <li className="leading-relaxed">{children}</li>,
  p: ({ children }) => <p className="my-2.5 leading-relaxed">{children}</p>,
  a: ({ href, children }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="text-blue-600 underline decoration-blue-300 underline-offset-2 hover:text-blue-800 dark:text-blue-400 dark:decoration-blue-500/50 dark:hover:text-blue-300"
    >
      {children}
    </a>
  ),
};

const AnalysisDetailPage = () => {
  const { theme } = useTheme();
  const { slug } = useParams<{ slug: string }>();
  const [md, setMd] = useState<string | null>(null);
  const [narrativeMd, setNarrativeMd] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const html = document.documentElement;
    html.setAttribute('data-theme', theme);
  }, [theme]);

  useEffect(() => {
    if (!slug) return;
    let aborted = false;
    (async () => {
      try {
        // Fetch facts report + optional narrative in parallel
        const [factsR, narrativeR] = await Promise.all([
          fetch(`${import.meta.env.BASE_URL}analyses/${slug}.md`),
          fetch(`${import.meta.env.BASE_URL}analyses/${slug}_narrative.md`),
        ]);
        if (!factsR.ok) throw new Error(`HTTP ${factsR.status}`);
        const factsText = await factsR.text();
        if (aborted) return;
        setMd(factsText);
        if (narrativeR.ok) {
          const nText = await narrativeR.text();
          // Strip YAML frontmatter (---\n...meta...\n---\n\n)
          const stripped = nText.replace(/^---\n[\s\S]*?\n---\n*/, '').trim();
          if (stripped) setNarrativeMd(stripped);
        }
      } catch (e) {
        if (!aborted) setError(String(e));
      }
    })();
    return () => {
      aborted = true;
    };
  }, [slug]);

  if (!slug) {
    return (
      <Layout>
        <Helmet>
          <html lang="zh" data-theme={theme} />
        </Helmet>
        <div className="mx-auto w-full max-w-3xl px-4 py-8">
          <p className="text-sm text-gray-500">missing slug in URL</p>
        </div>
      </Layout>
    );
  }

  return (
    <Layout>
      <Helmet>
        <html lang="zh" data-theme={theme} />
      </Helmet>
      <div className="mx-auto w-full max-w-3xl px-4 py-8">
        <p className="mb-6 text-sm">
          <Link
            to="/analysis"
            className="inline-flex items-center gap-1 text-blue-600 underline hover:text-blue-800 dark:text-blue-400"
          >
            ← 返回报告列表
          </Link>
        </p>

        {error && (
          <div className="rounded border border-red-300 bg-red-50 p-4 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">
            ⚠️ 加载报告失败：{error}
            <br />
            <span className="text-xs">
              slug: <code>{slug}</code>
            </span>
          </div>
        )}

        {md === null && !error && (
          <div className="text-sm text-gray-500">加载中…</div>
        )}

        {md && (
          <article className="text-base leading-relaxed text-zinc-800 dark:text-zinc-200">
            {narrativeMd && (
              <section className="mb-10 rounded-lg border border-amber-300 bg-amber-50/40 p-5 dark:border-amber-700/50 dark:bg-amber-950/20">
                <header className="mb-3 flex items-center gap-2 text-sm font-semibold text-amber-700 dark:text-amber-300">
                  <span>🤖</span>
                  <span>AI 解读 (Layer 3)</span>
                </header>
                <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
                  {narrativeMd}
                </ReactMarkdown>
              </section>
            )}
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
              {md}
            </ReactMarkdown>
          </article>
        )}
      </div>
    </Layout>
  );
};

export default AnalysisDetailPage;
