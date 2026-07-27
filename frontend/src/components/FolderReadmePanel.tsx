import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { FileText, PanelRightClose, PanelRightOpen } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { api } from '../api/client';

interface FolderReadmePanelProps {
  folderId: number;
}

// Persist the collapsed choice so hiding the README once keeps it hidden
// across folder switches and page reloads (#2520 item 2).
const COLLAPSE_STORAGE_KEY = 'fileManager.readmeCollapsed';

/**
 * Markdown panel for the selected folder (#1268).
 *
 * Docks as a collapsible right-hand rail on wide screens so the README sits
 * *beside* the file list instead of pushing it down and eating vertical
 * space (#2520 item 2); on narrow screens it stacks above the list
 * (`order-first`) where the page itself scrolls. Collapsing shrinks it to a
 * thin strip (desktop) / slim bar (mobile) with a one-click reopen, and the
 * choice is persisted. Auto-hidden when the folder has no markdown. Raw HTML
 * is disabled and links stay text-only — same posture as the print-archive
 * note panel.
 */
export function FolderReadmePanel({ folderId }: FolderReadmePanelProps) {
  const { t } = useTranslation();
  const [collapsed, setCollapsed] = useState<boolean>(
    () => localStorage.getItem(COLLAPSE_STORAGE_KEY) === '1',
  );

  useEffect(() => {
    localStorage.setItem(COLLAPSE_STORAGE_KEY, collapsed ? '1' : '0');
  }, [collapsed]);

  const { data, isLoading, error } = useQuery({
    queryKey: ['folder-readme', folderId],
    queryFn: () => api.getLibraryFolderReadme(folderId),
    retry: false,
    staleTime: 30_000,
  });

  if (isLoading || error || !data) return null;

  if (collapsed) {
    return (
      <div className="mb-2 lg:mb-0 order-first lg:order-none lg:w-10 lg:flex-shrink-0 lg:h-full">
        {/* Mobile: a slim horizontal bar that reopens the panel. */}
        <button
          type="button"
          onClick={() => setCollapsed(false)}
          title={t('fileManager.readme.show')}
          aria-label={t('fileManager.readme.show')}
          className="flex lg:hidden w-full items-center gap-2 px-3 py-2 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg hover:bg-bambu-dark/40 transition-colors"
        >
          <FileText className="w-4 h-4 text-bambu-green flex-shrink-0" />
          <span className="text-sm font-medium text-white truncate">{data.filename}</span>
          <PanelRightOpen className="w-4 h-4 text-bambu-gray flex-shrink-0 ml-auto" />
        </button>
        {/* Desktop: a thin vertical strip with a reopen button. */}
        <button
          type="button"
          onClick={() => setCollapsed(false)}
          title={t('fileManager.readme.show')}
          aria-label={t('fileManager.readme.show')}
          className="hidden lg:flex h-full w-10 flex-col items-center gap-2 py-3 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg hover:bg-bambu-dark/40 transition-colors"
        >
          <PanelRightOpen className="w-4 h-4 text-bambu-green flex-shrink-0" />
          <span className="text-xs font-medium text-bambu-gray [writing-mode:vertical-rl] rotate-180 select-none">
            {t('fileManager.readme.label')}
          </span>
        </button>
      </div>
    );
  }

  return (
    <div className="mb-4 lg:mb-0 order-first lg:order-none lg:w-80 xl:w-96 lg:flex-shrink-0 lg:h-full flex flex-col bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg overflow-hidden">
      <div className="flex items-center justify-between gap-2 px-3 py-2">
        <div className="flex items-center gap-2 min-w-0">
          <FileText className="w-4 h-4 text-bambu-green flex-shrink-0" />
          <span className="text-sm font-medium text-white truncate" title={data.filename}>
            {data.filename}
          </span>
          {data.truncated && (
            <span className="text-xs px-1.5 py-0.5 rounded bg-amber-100 dark:bg-amber-500/20 text-amber-700 dark:text-amber-400 flex-shrink-0">
              {t('fileManager.readme.truncated')}
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={() => setCollapsed(true)}
          title={t('fileManager.readme.hide')}
          aria-label={t('fileManager.readme.hide')}
          className="p-1 rounded text-bambu-gray hover:text-white hover:bg-bambu-dark/40 transition-colors flex-shrink-0"
        >
          <PanelRightClose className="w-4 h-4" />
        </button>
      </div>
      <div className="px-4 py-3 border-t border-bambu-dark-tertiary flex-1 overflow-y-auto max-h-96 lg:max-h-none text-sm text-bambu-gray-light leading-relaxed space-y-2">
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
    </div>
  );
}
