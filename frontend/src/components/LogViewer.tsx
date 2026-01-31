import { useState, useEffect, useRef, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Play,
  Square,
  Trash2,
  RefreshCw,
  Search,
  X,
  ChevronDown,
  ChevronUp,
  AlertCircle,
  AlertTriangle,
  Info,
  Bug,
} from 'lucide-react';
import { supportApi, type LogEntry } from '../api/client';
import { useTranslation } from 'react-i18next';

const LOG_LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR'] as const;
type LogLevel = (typeof LOG_LEVELS)[number];

const levelColors: Record<LogLevel, string> = {
  DEBUG: 'text-gray-400',
  INFO: 'text-blue-400',
  WARNING: 'text-yellow-400',
  ERROR: 'text-red-400',
};

const levelIcons: Record<LogLevel, typeof Info> = {
  DEBUG: Bug,
  INFO: Info,
  WARNING: AlertTriangle,
  ERROR: AlertCircle,
};

export function LogViewer() {
  const queryClient = useQueryClient();
  const { t } = useTranslation();
  const [autoScroll, setAutoScroll] = useState(true);
  const [expandedLogs, setExpandedLogs] = useState<Set<number>>(new Set());
  const [searchQuery, setSearchQuery] = useState('');
  const [levelFilter, setLevelFilter] = useState<LogLevel | 'ALL'>('ALL');
  const [isExpanded, setIsExpanded] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const logContainerRef = useRef<HTMLDivElement>(null);

  // Fetch logs with polling when streaming is enabled
  const { data, isLoading, refetch } = useQuery({
    queryKey: ['application-logs', levelFilter, searchQuery],
    queryFn: () =>
      supportApi.getLogs({
        limit: 200,
        level: levelFilter === 'ALL' ? undefined : levelFilter,
        search: searchQuery || undefined,
      }),
    refetchInterval: isStreaming ? 2000 : false, // Poll every 2 seconds when streaming
    enabled: isExpanded, // Only fetch when viewer is expanded
  });

  // Stop streaming when viewer is collapsed
  useEffect(() => {
    if (!isExpanded) {
      setIsStreaming(false);
    }
  }, [isExpanded]);

  const clearMutation = useMutation({
    mutationFn: () => supportApi.clearLogs(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['application-logs'] });
    },
  });

  // Auto-scroll to bottom when new logs arrive
  useEffect(() => {
    if (autoScroll && logContainerRef.current && data?.entries) {
      logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight;
    }
  }, [data?.entries, autoScroll]);

  const toggleExpand = (index: number) => {
    setExpandedLogs((prev) => {
      const newSet = new Set(prev);
      if (newSet.has(index)) {
        newSet.delete(index);
      } else {
        newSet.add(index);
      }
      return newSet;
    });
  };

  const formatTimestamp = (timestamp: string) => {
    // Input format: "2024-01-15 10:30:45,123"
    const parts = timestamp.split(' ');
    if (parts.length >= 2) {
      return parts[1]; // Return just the time part
    }
    return timestamp;
  };

  const entries = useMemo(() => data?.entries ?? [], [data?.entries]);

  // Reverse to show newest at bottom (better for auto-scroll UX)
  const displayEntries = useMemo(() => [...entries].reverse(), [entries]);

  const LevelIcon = ({ level }: { level: string }) => {
    const Icon = levelIcons[level as LogLevel] || Info;
    return <Icon className={`w-3.5 h-3.5 ${levelColors[level as LogLevel] || 'text-gray-400'}`} />;
  };

  return (
    <div className="bg-bambu-dark rounded-lg overflow-hidden">
      {/* Header - always visible */}
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full flex items-center justify-between p-4 hover:bg-bambu-dark-tertiary/50 transition-colors"
      >
        <div className="flex items-center gap-3">
          <div
            className={`p-2 rounded-lg ${
              isStreaming
                ? 'bg-bambu-green/20 text-bambu-green'
                : 'bg-bambu-dark-tertiary text-bambu-gray'
            }`}
          >
            <Bug className="w-5 h-5" />
          </div>
          <div className="text-left">
            <p className="font-medium text-white">{t('logViewer.title')}</p>
            <p className="text-sm text-bambu-gray">
              {isStreaming
                ? t('logViewer.liveStreaming', { count: data?.filtered_count ?? 0 })
                : t('logViewer.description')}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {isStreaming && (
            <span className="flex items-center gap-1.5 px-2 py-1 bg-bambu-green/20 rounded text-bambu-green text-xs">
              <span className="w-1.5 h-1.5 bg-bambu-green rounded-full animate-pulse" />
              {t('logViewer.live')}
            </span>
          )}
          {isExpanded ? (
            <ChevronUp className="w-5 h-5 text-bambu-gray" />
          ) : (
            <ChevronDown className="w-5 h-5 text-bambu-gray" />
          )}
        </div>
      </button>

      {/* Expanded content */}
      {isExpanded && (
        <div className="border-t border-bambu-dark-tertiary">
          {/* Controls */}
          <div className="flex flex-col gap-2 p-4 border-b border-bambu-dark-tertiary">
            <div className="flex items-center gap-2 flex-wrap">
              {/* Start/Stop streaming button */}
              {isStreaming ? (
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    setIsStreaming(false);
                  }}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-red-500/20 text-red-400 hover:bg-red-500/30 rounded transition-colors"
                >
                  <Square className="w-4 h-4" />
                  {t('logViewer.stop')}
                </button>
              ) : (
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    setIsStreaming(true);
                    refetch(); // Immediately fetch when starting
                  }}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-bambu-green/20 text-bambu-green hover:bg-bambu-green/30 rounded transition-colors"
                >
                  <Play className="w-4 h-4" />
                  {t('logViewer.start')}
                </button>
              )}

              {/* Clear button */}
              <button
                onClick={() => clearMutation.mutate()}
                disabled={clearMutation.isPending || entries.length === 0}
                className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-bambu-dark-tertiary text-bambu-gray hover:text-white hover:bg-bambu-dark-secondary rounded transition-colors disabled:opacity-50"
              >
                <Trash2 className="w-4 h-4" />
                {t('logViewer.clear')}
              </button>

              {/* Refresh button */}
              <button
                onClick={() => refetch()}
                disabled={isLoading}
                className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-bambu-dark-tertiary text-bambu-gray hover:text-white hover:bg-bambu-dark-secondary rounded transition-colors disabled:opacity-50"
              >
                <RefreshCw className={`w-4 h-4 ${isLoading ? 'animate-spin' : ''}`} />
              </button>

              <div className="flex-1" />

              {/* Auto-scroll toggle */}
              <label className="flex items-center gap-2 text-sm text-bambu-gray cursor-pointer">
                <input
                  type="checkbox"
                  checked={autoScroll}
                  onChange={(e) => setAutoScroll(e.target.checked)}
                  className="rounded border-bambu-dark-tertiary bg-bambu-dark-tertiary"
                />
                {t('logViewer.autoScroll')}
              </label>

              {/* Entry count */}
              <span className="text-sm text-bambu-gray">
                {data?.filtered_count ?? 0}/{data?.total_in_file ?? 0}
              </span>
            </div>

            {/* Search and Filter Row */}
            <div className="flex items-center gap-2">
              {/* Search input */}
              <div className="relative flex-1">
                <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray" />
                <input
                  type="text"
                  placeholder={t('logViewer.searchPlaceholder')}
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="w-full pl-8 pr-8 py-1.5 text-sm bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded text-white placeholder-bambu-gray focus:border-bambu-green focus:outline-none"
                />
                {searchQuery && (
                  <button
                    onClick={() => setSearchQuery('')}
                    className="absolute right-2 top-1/2 -translate-y-1/2 text-bambu-gray hover:text-white"
                  >
                    <X className="w-4 h-4" />
                  </button>
                )}
              </div>

              {/* Level filter */}
              <div className="flex items-center gap-1 bg-bambu-dark-secondary rounded border border-bambu-dark-tertiary">
                <button
                  onClick={() => setLevelFilter('ALL')}
                  className={`px-2 py-1.5 text-xs rounded-l transition-colors ${
                    levelFilter === 'ALL'
                      ? 'bg-bambu-green text-white'
                      : 'text-bambu-gray hover:text-white'
                  }`}
                >
                  {t('common.all')}
                </button>
                {LOG_LEVELS.map((level, idx) => (
                  <button
                    key={level}
                    onClick={() => setLevelFilter(level)}
                    className={`px-2 py-1.5 text-xs transition-colors flex items-center gap-1 ${
                      idx === LOG_LEVELS.length - 1 ? 'rounded-r' : ''
                    } ${
                      levelFilter === level
                        ? `${levelColors[level]} bg-bambu-dark-tertiary`
                        : 'text-bambu-gray hover:text-white'
                    }`}
                  >
                    {level}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* Log Content */}
          <div
            ref={logContainerRef}
            className="overflow-auto font-mono text-xs bg-black min-h-[300px] max-h-[500px]"
          >
            {entries.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-[300px] text-bambu-gray">
                <p className="mb-2">{t('logViewer.noEntries')}</p>
                <p className="text-sm">{t('logViewer.emptyLog')}</p>
              </div>
            ) : (
              <div className="divide-y divide-bambu-dark-tertiary/30">
                {displayEntries.map((log: LogEntry, index: number) => {
                  const isEntryExpanded = expandedLogs.has(index);
                  const hasMultiLine = log.message.includes('\n');

                  return (
                    <div
                      key={index}
                      className={`p-2 cursor-pointer hover:bg-bambu-dark-secondary/50 transition-colors ${
                        isEntryExpanded ? 'bg-bambu-dark-secondary/30' : ''
                      }`}
                      onClick={() => hasMultiLine && toggleExpand(index)}
                    >
                      <div className="flex items-start gap-2">
                        <span className="text-bambu-gray/70 shrink-0 w-20">
                          {formatTimestamp(log.timestamp)}
                        </span>
                        <span className="shrink-0">
                          <LevelIcon level={log.level} />
                        </span>
                        <span className="text-purple-400/80 shrink-0 max-w-[200px] truncate" title={log.logger_name}>
                          [{log.logger_name}]
                        </span>
                        <span
                          className={`flex-1 ${levelColors[log.level as LogLevel] || 'text-white/80'} ${
                            !isEntryExpanded && hasMultiLine ? 'truncate' : ''
                          }`}
                        >
                          {isEntryExpanded ? (
                            <pre className="whitespace-pre-wrap break-all">{log.message}</pre>
                          ) : (
                            log.message.split('\n')[0]
                          )}
                        </span>
                        {hasMultiLine && (
                          <span className="text-bambu-gray/50 shrink-0">
                            {isEntryExpanded ? (
                              <ChevronUp className="w-3.5 h-3.5" />
                            ) : (
                              <ChevronDown className="w-3.5 h-3.5" />
                            )}
                          </span>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Footer */}
          <div className="flex items-center justify-between p-3 border-t border-bambu-dark-tertiary text-sm text-bambu-gray">
            {isStreaming ? (
              <span className="flex items-center gap-2">
                <span className="w-2 h-2 bg-green-500 rounded-full animate-pulse" />
                {t('logViewer.autoRefreshing')}
              </span>
            ) : (
              <span>{t('logViewer.clickStart')}</span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
