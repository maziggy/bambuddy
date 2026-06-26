import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { ChevronDown, ChevronUp, FileText } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { api } from '../api/client';

interface FolderReadmePanelProps {
  folderId: number;
}

/**
 * Side panel that renders a `.md` file from the selected folder (#1268).
 * Hidden when the folder has no markdown file. Disables raw HTML and links
 * stay text-only — same posture as the print-archive note panel.
 */
export function FolderReadmePanel({ folderId }: FolderReadmePanelProps) {
  const { t } = useTranslation();
  const [collapsed, setCollapsed] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ['folder-readme', folderId],
    queryFn: () => api.getLibraryFolderReadme(folderId),
    retry: false,
    staleTime: 30_000,
  });

  if (isLoading || error || !data) return null;

  return (
    <div className="mb-4 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg overflow-hidden">
      <button
        type="button"
        onClick={() => setCollapsed((v) => !v)}
        className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left hover:bg-bambu-dark/40 transition-colors"
      >
        <div className="flex items-center gap-2 min-w-0">
          <FileText className="w-4 h-4 text-bambu-green flex-shrink-0" />
          <span className="text-sm font-medium text-white truncate" title={data.filename}>
            {data.filename}
          </span>
          {data.truncated && (
            <span className="text-xs px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-400 flex-shrink-0">
              {t('fileManager.readme.truncated')}
            </span>
          )}
        </div>
        {collapsed ? (
          <ChevronDown className="w-4 h-4 text-bambu-gray flex-shrink-0" />
        ) : (
          <ChevronUp className="w-4 h-4 text-bambu-gray flex-shrink-0" />
        )}
      </button>
      {!collapsed && (
        <div className="px-4 py-3 border-t border-bambu-dark-tertiary max-h-96 overflow-y-auto text-sm text-bambu-gray-light leading-relaxed space-y-2">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
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
            {data.content}
          </ReactMarkdown>
        </div>
      )}
    </div>
  );
}
