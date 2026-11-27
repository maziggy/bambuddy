import ReactMarkdown from 'react-markdown';

import remarkGfmNoAutolink from '../utils/remarkGfmNoAutolink';

/**
 * Markdown written by a user, styled for the app: a folder README (#1268) and
 * the Connect slicer page's instructions (#3602).
 *
 * Raw HTML is not rendered (react-markdown's default) and links open in a new
 * tab without the opener. GFM tables, task lists and strikethrough work; bare
 * URLs don't turn into links (see remarkGfmNoAutolink, #2971).
 */
export function MarkdownContent({ children }: { children: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfmNoAutolink]}
      components={{
        h1: ({ children }) => <h1 className="text-lg font-semibold text-white mt-2 mb-1">{children}</h1>,
        h2: ({ children }) => <h2 className="text-base font-semibold text-white mt-2 mb-1">{children}</h2>,
        h3: ({ children }) => <h3 className="text-sm font-semibold text-white mt-2 mb-1">{children}</h3>,
        p: ({ children }) => <p className="my-1">{children}</p>,
        ul: ({ children }) => <ul className="list-disc list-inside space-y-0.5 ml-2">{children}</ul>,
        ol: ({ children }) => <ol className="list-decimal list-inside space-y-0.5 ml-2">{children}</ol>,
        li: ({ children }) => <li>{children}</li>,
        code: ({ children, ...props }) => {
          const inline = !(props as { className?: string }).className;
          return inline ? (
            <code className="px-1 py-0.5 bg-bambu-dark rounded text-xs font-mono text-bambu-green">{children}</code>
          ) : (
            <code className="block p-2 bg-bambu-dark rounded text-xs font-mono text-bambu-gray-light overflow-x-auto">{children}</code>
          );
        },
        pre: ({ children }) => <pre className="my-2">{children}</pre>,
        blockquote: ({ children }) => (
          <blockquote className="border-l-2 border-bambu-dark-tertiary pl-3 text-bambu-gray italic">{children}</blockquote>
        ),
        a: ({ children, href }) => (
          <a href={href} target="_blank" rel="noopener noreferrer" className="text-bambu-green hover:underline">
            {children}
          </a>
        ),
        table: ({ children }) => (
          <div className="overflow-x-auto">
            <table className="min-w-full text-xs border-collapse">{children}</table>
          </div>
        ),
        th: ({ children }) => <th className="border border-bambu-dark-tertiary px-2 py-1 text-left font-semibold text-white">{children}</th>,
        td: ({ children }) => <td className="border border-bambu-dark-tertiary px-2 py-1">{children}</td>,
        hr: () => <hr className="border-bambu-dark-tertiary my-2" />,
      }}
    >
      {children}
    </ReactMarkdown>
  );
}
