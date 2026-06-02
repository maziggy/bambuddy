import { useState, useRef, useCallback, useMemo, useEffect } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import {
  FolderOpen,
  Loader2,
  Plus,
  Upload,
  Trash2,
  Download,
  MoreVertical,
  ChevronRight,
  FolderPlus,
  FileBox,
  Clock,
  HardDrive,
  File,
  MoveRight,
  CheckSquare,
  Square,
  LayoutGrid,
  List,
  Search,
  SortAsc,
  SortDesc,
  AlertTriangle,
  Filter,
  X,
  Link2,
  Unlink,
  Archive as ArchiveIcon,
  Briefcase,
  Cog,
  Printer,
  Pencil,
  Image,
  User,
  Box,
  RefreshCw,
  Lock,
  FolderSymlink,
  Tag as TagIcon,
} from 'lucide-react';
import { api } from '../api/client';
import type {
  LibraryFolderTree,
  LibraryFileListItem,
  LibraryFolderCreate,
  LibraryFolderUpdate,
  ExternalFolderCreate,
  AppSettings,
  Archive,
  Permission,
} from '../api/client';
import { Button } from '../components/Button';
import { ConfirmModal } from '../components/ConfirmModal';
import { PrintModal } from '../components/PrintModal';
import { ModelViewerModal } from '../components/ModelViewerModal';
import { SliceModal } from '../components/SliceModal';
import { BulkTagsPickerModal } from '../components/BulkTagsPickerModal';
import { FileUploadModal } from '../components/FileUploadModal';
import { FolderReadmePanel } from '../components/FolderReadmePanel';
import { LibraryTagsModal } from '../components/LibraryTagsModal';
import { PurgeOldFilesModal } from '../components/PurgeOldFilesModal';
import { useToast } from '../contexts/ToastContext';
import { useIsMobile } from '../hooks/useIsMobile';
import { usePageFileDrop } from '../hooks/usePageFileDrop';
import { useAuth } from '../contexts/AuthContext';
import { formatDuration, parseUTCDate } from '../utils/date';
import { formatFileSize } from '../utils/file';

type SortField = 'name' | 'date' | 'size' | 'type' | 'prints';
type SortDirection = 'asc' | 'desc';
type TFunction = (key: string, options?: Record<string, unknown>) => string;

// New Folder Modal
interface NewFolderModalProps {
  parentId: number | null;
  onClose: () => void;
  onSave: (data: LibraryFolderCreate) => void;
  isLoading: boolean;
  t: TFunction;
}

function NewFolderModal({ parentId, onClose, onSave, isLoading, t }: NewFolderModalProps) {
  const [name, setName] = useState('');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSave({ name: name.trim(), parent_id: parentId });
  };

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
      <div className="bg-bambu-dark-secondary rounded-lg w-full max-w-sm border border-bambu-dark-tertiary">
        <div className="p-4 border-b border-bambu-dark-tertiary">
          <h2 className="text-lg font-semibold text-white">{t('fileManager.newFolder')}</h2>
        </div>
        <form onSubmit={handleSubmit} className="p-4 space-y-4">
          <div>
            <label className="block text-sm font-medium text-white mb-1">
              {t('fileManager.folderName')}
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full bg-bambu-dark border border-bambu-dark-tertiary rounded px-3 py-2 text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green"
              placeholder={t('fileManager.folderNamePlaceholder')}
              autoFocus
              required
            />
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="secondary" onClick={onClose}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" disabled={!name.trim() || isLoading}>
              {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : t('common.create')}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}

// External Folder Modal
interface ExternalFolderModalProps {
  onClose: () => void;
  onSave: (data: ExternalFolderCreate) => void;
  isLoading: boolean;
  t: TFunction;
}

function ExternalFolderModal({ onClose, onSave, isLoading, t }: ExternalFolderModalProps) {
  const [name, setName] = useState('');
  const [path, setPath] = useState('');
  const [readonly, setReadonly] = useState(true);
  const [showHidden, setShowHidden] = useState(false);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSave({
      name: name.trim(),
      external_path: path.trim(),
      readonly,
      show_hidden: showHidden,
    });
  };

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
      <div className="bg-bambu-dark-secondary rounded-lg w-full max-w-md border border-bambu-dark-tertiary">
        <div className="p-4 border-b border-bambu-dark-tertiary">
          <h2 className="text-lg font-semibold text-white flex items-center gap-2">
            <FolderSymlink className="w-5 h-5 text-bambu-green" />
            {t('fileManager.linkExternalFolder')}
          </h2>
          <p className="text-sm text-bambu-gray mt-1">{t('fileManager.linkExternalFolderDescription')}</p>
        </div>
        <form onSubmit={handleSubmit} className="p-4 space-y-4">
          <div>
            <label className="block text-sm font-medium text-white mb-1">
              {t('fileManager.folderName')}
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full bg-bambu-dark border border-bambu-dark-tertiary rounded px-3 py-2 text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green"
              placeholder={t('fileManager.externalFolderNamePlaceholder')}
              autoFocus
              required
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-white mb-1">
              {t('fileManager.externalPath')}
            </label>
            <input
              type="text"
              value={path}
              onChange={(e) => setPath(e.target.value)}
              className="w-full bg-bambu-dark border border-bambu-dark-tertiary rounded px-3 py-2 text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green font-mono text-sm"
              placeholder="/mnt/nas/3d-prints"
              required
            />
            <p className="text-xs text-bambu-gray mt-1">{t('fileManager.externalPathHelp')}</p>
          </div>
          <div className="space-y-2">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={readonly}
                onChange={(e) => setReadonly(e.target.checked)}
                className="rounded border-bambu-dark-tertiary bg-bambu-dark text-bambu-green focus:ring-bambu-green"
              />
              <span className="text-sm text-white">{t('fileManager.readOnly')}</span>
              <span className="text-xs text-bambu-gray">({t('fileManager.readOnlyHelp')})</span>
            </label>
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={showHidden}
                onChange={(e) => setShowHidden(e.target.checked)}
                className="rounded border-bambu-dark-tertiary bg-bambu-dark text-bambu-green focus:ring-bambu-green"
              />
              <span className="text-sm text-white">{t('fileManager.showHiddenFiles')}</span>
            </label>
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="secondary" onClick={onClose}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" disabled={!name.trim() || !path.trim() || isLoading}>
              {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : t('fileManager.linkFolder')}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}

// FAT32/exFAT-illegal chars rejected by Bambu Studio (#1540). Mirrors the
// backend validator in backend/app/utils/filename.py — keep in sync.
const INVALID_FILENAME_CHARS = '<>:"/\\|?*';

function findInvalidFilenameChar(name: string): string | null {
  for (const ch of name) {
    if (INVALID_FILENAME_CHARS.includes(ch)) return ch;
    if (ch.charCodeAt(0) < 0x20) return ch;
  }
  return null;
}

// Rename Modal
interface RenameModalProps {
  type: 'file' | 'folder';
  currentName: string;
  onClose: () => void;
  onSave: (newName: string) => void;
  isLoading: boolean;
  t: TFunction;
}

function RenameModal({ type, currentName, onClose, onSave, isLoading, t }: RenameModalProps) {
  // For files, separate the extension so users can only edit the base name
  // Handle compound extensions like .gcode.3mf
  const fileExtension = type === 'file' ? (currentName.match(/(\.gcode\.3mf|\.3mf|\.gcode)$/i)?.[1] ?? '') : '';
  const baseName = type === 'file' && fileExtension ? currentName.slice(0, -fileExtension.length) : currentName;
  const [name, setName] = useState(baseName);

  const invalidChar = type === 'file' ? findInvalidFilenameChar(name) : null;
  const filenameError = invalidChar
    ? t('fileManager.invalidFilenameChar', { char: invalidChar })
    : null;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (filenameError) return;
    const fullName = type === 'file' ? name.trim() + fileExtension : name.trim();
    if (name.trim() && fullName !== currentName) {
      onSave(fullName);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
      <div className="bg-bambu-dark-secondary rounded-lg w-full max-w-sm border border-bambu-dark-tertiary">
        <div className="p-4 border-b border-bambu-dark-tertiary">
          <h2 className="text-lg font-semibold text-white">{type === 'file' ? t('fileManager.renameFile') : t('fileManager.renameFolder')}</h2>
        </div>
        <form onSubmit={handleSubmit} className="p-4 space-y-4">
          <div>
            <label className="block text-sm font-medium text-white mb-1">
              {t('common.name')}
            </label>
            <div className={`flex items-center bg-bambu-dark border rounded focus-within:border-bambu-green ${filenameError ? 'border-red-500' : 'border-bambu-dark-tertiary'}`}>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="flex-1 bg-transparent px-3 py-2 text-white placeholder-bambu-gray focus:outline-none min-w-0"
                autoFocus
                required
              />
              {fileExtension && (
                <span className="pr-3 text-bambu-gray text-sm select-none whitespace-nowrap">{fileExtension}</span>
              )}
            </div>
            {filenameError && (
              <p className="mt-1 text-xs text-red-400">{filenameError}</p>
            )}
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="secondary" onClick={onClose}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" disabled={!name.trim() || name.trim() === baseName || !!filenameError || isLoading}>
              {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : t('common.rename')}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}

// Move Files Modal
interface MoveFilesModalProps {
  folders: LibraryFolderTree[];
  selectedFiles: number[];
  currentFolderId: number | null;
  onClose: () => void;
  onMove: (folderId: number | null) => void;
  isLoading: boolean;
  t: TFunction;
}

function MoveFilesModal({ folders, selectedFiles, currentFolderId, onClose, onMove, isLoading, t }: MoveFilesModalProps) {
  const [targetFolder, setTargetFolder] = useState<number | null>(null);

  const flattenFolders = (items: LibraryFolderTree[], depth = 0): { id: number | null; name: string; depth: number }[] => {
    const result: { id: number | null; name: string; depth: number }[] = [];
    for (const item of items) {
      result.push({ id: item.id, name: item.name, depth });
      if (item.children.length > 0) {
        result.push(...flattenFolders(item.children, depth + 1));
      }
    }
    return result;
  };

  const flatFolders = [{ id: null, name: t('fileManager.rootNoFolder'), depth: 0 }, ...flattenFolders(folders)];

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
      <div className="bg-bambu-dark-secondary rounded-lg w-full max-w-sm border border-bambu-dark-tertiary">
        <div className="p-4 border-b border-bambu-dark-tertiary">
          <h2 className="text-lg font-semibold text-white">{t('fileManager.moveFiles', { count: selectedFiles.length })}</h2>
        </div>
        <div className="p-4 space-y-4">
          <div className="max-h-64 overflow-y-auto space-y-1">
            {flatFolders.map((folder) => (
              <button
                key={folder.id ?? 'root'}
                onClick={() => setTargetFolder(folder.id)}
                disabled={folder.id === currentFolderId}
                className={`w-full text-left px-3 py-2 rounded transition-colors flex items-center gap-2 ${
                  targetFolder === folder.id
                    ? 'bg-bambu-green/20 text-bambu-green'
                    : folder.id === currentFolderId
                    ? 'opacity-50 cursor-not-allowed text-bambu-gray'
                    : 'hover:bg-bambu-dark text-white'
                }`}
                style={{ paddingLeft: `${12 + folder.depth * 16}px` }}
              >
                <FolderOpen className="w-4 h-4" />
                {folder.name}
                {folder.id === currentFolderId && <span className="text-xs text-bambu-gray ml-auto">({t('fileManager.current')})</span>}
              </button>
            ))}
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="secondary" onClick={onClose}>
              {t('common.cancel')}
            </Button>
            <Button onClick={() => onMove(targetFolder)} disabled={isLoading}>
              {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : t('common.move')}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

// Link Folder Modal
interface LinkFolderModalProps {
  folder: LibraryFolderTree;
  onClose: () => void;
  onLink: (update: LibraryFolderUpdate) => void;
  isLoading: boolean;
  t: TFunction;
}

function LinkFolderModal({ folder, onClose, onLink, isLoading, t }: LinkFolderModalProps) {
  const [linkType, setLinkType] = useState<'project' | 'archive'>('project');
  const [selectedId, setSelectedId] = useState<number | null>(
    folder.project_id || folder.archive_id || null
  );

  // Initialize linkType based on existing link
  useState(() => {
    if (folder.archive_id) setLinkType('archive');
  });

  const { data: projects } = useQuery({
    queryKey: ['projects'],
    queryFn: () => api.getProjects(),
    select: (rows) => [...rows].sort((a, b) => a.name.localeCompare(b.name)),
  });

  const { data: archives } = useQuery({
    queryKey: ['archives-for-link'],
    queryFn: () => api.getArchives(undefined, undefined, 100),
  });

  const handleSave = () => {
    if (linkType === 'project') {
      onLink({
        project_id: selectedId,
        archive_id: 0, // Unlink archive
      });
    } else {
      onLink({
        project_id: 0, // Unlink project
        archive_id: selectedId,
      });
    }
  };

  const handleUnlink = () => {
    onLink({
      project_id: 0,
      archive_id: 0,
    });
  };

  const isLinked = folder.project_id || folder.archive_id;

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
      <div className="bg-bambu-dark-secondary rounded-lg w-full max-w-md border border-bambu-dark-tertiary">
        <div className="p-4 border-b border-bambu-dark-tertiary flex items-center justify-between">
          <h2 className="text-lg font-semibold text-white flex items-center gap-2">
            <Link2 className="w-5 h-5 text-bambu-green" />
            {t('fileManager.linkFolder')}
          </h2>
          <button onClick={onClose} className="p-1 hover:bg-bambu-dark rounded">
            <X className="w-5 h-5 text-bambu-gray" />
          </button>
        </div>

        <div className="p-4 space-y-4">
          <p className="text-sm text-bambu-gray">
            {t('fileManager.linkFolderDescription', { name: folder.name })}
          </p>

          {/* Link type selector */}
          <div className="flex gap-2">
            <button
              onClick={() => { setLinkType('project'); setSelectedId(null); }}
              className={`flex-1 flex items-center justify-center gap-2 px-3 py-2 rounded-lg border transition-colors ${
                linkType === 'project'
                  ? 'border-bambu-green bg-bambu-green/10 text-bambu-green'
                  : 'border-bambu-dark-tertiary text-bambu-gray hover:text-white'
              }`}
            >
              <Briefcase className="w-4 h-4" />
              {t('fileManager.project')}
            </button>
            <button
              onClick={() => { setLinkType('archive'); setSelectedId(null); }}
              className={`flex-1 flex items-center justify-center gap-2 px-3 py-2 rounded-lg border transition-colors ${
                linkType === 'archive'
                  ? 'border-bambu-green bg-bambu-green/10 text-bambu-green'
                  : 'border-bambu-dark-tertiary text-bambu-gray hover:text-white'
              }`}
            >
              <ArchiveIcon className="w-4 h-4" />
              {t('fileManager.archive')}
            </button>
          </div>

          {/* Selection list */}
          <div className="max-h-64 overflow-y-auto space-y-1 bg-bambu-dark rounded-lg p-2">
            {linkType === 'project' ? (
              projects && projects.length > 0 ? (
                projects.map((project) => (
                  <button
                    key={project.id}
                    onClick={() => setSelectedId(project.id)}
                    className={`w-full text-left px-3 py-2 rounded transition-colors flex items-center gap-2 ${
                      selectedId === project.id
                        ? 'bg-bambu-green/20 text-bambu-green'
                        : 'hover:bg-bambu-dark-tertiary text-white'
                    }`}
                  >
                    <div
                      className="w-3 h-3 rounded-full flex-shrink-0"
                      style={{ backgroundColor: project.color || '#00ae42' }}
                    />
                    <span className="truncate">{project.name}</span>
                  </button>
                ))
              ) : (
                <p className="text-sm text-bambu-gray text-center py-4">{t('fileManager.noProjectsFound')}</p>
              )
            ) : (
              archives && archives.length > 0 ? (
                archives.map((archive: Archive) => (
                  <button
                    key={archive.id}
                    onClick={() => setSelectedId(archive.id)}
                    className={`w-full text-left px-3 py-2 rounded transition-colors flex items-center gap-2 ${
                      selectedId === archive.id
                        ? 'bg-bambu-green/20 text-bambu-green'
                        : 'hover:bg-bambu-dark-tertiary text-white'
                    }`}
                  >
                    <FileBox className="w-4 h-4 text-bambu-gray flex-shrink-0" />
                    <span className="truncate">{archive.print_name || archive.filename}</span>
                  </button>
                ))
              ) : (
                <p className="text-sm text-bambu-gray text-center py-4">{t('fileManager.noArchivesFound')}</p>
              )
            )}
          </div>
        </div>

        <div className="p-4 border-t border-bambu-dark-tertiary flex justify-between">
          {isLinked && (
            <Button variant="danger" onClick={handleUnlink} disabled={isLoading}>
              <Unlink className="w-4 h-4 mr-2" />
              {t('fileManager.unlink')}
            </Button>
          )}
          <div className={`flex gap-2 ${!isLinked ? 'ml-auto' : ''}`}>
            <Button variant="secondary" onClick={onClose}>
              {t('common.cancel')}
            </Button>
            <Button onClick={handleSave} disabled={!selectedId || isLoading}>
              {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : t('fileManager.link')}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

// Folder Tree Item
interface FolderTreeItemProps {
  folder: LibraryFolderTree;
  selectedFolderId: number | null;
  onSelect: (id: number | null) => void;
  onDelete: (id: number) => void;
  onLink: (folder: LibraryFolderTree) => void;
  onRename: (folder: LibraryFolderTree) => void;
  depth?: number;
  wrapNames?: boolean;
  defaultExpanded?: boolean;
  hasPermission: (permission: Permission) => boolean;
  t: TFunction;
}

function FolderTreeItem({ folder, selectedFolderId, onSelect, onDelete, onLink, onRename, depth = 0, wrapNames = false, defaultExpanded = true, hasPermission, t }: FolderTreeItemProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const [showActions, setShowActions] = useState(false);
  const hasChildren = folder.children.length > 0;
  const isLinked = folder.project_id || folder.archive_id;
  const isExternal = folder.is_external;

  return (
    <div>
      <div
        className={`group flex items-center gap-1 px-2 py-1.5 rounded cursor-pointer transition-colors ${
          selectedFolderId === folder.id
            ? 'bg-bambu-green/20 text-bambu-green'
            : 'hover:bg-bambu-dark text-white'
        }`}
        style={{ paddingLeft: `${8 + depth * 12}px` }}
        onClick={() => onSelect(folder.id)}
      >
        {hasChildren ? (
          <button
            onClick={(e) => {
              e.stopPropagation();
              setExpanded(!expanded);
            }}
            className="p-0.5 hover:bg-bambu-dark-tertiary rounded"
          >
            <ChevronRight className={`w-3.5 h-3.5 transition-transform ${expanded ? 'rotate-90' : ''}`} />
          </button>
        ) : (
          <div className="w-4.5" />
        )}
        {isExternal ? (
          <FolderSymlink className="w-4 h-4 text-purple-400 flex-shrink-0" />
        ) : (
          <FolderOpen className="w-4 h-4 text-bambu-green flex-shrink-0" />
        )}
        <span className={`text-sm flex-1 min-w-0 ${wrapNames ? 'break-all' : 'truncate'}`} title={folder.name}>{folder.name}</span>
        {/* Link indicator - clickable to change link */}
        {isLinked && (
          <button
            onClick={(e) => { e.stopPropagation(); onLink(folder); }}
            className="flex-shrink-0 flex items-center gap-1 text-xs px-1.5 py-0.5 rounded bg-blue-500/20 text-blue-400 hover:bg-blue-500/30 transition-colors"
            title={`${folder.project_name ? `Project: ${folder.project_name}` : `Archive: ${folder.archive_name}`} (click to change)`}
          >
            <Link2 className="w-3 h-3" />
            {folder.project_name ? (
              <Briefcase className="w-3 h-3" />
            ) : (
              <ArchiveIcon className="w-3 h-3" />
            )}
          </button>
        )}
        {/* Read-only indicator for external folders */}
        {isExternal && folder.external_readonly && (
          <span title={t('fileManager.readOnly')}>
            <Lock className="w-3 h-3 text-amber-400 flex-shrink-0" />
          </span>
        )}
        {folder.file_count > 0 && (
          <span className="flex-shrink-0 text-xs text-bambu-gray">{folder.file_count}</span>
        )}
        {/* Quick link button - always visible for unlinked folders */}
        {!isLinked && !isExternal && (
          <button
            onClick={(e) => { e.stopPropagation(); onLink(folder); }}
            className="flex-shrink-0 p-1 rounded hover:bg-bambu-dark-tertiary"
            title={t('fileManager.linkToProjectOrArchive')}
          >
            <Link2 className="w-3.5 h-3.5 text-bambu-gray hover:text-bambu-green" />
          </button>
        )}
        <div className={`flex-shrink-0 flex items-center gap-0.5 transition-opacity ${wrapNames ? '' : 'opacity-0 group-hover:opacity-100'}`} onClick={(e) => e.stopPropagation()}>
          <div className="relative">
            <button
              onClick={() => setShowActions(!showActions)}
              className="p-1 rounded hover:bg-bambu-dark-tertiary"
            >
              <MoreVertical className="w-3.5 h-3.5 text-bambu-gray" />
            </button>
            {showActions && (
              <>
                <div className="fixed inset-0 z-10" onClick={() => setShowActions(false)} />
                <div className="absolute right-0 top-full mt-1 z-20 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-xl py-1 min-w-[120px]">
                <button
                  className={`w-full px-3 py-1.5 text-left text-sm flex items-center gap-2 ${
                    hasPermission('library:update_all') ? 'text-white hover:bg-bambu-dark' : 'text-bambu-gray cursor-not-allowed'
                  }`}
                  onClick={() => { if (hasPermission('library:update_all')) { onRename(folder); setShowActions(false); } }}
                  disabled={!hasPermission('library:update_all')}
                  title={!hasPermission('library:update_all') ? t('fileManager.noPermissionRenameFolder') : undefined}
                >
                  <Pencil className="w-3.5 h-3.5" />
                  {t('common.rename')}
                </button>
                <button
                  className={`w-full px-3 py-1.5 text-left text-sm flex items-center gap-2 ${
                    hasPermission('library:update_all') ? 'text-white hover:bg-bambu-dark' : 'text-bambu-gray cursor-not-allowed'
                  }`}
                  onClick={() => { if (hasPermission('library:update_all')) { onLink(folder); setShowActions(false); } }}
                  disabled={!hasPermission('library:update_all')}
                  title={!hasPermission('library:update_all') ? t('fileManager.noPermissionLinkFolder') : undefined}
                >
                  <Link2 className="w-3.5 h-3.5" />
                  {isLinked ? t('fileManager.changeLink') : t('fileManager.linkTo')}
                </button>
                <button
                  className={`w-full px-3 py-1.5 text-left text-sm flex items-center gap-2 ${
                    hasPermission('library:delete_all') ? 'text-red-400 hover:bg-bambu-dark' : 'text-bambu-gray cursor-not-allowed'
                  }`}
                  onClick={() => { if (hasPermission('library:delete_all')) { onDelete(folder.id); setShowActions(false); } }}
                  disabled={!hasPermission('library:delete_all')}
                  title={!hasPermission('library:delete_all') ? t('fileManager.noPermissionDeleteFolder') : undefined}
                >
                  <Trash2 className="w-3.5 h-3.5" />
                  {t('common.delete')}
                </button>
              </div>
              </>
            )}
          </div>
        </div>
      </div>
      {hasChildren && expanded && (
        <div>
          {folder.children.map((child) => (
            <FolderTreeItem
              key={child.id}
              folder={child}
              selectedFolderId={selectedFolderId}
              onSelect={onSelect}
              onDelete={onDelete}
              onLink={onLink}
              onRename={onRename}
              depth={depth + 1}
              wrapNames={wrapNames}
              defaultExpanded={defaultExpanded}
              hasPermission={hasPermission}
              t={t}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// Helper to check if a file is sliced (printable)
function isSlicedFilename(filename: string): boolean {
  const lower = filename.toLowerCase();
  return lower.endsWith('.gcode') || lower.endsWith('.gcode.3mf');
}

// Files that can be fed to the slicer sidecar (model geometry inputs).
// Excludes .gcode.* (already sliced) and any other non-model formats.
function isSliceableFilename(filename: string): boolean {
  const lower = filename.toLowerCase();
  if (lower.endsWith('.gcode') || lower.endsWith('.gcode.3mf')) return false;
  return lower.endsWith('.stl') || lower.endsWith('.3mf') || lower.endsWith('.step') || lower.endsWith('.stp');
}

// File Card
interface FileCardProps {
  file: LibraryFileListItem;
  isSelected: boolean;
  isMobile: boolean;
  onSelect: (id: number) => void;
  onDelete: (id: number) => void;
  onDownload: (id: number) => void;
  onPrint?: (file: LibraryFileListItem) => void;
  onSlice?: (file: LibraryFileListItem) => void;
  useSlicerApi?: boolean;
  onPreview3d?: (file: LibraryFileListItem) => void;
  onRename?: (file: LibraryFileListItem) => void;
  onGenerateThumbnail?: (file: LibraryFileListItem) => void;
  onTagClick?: (tagId: number) => void;
  thumbnailVersion?: number;
  hasPermission: (permission: Permission) => boolean;
  canModify: (resource: 'queue' | 'archives' | 'library', action: 'update' | 'delete' | 'reprint', createdById: number | null | undefined) => boolean;
  authEnabled: boolean;
  t: TFunction;
}

function FileCard({ file, isSelected, isMobile, onSelect, onDelete, onDownload, onPrint, onSlice, useSlicerApi, onPreview3d, onRename, onGenerateThumbnail, onTagClick, thumbnailVersion, hasPermission, canModify, authEnabled, t }: FileCardProps) {
  const [showActions, setShowActions] = useState(false);

  return (
    <div
      className={`group relative bg-bambu-dark-secondary rounded-lg border transition-all cursor-pointer overflow-hidden ${
        isSelected
          ? 'border-bambu-green ring-1 ring-bambu-green'
          : 'border-bambu-dark-tertiary hover:border-bambu-green/50'
      }`}
      onClick={() => onSelect(file.id)}
    >
      {/* Thumbnail */}
      <div className="aspect-square bg-bambu-dark flex items-center justify-center overflow-hidden">
        {file.thumbnail_path ? (
          <img
            src={`${api.getLibraryFileThumbnailUrl(file.id)}${thumbnailVersion ? ((api.getLibraryFileThumbnailUrl(file.id).includes('?') ? '&' : '?') + `v=${thumbnailVersion}`) : ''}`}
            alt={file.filename}
            className="w-full h-full object-cover"
          />
        ) : (
          <FileBox className="w-12 h-12 text-bambu-gray/30" />
        )}
        {/* File type badge */}
        <div className={`absolute top-2 right-2 text-xs px-1.5 py-0.5 rounded font-medium ${
          file.file_type === '3mf' ? 'bg-bambu-green/90 text-white'
          // Sliced output — share the gcode blue so users see at a glance
          // that the file is already sliced and ready to print (#1543).
          : file.file_type === 'gcode' || file.file_type === 'gcode.3mf' ? 'bg-blue-500/90 text-white'
          : file.file_type === 'stl' ? 'bg-purple-500/90 text-white'
          : 'bg-bambu-gray/90 text-white'
        }`}>
          {file.file_type.toUpperCase()}
        </div>
      </div>

      {/* Info */}
      <div className="p-3">
        <h3 className="text-sm font-medium text-white truncate" title={file.print_name || file.filename}>
          {file.print_name || file.filename}
        </h3>
        <div className="flex items-center gap-3 mt-1 text-xs text-bambu-gray">
          <span>{formatFileSize(file.file_size)}</span>
          {file.print_time_seconds && (
            <span className="flex items-center gap-1">
              <Clock className="w-3 h-3" />
              {formatDuration(file.print_time_seconds)}
            </span>
          )}
        </div>
        {file.sliced_for_model && (
          <div className="mt-1 text-xs text-bambu-gray flex items-center gap-1">
            <Printer className="w-3 h-3" />
            {file.sliced_for_model}
          </div>
        )}
        {file.print_count > 0 && (
          <div className="mt-1 text-xs text-bambu-green">
            {t('fileManager.printedCount', { count: file.print_count })}
          </div>
        )}
        {authEnabled && file.created_by_username && (
          <div className="mt-1 text-xs text-bambu-gray flex items-center gap-1">
            <User className="w-3 h-3" />
            {file.created_by_username}
          </div>
        )}
        {(file.tags?.length ?? 0) > 0 && (
          <div className="mt-2 flex flex-wrap gap-1" onClick={(e) => e.stopPropagation()}>
            {file.tags!.map((tg) => (
              <button
                key={tg.id}
                type="button"
                onClick={() => onTagClick?.(tg.id)}
                className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-full text-[10px] bg-bambu-green/10 text-bambu-green hover:bg-bambu-green/20 transition-colors max-w-full"
                title={tg.name}
              >
                <TagIcon className="w-2.5 h-2.5 flex-shrink-0" />
                <span className="truncate">{tg.name}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Actions - always visible on mobile, hover on desktop */}
      <div className={`absolute bottom-2 right-2 transition-opacity ${isMobile ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'}`} onClick={(e) => e.stopPropagation()}>
        <button
          onClick={() => setShowActions(!showActions)}
          className="p-1.5 rounded bg-bambu-dark-secondary/90 hover:bg-bambu-dark-tertiary"
        >
          <MoreVertical className="w-4 h-4 text-bambu-gray" />
        </button>
        {showActions && (
          <>
            <div className="fixed inset-0 z-10" onClick={() => setShowActions(false)} />
            <div className="absolute right-0 bottom-8 z-20 bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-xl py-1 min-w-[140px]">
              {onPrint && isSlicedFilename(file.filename) && (
                <button
                  className={`w-full px-3 py-1.5 text-left text-sm flex items-center gap-2 ${
                    hasPermission('queue:create') ? 'text-bambu-green hover:bg-bambu-dark' : 'text-bambu-gray cursor-not-allowed'
                  }`}
                  onClick={() => { if (hasPermission('queue:create')) { onPrint(file); setShowActions(false); } }}
                  disabled={!hasPermission('queue:create')}
                  title={!hasPermission('queue:create') ? t('fileManager.noPermissionAddToQueue') : undefined}
                >
                  <Printer className="w-3.5 h-3.5" />
                  {t('common.print')}
                </button>
              )}
              {onSlice && useSlicerApi && isSliceableFilename(file.filename) && (
                <button
                  className={`w-full px-3 py-1.5 text-left text-sm flex items-center gap-2 ${
                    hasPermission('library:upload') ? 'text-white hover:bg-bambu-dark' : 'text-bambu-gray cursor-not-allowed'
                  }`}
                  onClick={() => { if (hasPermission('library:upload')) { onSlice(file); setShowActions(false); } }}
                  disabled={!hasPermission('library:upload')}
                  title={!hasPermission('library:upload') ? t('fileManager.noPermissionSlice') : undefined}
                >
                  <Cog className="w-3.5 h-3.5" />
                  {t('slice.action')}
                </button>
              )}
              {onPreview3d && (file.file_type === '3mf' || file.file_type === 'gcode' || file.file_type === 'stl' || file.file_type === 'gcode.3mf') && (
                <button
                  className={`w-full px-3 py-1.5 text-left text-sm flex items-center gap-2 ${
                    hasPermission('library:read') ? 'text-white hover:bg-bambu-dark' : 'text-bambu-gray cursor-not-allowed'
                  }`}
                  onClick={() => { if (hasPermission('library:read')) { onPreview3d(file); setShowActions(false); } }}
                  disabled={!hasPermission('library:read')}
                  title={!hasPermission('library:read') ? 'You do not have permission to preview files' : undefined}
                >
                  <Box className="w-3.5 h-3.5" />
                  3D Preview
                </button>
              )}
              <button
                className={`w-full px-3 py-1.5 text-left text-sm flex items-center gap-2 ${
                  hasPermission('library:read') ? 'text-white hover:bg-bambu-dark' : 'text-bambu-gray cursor-not-allowed'
                }`}
                onClick={() => { if (hasPermission('library:read')) { onDownload(file.id); setShowActions(false); } }}
                disabled={!hasPermission('library:read')}
                title={!hasPermission('library:read') ? t('fileManager.noPermissionDownload') : undefined}
              >
                <Download className="w-3.5 h-3.5" />
                {t('common.download')}
              </button>
              {onRename && (
                <button
                  className={`w-full px-3 py-1.5 text-left text-sm flex items-center gap-2 ${
                    canModify('library', 'update', file.created_by_id) ? 'text-white hover:bg-bambu-dark' : 'text-bambu-gray cursor-not-allowed'
                  }`}
                  onClick={() => { if (canModify('library', 'update', file.created_by_id)) { onRename(file); setShowActions(false); } }}
                  disabled={!canModify('library', 'update', file.created_by_id)}
                  title={!canModify('library', 'update', file.created_by_id) ? t('fileManager.noPermissionRenameFile') : undefined}
                >
                  <Pencil className="w-3.5 h-3.5" />
                  {t('common.rename')}
                </button>
              )}
              {onGenerateThumbnail && file.file_type === 'stl' && (
                <button
                  className={`w-full px-3 py-1.5 text-left text-sm flex items-center gap-2 ${
                    canModify('library', 'update', file.created_by_id) ? 'text-white hover:bg-bambu-dark' : 'text-bambu-gray cursor-not-allowed'
                  }`}
                  onClick={() => { if (canModify('library', 'update', file.created_by_id)) { onGenerateThumbnail(file); setShowActions(false); } }}
                  disabled={!canModify('library', 'update', file.created_by_id)}
                  title={!canModify('library', 'update', file.created_by_id) ? t('fileManager.noPermissionGenerateThumbnail') : undefined}
                >
                  <Image className="w-3.5 h-3.5" />
                  {t('fileManager.generateThumbnail')}
                </button>
              )}
              <button
                className={`w-full px-3 py-1.5 text-left text-sm flex items-center gap-2 ${
                  canModify('library', 'delete', file.created_by_id) ? 'text-red-400 hover:bg-bambu-dark' : 'text-bambu-gray cursor-not-allowed'
                }`}
                onClick={() => { if (canModify('library', 'delete', file.created_by_id)) { onDelete(file.id); setShowActions(false); } }}
                disabled={!canModify('library', 'delete', file.created_by_id)}
                title={!canModify('library', 'delete', file.created_by_id) ? t('fileManager.noPermissionDeleteFile') : undefined}
              >
                <Trash2 className="w-3.5 h-3.5" />
                {t('common.delete')}
              </button>
            </div>
          </>
        )}
      </div>

      {/* Selection checkbox - always visible on mobile, hover on desktop */}
      <div className={`absolute top-2 left-2 w-5 h-5 rounded border-2 flex items-center justify-center transition-all ${
        isSelected
          ? 'bg-bambu-green border-bambu-green'
          : `border-white/30 bg-black/30 ${isMobile ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'}`
      }`}>
        {isSelected && <div className="w-2 h-2 bg-white rounded-sm" />}
      </div>
    </div>
  );
}

export function FileManagerPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { hasPermission, hasAnyPermission, canModify, authEnabled } = useAuth();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();

  // Read folder ID from URL query parameter
  const folderIdFromUrl = searchParams.get('folder');
  const initialFolderId = folderIdFromUrl ? parseInt(folderIdFromUrl, 10) : null;

  // State
  const [selectedFolderId, setSelectedFolderId] = useState<number | null>(initialFolderId);
  // Which top-level pseudo-view the sidebar shows when no specific folder is
  // selected: "internal" = files in Bambuddy's managed storage, "external" =
  // combined view across every linked external folder (#1621). Per-folder
  // selection bypasses this (selectedFolderId !== null disables the filter).
  const [topLevelView, setTopLevelView] = useState<'internal' | 'external'>('internal');
  const [selectedFiles, setSelectedFiles] = useState<number[]>([]);
  const [showNewFolderModal, setShowNewFolderModal] = useState(false);
  const [showExternalFolderModal, setShowExternalFolderModal] = useState(false);
  const [showMoveModal, setShowMoveModal] = useState(false);
  const [showUploadModal, setShowUploadModal] = useState(false);
  const [droppedFiles, setDroppedFiles] = useState<File[]>([]);
  const [showPurgeModal, setShowPurgeModal] = useState(false);
  // Tag UI state (#1268). selectedTagIds is the AND-style filter applied to
  // the listing; setting it bypasses folder scoping on the server so
  // "every toy" works regardless of which folder is currently selected.
  const [showTagsModal, setShowTagsModal] = useState(false);
  const [showBulkTagsModal, setShowBulkTagsModal] = useState(false);
  const [selectedTagIds, setSelectedTagIds] = useState<number[]>([]);
  const [linkFolder, setLinkFolder] = useState<LibraryFolderTree | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState<{ type: 'file' | 'folder' | 'bulk'; id: number; count?: number } | null>(null);
  const [printFile, setPrintFile] = useState<LibraryFileListItem | null>(null);
  const [sliceFile, setSliceFile] = useState<LibraryFileListItem | null>(null);
  const [renameItem, setRenameItem] = useState<{ type: 'file' | 'folder'; id: number; name: string } | null>(null);
  const [thumbnailVersions, setThumbnailVersions] = useState<Record<number, number>>({});
  const [viewerFile, setViewerFile] = useState<LibraryFileListItem | null>(null);
  const [viewMode, setViewMode] = useState<'grid' | 'list'>(() => {
    return (localStorage.getItem('library-view-mode') as 'grid' | 'list') || 'grid';
  });
  const [wrapFolderNames, setWrapFolderNames] = useState(() => {
    return localStorage.getItem('library-wrap-folders') === 'true';
  });
  const [collapseFoldersByDefault, setCollapseFoldersByDefault] = useState(() => {
    return localStorage.getItem('library-collapse-folders') === 'true';
  });
  // Folder tree sort (#1770). 'name' = alphabetical (the prior behaviour);
  // 'activity' = most recent file activity inside the folder first. Persisted
  // independently from the file-side sort so each can be tuned to taste.
  const [folderSortField, setFolderSortField] = useState<'name' | 'activity'>(() => {
    const saved = localStorage.getItem('library-folder-sort-field');
    return saved === 'activity' ? 'activity' : 'name';
  });
  const [folderSortDirection, setFolderSortDirection] = useState<'asc' | 'desc'>(() => {
    const saved = localStorage.getItem('library-folder-sort-direction');
    return saved === 'desc' ? 'desc' : 'asc';
  });

  // Resizable sidebar state
  const [sidebarWidth, setSidebarWidth] = useState(() => {
    const saved = localStorage.getItem('library-sidebar-width');
    return saved ? parseInt(saved, 10) : 256; // Default w-64 = 256px
  });
  const [isResizing, setIsResizing] = useState(false);
  const sidebarRef = useRef<HTMLDivElement>(null);

  // Handle sidebar resize
  useEffect(() => {
    if (!isResizing) return;

    // Prevent text selection during resize
    document.body.style.userSelect = 'none';
    document.body.style.cursor = 'col-resize';

    const handleMouseMove = (e: MouseEvent) => {
      if (!sidebarRef.current) return;
      const containerRect = sidebarRef.current.parentElement?.getBoundingClientRect();
      if (!containerRect) return;
      // Calculate new width based on mouse position relative to container
      const newWidth = e.clientX - containerRect.left;
      // Clamp between 200px and 500px
      const clampedWidth = Math.min(500, Math.max(200, newWidth));
      setSidebarWidth(clampedWidth);
    };

    const handleMouseUp = () => {
      setIsResizing(false);
      document.body.style.userSelect = '';
      document.body.style.cursor = '';
      // Save to localStorage
      localStorage.setItem('library-sidebar-width', String(sidebarWidth));
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);

    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
      document.body.style.userSelect = '';
      document.body.style.cursor = '';
    };
  }, [isResizing, sidebarWidth]);

  // Filter and sort state (persist sort preferences to localStorage)
  const [searchQuery, setSearchQuery] = useState('');
  const [filterType, setFilterType] = useState<string>('all');
  const [filterUsername, setFilterUsername] = useState('');
  const [sortField, setSortField] = useState<SortField>(() => {
    const saved = localStorage.getItem('library-sort-field');
    return (saved as SortField) || 'name';
  });
  const [sortDirection, setSortDirection] = useState<SortDirection>(() => {
    const saved = localStorage.getItem('library-sort-direction');
    return (saved as SortDirection) || 'asc';
  });

  // Mobile detection for touch-friendly UI
  const isMobile = useIsMobile();

  // Update selectedFolderId when URL parameter changes (e.g., navigating from Project or Archive page)
  useEffect(() => {
    const folderParam = searchParams.get('folder');
    if (folderParam) {
      const newFolderId = parseInt(folderParam, 10);
      setSelectedFolderId(newFolderId);
    }
  }, [searchParams]);

  // Queries
  const { data: settings } = useQuery({
    queryKey: ['settings'],
    queryFn: () => api.getSettings() as Promise<AppSettings>,
  });
  const { data: folders, isLoading: foldersLoading } = useQuery({
    queryKey: ['library-folders'],
    queryFn: () => api.getLibraryFolders(),
  });

  // Recursive folder tree sort (#1770). Applies the same comparator to the
  // top-level list AND to each level of `children`, so sort order is uniform
  // at every depth of nesting. When sorting by activity, the comparator falls
  // back to a created-at fallback for folders with no files (`latest_activity_at`
  // is null) so they stay grouped at the end / start of the bucket instead of
  // randomly interspersed.
  const sortedFolders = useMemo(() => {
    if (!folders) return folders;
    const sortLevel = (items: LibraryFolderTree[]): LibraryFolderTree[] => {
      const sorted = [...items].sort((a, b) => {
        let comparison = 0;
        if (folderSortField === 'name') {
          comparison = a.name.localeCompare(b.name);
        } else {
          // activity: newest first on 'desc', oldest first on 'asc'.
          // Folders with no activity timestamp sort to the end regardless
          // of direction so an empty folder doesn't elbow a recently-used one.
          const aTs = a.latest_activity_at ? new Date(a.latest_activity_at).getTime() : null;
          const bTs = b.latest_activity_at ? new Date(b.latest_activity_at).getTime() : null;
          if (aTs === null && bTs === null) {
            comparison = a.name.localeCompare(b.name);
          } else if (aTs === null) {
            return 1;
          } else if (bTs === null) {
            return -1;
          } else {
            comparison = aTs - bTs;
          }
        }
        return folderSortDirection === 'asc' ? comparison : -comparison;
      });
      return sorted.map((f) => ({ ...f, children: sortLevel(f.children) }));
    };
    return sortLevel(folders);
  }, [folders, folderSortField, folderSortDirection]);

  // Trash count for the header badge (#1008). Empty/error are silently treated
  // as zero so a broken trash endpoint doesn't break the File Manager.
  const { data: trashCount } = useQuery({
    queryKey: ['library-trash-count'],
    queryFn: async () => {
      try {
        const res = await api.listLibraryTrash(1, 0);
        return res.total;
      } catch {
        return 0;
      }
    },
    staleTime: 30_000,
  });

  // #1268: when a folder is selected and the user has typed a search query,
  // ask the server to expand the result to every descendant folder so the
  // client-side filter can match files in subfolders too. Without this the
  // listing is just the immediate children and "robot.3mf" two levels deep
  // is invisible from the parent. Only kicks in for folder-scoped views —
  // root and the internal/external pseudo-nodes already return the union.
  const searchExpandsSubfolders = selectedFolderId !== null && searchQuery.trim().length > 0;
  // The tag filter overrides folder scoping server-side (#1268 design call),
  // so the FE query key includes it as a peer of folder/topLevelView. Sorted
  // so the cache hits regardless of the order tags were toggled.
  const tagFilterKey = useMemo(() => [...selectedTagIds].sort((a, b) => a - b), [selectedTagIds]);
  // Tag catalog — needed to resolve names for the active-filter chip bar.
  // Cheap query, shared with LibraryTagsModal / BulkTagsPickerModal via the
  // same queryKey so they all invalidate together on tag CRUD.
  const { data: tagCatalog = [] } = useQuery({
    queryKey: ['library-tags'],
    queryFn: api.getLibraryTags,
  });
  const tagsById = useMemo(() => {
    const map = new Map<number, string>();
    for (const t of tagCatalog) map.set(t.id, t.name);
    return map;
  }, [tagCatalog]);
  // Prune the active filter when a tag is removed from the catalog so the
  // listing never stalls on a phantom id. Skipped while the catalog query is
  // still settling (empty array on first paint) — otherwise the user's filter
  // gets cleared the moment the page mounts.
  useEffect(() => {
    if (tagCatalog.length === 0) return;
    setSelectedTagIds((prev) => {
      const next = prev.filter((id) => tagsById.has(id));
      return next.length === prev.length ? prev : next;
    });
  }, [tagCatalog.length, tagsById]);

  const toggleTagFilter = useCallback((tagId: number) => {
    setSelectedTagIds((prev) =>
      prev.includes(tagId) ? prev.filter((id) => id !== tagId) : [...prev, tagId],
    );
  }, []);

  const { data: files, isLoading: filesLoading } = useQuery({
    queryKey: ['library-files', selectedFolderId, topLevelView, searchExpandsSubfolders, tagFilterKey],
    // When a specific folder is selected we list its contents directly; when
    // no folder is selected the topLevelView pseudo-node decides whether the
    // server scopes the result to internal-managed-storage files or to the
    // union of every external folder (#1621). include_root stays false so the
    // listing still descends into subfolders (regression guard from #1499).
    queryFn: () =>
      api.getLibraryFiles(
        selectedFolderId,
        false,
        undefined,
        selectedFolderId === null ? topLevelView : undefined,
        searchExpandsSubfolders,
        tagFilterKey,
      ),
  });

  const { data: stats } = useQuery({
    queryKey: ['library-stats'],
    queryFn: () => api.getLibraryStats(),
  });

  // Get users for the username filter autocomplete
  const { data: users } = useQuery({
    queryKey: ['users'],
    queryFn: () => api.getUsers(),
  });

  // Get unique file types for filter dropdown
  const fileTypes = useMemo(() => {
    if (!files) return [];
    const types = new Set(files.map((f) => f.file_type));
    return Array.from(types).sort();
  }, [files]);

  // Filter and sort files
  const filteredAndSortedFiles = useMemo(() => {
    if (!files) return [];

    let result = [...files];

    // Apply search filter
    if (searchQuery.trim()) {
      const query = searchQuery.toLowerCase();
      result = result.filter(
        (f) =>
          f.filename.toLowerCase().includes(query) ||
          (f.print_name && f.print_name.toLowerCase().includes(query))
      );
    }

    // Apply type filter
    if (filterType !== 'all') {
      result = result.filter((f) => f.file_type === filterType);
    }

    // Apply username filter
    if (filterUsername.trim()) {
      const query = filterUsername.toLowerCase();
      result = result.filter(
        (f) => f.created_by_username && f.created_by_username.toLowerCase().includes(query)
      );
    }

    // Apply sorting
    result.sort((a, b) => {
      let comparison = 0;
      switch (sortField) {
        case 'name':
          comparison = (a.print_name || a.filename).localeCompare(b.print_name || b.filename);
          break;
        case 'date':
          comparison = (parseUTCDate(a.created_at)?.getTime() ?? 0) - (parseUTCDate(b.created_at)?.getTime() ?? 0);
          break;
        case 'size':
          comparison = a.file_size - b.file_size;
          break;
        case 'type':
          comparison = a.file_type.localeCompare(b.file_type);
          break;
        case 'prints':
          comparison = a.print_count - b.print_count;
          break;
      }
      return sortDirection === 'asc' ? comparison : -comparison;
    });

    return result;
  }, [files, searchQuery, filterType, filterUsername, sortField, sortDirection]);

  // Check if disk space is low
  const isDiskSpaceLow = useMemo(() => {
    if (!stats || !settings) return false;
    const thresholdBytes = (settings.library_disk_warning_gb || 5) * 1024 * 1024 * 1024;
    return stats.disk_free_bytes < thresholdBytes;
  }, [stats, settings]);

  // Mutations
  const createFolderMutation = useMutation({
    mutationFn: (data: LibraryFolderCreate) => api.createLibraryFolder(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['library-folders'] });
      setShowNewFolderModal(false);
      showToast(t('fileManager.toast.folderCreated'), 'success');
    },
    onError: (error: Error) => showToast(error.message, 'error'),
  });

  const createExternalFolderMutation = useMutation({
    mutationFn: async (data: ExternalFolderCreate) => {
      const folder = await api.createExternalFolder(data);
      // Auto-scan after creation
      await api.scanExternalFolder(folder.id);
      return folder;
    },
    onSuccess: (folder) => {
      queryClient.invalidateQueries({ queryKey: ['library-folders'] });
      queryClient.invalidateQueries({ queryKey: ['library-files'] });
      queryClient.invalidateQueries({ queryKey: ['library-stats'] });
      setShowExternalFolderModal(false);
      setSelectedFolderId(folder.id);
      showToast(t('fileManager.toast.externalFolderLinked'), 'success');
    },
    onError: (error: Error) => showToast(error.message, 'error'),
  });

  const scanExternalFolderMutation = useMutation({
    mutationFn: (folderId: number) => api.scanExternalFolder(folderId),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['library-files'] });
      queryClient.invalidateQueries({ queryKey: ['library-folders'] });
      queryClient.invalidateQueries({ queryKey: ['library-stats'] });
      showToast(t('fileManager.toast.folderScanned', { added: result.added, removed: result.removed }), 'success');
    },
    onError: (error: Error) => showToast(error.message, 'error'),
  });

  const deleteFolderMutation = useMutation({
    mutationFn: (id: number) => api.deleteLibraryFolder(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['library-folders'] });
      queryClient.invalidateQueries({ queryKey: ['library-files'] });
      queryClient.invalidateQueries({ queryKey: ['library-stats'] });
      if (selectedFolderId === deleteConfirm?.id) {
        setSelectedFolderId(null);
      }
      setDeleteConfirm(null);
      showToast(t('fileManager.toast.folderDeleted'), 'success');
    },
    onError: (error: Error) => {
      setDeleteConfirm(null);
      showToast(error.message, 'error');
    },
  });

  const deleteFileMutation = useMutation({
    mutationFn: (id: number) => api.deleteLibraryFile(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['library-files'] });
      queryClient.invalidateQueries({ queryKey: ['library-folders'] });
      queryClient.invalidateQueries({ queryKey: ['library-stats'] });
      queryClient.invalidateQueries({ queryKey: ['library-trash-count'] });
      setSelectedFiles((prev) => prev.filter((id) => id !== deleteConfirm?.id));
      setDeleteConfirm(null);
      showToast(t('fileManager.toast.fileDeleted'), 'success');
    },
    onError: (error: Error) => {
      setDeleteConfirm(null);
      showToast(error.message, 'error');
    },
  });

  const bulkDeleteMutation = useMutation({
    mutationFn: (fileIds: number[]) => api.bulkDeleteLibrary(fileIds, []),
    onSuccess: (_, fileIds) => {
      queryClient.invalidateQueries({ queryKey: ['library-files'] });
      queryClient.invalidateQueries({ queryKey: ['library-folders'] });
      queryClient.invalidateQueries({ queryKey: ['library-stats'] });
      queryClient.invalidateQueries({ queryKey: ['library-trash-count'] });
      showToast(t('fileManager.toast.filesDeleted', { count: fileIds.length }), 'success');
      setSelectedFiles([]);
      setDeleteConfirm(null);
    },
    onError: (error: Error) => {
      setDeleteConfirm(null);
      showToast(error.message, 'error');
    },
  });

  const moveFilesMutation = useMutation({
    mutationFn: ({ fileIds, folderId }: { fileIds: number[]; folderId: number | null }) =>
      api.moveLibraryFiles(fileIds, folderId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['library-files'] });
      queryClient.invalidateQueries({ queryKey: ['library-folders'] });
      setSelectedFiles([]);
      setShowMoveModal(false);
      showToast(t('fileManager.toast.filesMoved'), 'success');
    },
    onError: (error: Error) => showToast(error.message, 'error'),
  });

  const updateFolderMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: LibraryFolderUpdate }) =>
      api.updateLibraryFolder(id, data),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['library-folders'] });
      // Invalidate project/archive folder queries so other pages see the update
      queryClient.invalidateQueries({ queryKey: ['project-folders'] });
      queryClient.invalidateQueries({ queryKey: ['archive-folders'] });
      setLinkFolder(null);
      const isUnlink = variables.data.project_id === 0 && variables.data.archive_id === 0;
      showToast(isUnlink ? t('fileManager.toast.folderUnlinked') : t('fileManager.toast.folderLinked'), 'success');
    },
    onError: (error: Error) => showToast(error.message, 'error'),
  });

  const renameFileMutation = useMutation({
    mutationFn: ({ id, filename }: { id: number; filename: string }) =>
      api.updateLibraryFile(id, { filename }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['library-files'] });
      setRenameItem(null);
      showToast(t('fileManager.toast.fileRenamed'), 'success');
    },
    onError: (error: Error) => {
      setRenameItem(null);
      showToast(error.message, 'error');
    },
  });

  const renameFolderMutation = useMutation({
    mutationFn: ({ id, name }: { id: number; name: string }) =>
      api.updateLibraryFolder(id, { name }),
    onSuccess: () => {
      // Invalidate both folders and files - files may display folder info
      queryClient.invalidateQueries({ queryKey: ['library-folders'] });
      queryClient.invalidateQueries({ queryKey: ['library-files'] });
      setRenameItem(null);
      showToast(t('fileManager.toast.folderRenamed'), 'success');
    },
    onError: (error: Error) => {
      setRenameItem(null);
      showToast(error.message, 'error');
    },
  });

  const batchThumbnailMutation = useMutation({
    mutationFn: () => api.batchGenerateStlThumbnails({ all_missing: true }),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['library-files'] });
      // Update thumbnail versions for cache busting
      if (result.succeeded > 0) {
        const now = Date.now();
        const newVersions: Record<number, number> = {};
        result.results.forEach((r) => {
          if (r.success) {
            newVersions[r.file_id] = now;
          }
        });
        setThumbnailVersions((prev) => ({ ...prev, ...newVersions }));
      }
      if (result.succeeded > 0 && result.failed === 0) {
        showToast(t('fileManager.toast.thumbnailsGenerated', { count: result.succeeded }), 'success');
      } else if (result.succeeded > 0 && result.failed > 0) {
        showToast(t('fileManager.toast.thumbnailsGeneratedPartial', { succeeded: result.succeeded, failed: result.failed }), 'success');
      } else if (result.processed === 0) {
        showToast(t('fileManager.toast.noStlMissingThumbnails'), 'info');
      } else {
        showToast(t('fileManager.toast.failedToGenerateThumbnails', { error: result.results[0]?.error || 'Unknown error' }), 'error');
      }
    },
    onError: (error: Error) => showToast(error.message, 'error'),
  });

  const singleThumbnailMutation = useMutation({
    mutationFn: (fileId: number) => api.batchGenerateStlThumbnails({ file_ids: [fileId] }),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['library-files'] });
      // Update thumbnail version for cache busting
      if (result.succeeded > 0) {
        const fileId = result.results[0]?.file_id;
        if (fileId) {
          setThumbnailVersions((prev) => ({ ...prev, [fileId]: Date.now() }));
        }
        showToast(t('fileManager.toast.thumbnailGenerated'), 'success');
      } else {
        showToast(t('fileManager.toast.failedToGenerateThumbnail', { error: result.results[0]?.error || 'Unknown error' }), 'error');
      }
    },
    onError: (error: Error) => showToast(error.message, 'error'),
  });

  // Helper to check if a file is sliced (printable)
  const isSlicedFile = useCallback((filename: string) => {
    const lower = filename.toLowerCase();
    return lower.endsWith('.gcode') || lower.includes('.gcode.');
  }, []);

  // Get sliced files from selection
  const selectedSlicedFiles = useMemo(() => {
    if (!files) return [];
    return files.filter(f => selectedFiles.includes(f.id) && isSlicedFile(f.filename));
  }, [files, selectedFiles, isSlicedFile]);

  // Handlers
  const handleFileSelect = useCallback((id: number) => {
    // Always toggle selection (multi-select by default)
    setSelectedFiles((prev) => {
      return prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id];
    });
  }, []);

  const handleSelectAll = useCallback(() => {
    if (filteredAndSortedFiles.length > 0) {
      setSelectedFiles(filteredAndSortedFiles.map((f) => f.id));
    }
  }, [filteredAndSortedFiles]);

  const handleDeselectAll = useCallback(() => {
    setSelectedFiles([]);
  }, []);

  const handleUploadComplete = () => {
    queryClient.invalidateQueries({ queryKey: ['library-files'] });
    queryClient.invalidateQueries({ queryKey: ['library-folders'] });
    queryClient.invalidateQueries({ queryKey: ['library-stats'] });
  };

  // Page-wide drag-and-drop upload (#1510). Disabled when the user lacks
  // library:upload so a non-uploader can't accidentally show the overlay,
  // and also disabled while the upload modal itself is open so drags into
  // the modal's own drop zone don't bubble up and flash the page overlay
  // behind it.
  const canUpload = hasPermission('library:upload');
  const { isDraggingOver, dragHandlers } = usePageFileDrop({
    disabled: !canUpload || showUploadModal,
    onFiles: (files) => {
      setDroppedFiles(files);
      setShowUploadModal(true);
    },
  });

  const handleDownload = (id: number) => {
    api.downloadLibraryFile(id).catch((err) => {
      console.error('Library file download failed:', err);
    });
  };

  const handleDeleteConfirm = () => {
    if (!deleteConfirm) return;
    if (deleteConfirm.type === 'file') {
      deleteFileMutation.mutate(deleteConfirm.id);
    } else if (deleteConfirm.type === 'folder') {
      deleteFolderMutation.mutate(deleteConfirm.id);
    } else if (deleteConfirm.type === 'bulk') {
      bulkDeleteMutation.mutate(selectedFiles);
    }
  };

  const isDeleting = deleteFolderMutation.isPending || deleteFileMutation.isPending || bulkDeleteMutation.isPending;

  const handleViewModeChange = (mode: 'grid' | 'list') => {
    setViewMode(mode);
    localStorage.setItem('library-view-mode', mode);
  };

  const isLoading = foldersLoading || filesLoading;

  // Find the selected folder in the tree to check external status
  const selectedFolder = useMemo(() => {
    if (!selectedFolderId || !folders) return null;
    const findFolder = (items: LibraryFolderTree[]): LibraryFolderTree | null => {
      for (const item of items) {
        if (item.id === selectedFolderId) return item;
        const found = findFolder(item.children);
        if (found) return found;
      }
      return null;
    };
    return findFolder(folders);
  }, [selectedFolderId, folders]);

  return (
    <div
      className="p-4 md:p-8 min-h-[calc(100vh-64px)] lg:h-[calc(100vh-64px)] flex flex-col relative"
      {...dragHandlers}
    >
      {/* Drag & Drop Overlay — page-wide file upload (#1510) */}
      {isDraggingOver && (
        <div className="fixed inset-0 z-50 bg-bambu-dark/90 flex items-center justify-center pointer-events-none">
          <div className="border-4 border-dashed border-bambu-green rounded-xl p-12 text-center">
            <Upload className="w-16 h-16 mx-auto mb-4 text-bambu-green" />
            <p className="text-2xl font-semibold text-white mb-2">{t('fileManager.dropFilesHere')}</p>
            <p className="text-bambu-gray">{t('fileManager.releaseToUpload')}</p>
          </div>
        </div>
      )}

      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-3">
            <FolderOpen className="w-7 h-7 text-bambu-green" />
            {t('fileManager.title')}
          </h1>
          <p className="text-bambu-gray mt-1">
            {t('fileManager.subtitle')}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {/* View mode toggle */}
          <div className="flex items-center bg-bambu-dark rounded-lg p-1">
            <button
              onClick={() => handleViewModeChange('grid')}
              className={`p-1.5 rounded transition-colors ${
                viewMode === 'grid' ? 'bg-bambu-dark-secondary text-white' : 'text-bambu-gray hover:text-white'
              }`}
              title={t('fileManager.gridView')}
            >
              <LayoutGrid className="w-4 h-4" />
            </button>
            <button
              onClick={() => handleViewModeChange('list')}
              className={`p-1.5 rounded transition-colors ${
                viewMode === 'list' ? 'bg-bambu-dark-secondary text-white' : 'text-bambu-gray hover:text-white'
              }`}
              title={t('fileManager.listView')}
            >
              <List className="w-4 h-4" />
            </button>
          </div>
          <Button
            variant="secondary"
            onClick={() => batchThumbnailMutation.mutate()}
            disabled={batchThumbnailMutation.isPending || !hasAnyPermission('library:update_own', 'library:update_all')}
            title={!hasAnyPermission('library:update_own', 'library:update_all') ? t('fileManager.noPermissionGenerateThumbnail') : t('fileManager.generateThumbnailsForMissing')}
          >
            {batchThumbnailMutation.isPending ? (
              <Loader2 className="w-4 h-4 mr-2 animate-spin" />
            ) : (
              <Image className="w-4 h-4 mr-2" />
            )}
            {t('fileManager.generateThumbnails')}
          </Button>
          <Button
            variant="secondary"
            onClick={() => setShowExternalFolderModal(true)}
            disabled={!hasPermission('library:upload')}
            title={!hasPermission('library:upload') ? t('fileManager.noPermissionCreateFolder') : t('fileManager.linkExternalFolder')}
          >
            <FolderSymlink className="w-4 h-4 mr-2" />
            {t('fileManager.linkExternal')}
          </Button>
          <Button
            variant="secondary"
            onClick={() => setShowNewFolderModal(true)}
            disabled={!hasPermission('library:upload')}
            title={!hasPermission('library:upload') ? t('fileManager.noPermissionCreateFolder') : undefined}
          >
            <FolderPlus className="w-4 h-4 mr-2" />
            {t('fileManager.newFolder')}
          </Button>
          <Button
            variant="secondary"
            onClick={() => setShowTagsModal(true)}
            title={t('fileManager.tags.manageTitle')}
          >
            <TagIcon className="w-4 h-4 mr-2" />
            {t('fileManager.tags.manage')}
          </Button>
          {hasPermission('library:purge') && (
            <Button
              variant="secondary"
              onClick={() => setShowPurgeModal(true)}
              title={t('libraryPurge.headerTooltip')}
            >
              <Trash2 className="w-4 h-4 mr-2" />
              {t('libraryPurge.headerButton')}
            </Button>
          )}
          {(hasAnyPermission('library:delete_own', 'library:delete_all')) && (
            <Link
              to="/files/trash"
              className="inline-flex items-center px-3 py-1.5 text-sm rounded bg-bambu-dark-secondary text-bambu-gray hover:text-white hover:bg-bambu-dark transition-colors"
              title={t('libraryTrash.headerTooltip')}
            >
              <Trash2 className="w-4 h-4 mr-2" />
              {t('libraryTrash.headerButton')}
              {typeof trashCount === 'number' && trashCount > 0 && (
                <span className="ml-1.5 px-1.5 py-0.5 text-xs rounded-full bg-bambu-green/20 text-bambu-green">
                  {trashCount}
                </span>
              )}
            </Link>
          )}
          <Button
            onClick={() => setShowUploadModal(true)}
            disabled={!hasPermission('library:upload')}
            title={!hasPermission('library:upload') ? t('fileManager.noPermissionUpload') : undefined}
          >
            <Upload className="w-4 h-4 mr-2" />
            {t('common.upload')}
          </Button>
        </div>
      </div>

      {/* Disk space warning */}
      {isDiskSpaceLow && stats && settings && (
        <div className="flex items-center gap-3 mb-4 p-3 bg-amber-500/10 border border-amber-500/30 rounded-lg">
          <AlertTriangle className="w-5 h-5 text-amber-500 flex-shrink-0" />
          <div className="flex-1">
            <p className="text-sm text-amber-500 font-medium">{t('fileManager.lowDiskSpaceWarning')}</p>
            <p className="text-xs text-amber-500/80">
              {t('fileManager.lowDiskSpaceDetails', { free: formatFileSize(stats.disk_free_bytes), total: formatFileSize(stats.disk_total_bytes), threshold: settings.library_disk_warning_gb })}
            </p>
          </div>
        </div>
      )}

      {/* Stats bar */}
      {stats && (
        <div className="flex flex-wrap items-center gap-3 sm:gap-6 mb-6 p-3 bg-bambu-dark-secondary rounded-lg border border-bambu-dark-tertiary">
          <div className="flex items-center gap-2 text-sm">
            <File className="w-4 h-4 text-bambu-green" />
            <span className="text-bambu-gray">{t('fileManager.files')}:</span>
            <span className="text-white font-medium">{stats.total_files}</span>
          </div>
          <div className="flex items-center gap-2 text-sm">
            <FolderOpen className="w-4 h-4 text-blue-400" />
            <span className="text-bambu-gray">{t('fileManager.folders')}:</span>
            <span className="text-white font-medium">{stats.total_folders}</span>
          </div>
          <div className="flex items-center gap-2 text-sm">
            <HardDrive className="w-4 h-4 text-amber-400" />
            <span className="text-bambu-gray">{t('fileManager.size')}:</span>
            <span className="text-white font-medium">{formatFileSize(stats.total_size_bytes)}</span>
          </div>
          <div className="flex items-center gap-2 text-sm sm:ml-auto">
            <span className="text-bambu-gray">{t('fileManager.free')}:</span>
            <span className={`font-medium ${isDiskSpaceLow ? 'text-amber-500' : 'text-white'}`}>
              {formatFileSize(stats.disk_free_bytes)}
            </span>
          </div>
        </div>
      )}

      {/* Main content */}
      <div className="flex-1 flex flex-col lg:flex-row gap-4 lg:gap-6 min-h-0">
        {/* Mobile folder selector */}
        <div className="lg:hidden">
          <select
            value={selectedFolderId !== null ? String(selectedFolderId) : `__top:${topLevelView}`}
            onChange={(e) => {
              const v = e.target.value;
              if (v.startsWith('__top:')) {
                setSelectedFolderId(null);
                setTopLevelView(v.slice('__top:'.length) as 'internal' | 'external');
              } else {
                setSelectedFolderId(parseInt(v, 10));
              }
            }}
            className="w-full bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg px-3 py-2.5 text-white focus:outline-none focus:border-bambu-green"
          >
            <option value="__top:internal">📁 {t('fileManager.allFiles')}</option>
            {folders?.some((f) => f.is_external) && (
              <option value="__top:external">🔗 {t('fileManager.allExternal')}</option>
            )}
            {sortedFolders && (() => {
              // Flatten folder tree for mobile selector
              const flattenFolders = (items: LibraryFolderTree[], depth = 0): { id: number; name: string; fileCount: number; depth: number }[] => {
                const result: { id: number; name: string; fileCount: number; depth: number }[] = [];
                for (const item of items) {
                  result.push({ id: item.id, name: item.name, fileCount: item.file_count, depth });
                  if (item.children.length > 0) {
                    result.push(...flattenFolders(item.children, depth + 1));
                  }
                }
                return result;
              };
              return flattenFolders(sortedFolders).map((folder) => (
                <option key={folder.id} value={folder.id}>
                  {'│ '.repeat(folder.depth)}📂 {folder.name} {folder.fileCount > 0 ? `(${folder.fileCount})` : ''}
                </option>
              ));
            })()}
          </select>
        </div>

        {/* Folder sidebar - resizable, hidden on mobile */}
        <div
          ref={sidebarRef}
          className="hidden lg:flex flex-shrink-0 bg-bambu-dark-secondary rounded-lg border border-bambu-dark-tertiary overflow-hidden flex-col relative"
          style={{ width: `${sidebarWidth}px` }}
        >
          {/* Resize handle - drag to resize, double-click to reset */}
          <div
            className={`absolute right-0 top-0 bottom-0 w-1.5 cursor-col-resize z-10 group/resize flex items-center justify-center transition-colors ${
              isResizing ? 'bg-bambu-green' : 'hover:bg-bambu-green/50'
            }`}
            onMouseDown={(e) => {
              e.preventDefault();
              setIsResizing(true);
            }}
            onDoubleClick={() => {
              setSidebarWidth(256); // Reset to default w-64
              localStorage.setItem('library-sidebar-width', '256');
            }}
            title={t('fileManager.dragToResizeTooltip')}
          >
            {/* Grip dots */}
            <div className={`flex flex-col gap-1 opacity-0 group-hover/resize:opacity-100 transition-opacity ${isResizing ? 'opacity-100' : ''}`}>
              <div className="w-0.5 h-0.5 rounded-full bg-white/70" />
              <div className="w-0.5 h-0.5 rounded-full bg-white/70" />
              <div className="w-0.5 h-0.5 rounded-full bg-white/70" />
            </div>
          </div>
          <div className="p-3 border-b border-bambu-dark-tertiary flex items-center justify-between">
            <h2 className="text-sm font-medium text-white">{t('fileManager.folders')}</h2>
            <div className="flex items-center gap-1">
              {/* Folder tree sort (#1770). Dropdown drives the comparator;
                  direction button flips asc/desc. Both persist to localStorage
                  on change so the choice survives reloads. */}
              <select
                value={folderSortField}
                onChange={(e) => {
                  const v = e.target.value === 'activity' ? 'activity' : 'name';
                  setFolderSortField(v);
                  localStorage.setItem('library-folder-sort-field', v);
                }}
                className="text-xs px-1 py-0.5 rounded bg-bambu-dark border border-bambu-dark-tertiary text-bambu-gray focus:outline-none focus:border-bambu-green"
                title={t('fileManager.folderSort')}
                aria-label={t('fileManager.folderSort')}
              >
                <option value="name">{t('fileManager.folderSortByName')}</option>
                <option value="activity">{t('fileManager.folderSortByActivity')}</option>
              </select>
              <button
                onClick={() => {
                  const newValue = folderSortDirection === 'asc' ? 'desc' : 'asc';
                  setFolderSortDirection(newValue);
                  localStorage.setItem('library-folder-sort-direction', newValue);
                }}
                className="text-bambu-gray hover:text-white hover:bg-bambu-dark p-1 rounded transition-colors"
                title={folderSortDirection === 'asc' ? t('fileManager.ascending') : t('fileManager.descending')}
                aria-label={folderSortDirection === 'asc' ? t('fileManager.ascending') : t('fileManager.descending')}
              >
                {folderSortDirection === 'asc' ? <SortAsc className="w-3.5 h-3.5" /> : <SortDesc className="w-3.5 h-3.5" />}
              </button>
              <button
                onClick={() => {
                  const newValue = !collapseFoldersByDefault;
                  setCollapseFoldersByDefault(newValue);
                  localStorage.setItem('library-collapse-folders', String(newValue));
                }}
                className={`text-xs px-1.5 py-0.5 rounded transition-colors ${
                  collapseFoldersByDefault
                    ? 'bg-bambu-green/20 text-bambu-green'
                    : 'text-bambu-gray hover:text-white hover:bg-bambu-dark'
                }`}
                title={collapseFoldersByDefault ? t('fileManager.expandFoldersByDefault') : t('fileManager.collapseFoldersByDefault')}
              >
                {t('fileManager.collapse')}
              </button>
              <button
                onClick={() => {
                  const newValue = !wrapFolderNames;
                  setWrapFolderNames(newValue);
                  localStorage.setItem('library-wrap-folders', String(newValue));
                }}
                className={`text-xs px-1.5 py-0.5 rounded transition-colors ${
                  wrapFolderNames
                    ? 'bg-bambu-green/20 text-bambu-green'
                    : 'text-bambu-gray hover:text-white hover:bg-bambu-dark'
                }`}
                title={wrapFolderNames ? t('fileManager.disableTextWrapping') : t('fileManager.enableTextWrapping')}
              >
                {t('fileManager.wrap')}
              </button>
            </div>
          </div>
          <div className="flex-1 overflow-y-auto p-2">
            {/* All Files = the user's own uploaded / managed-storage files
                only. External folders are surfaced separately below to keep
                a linked NAS from drowning the user's own uploads (#1621). */}
            <div
              className={`flex items-center gap-2 px-2 py-1.5 rounded cursor-pointer transition-colors ${
                selectedFolderId === null && topLevelView === 'internal'
                  ? 'bg-bambu-green/20 text-bambu-green'
                  : 'hover:bg-bambu-dark text-white'
              }`}
              onClick={() => {
                setSelectedFolderId(null);
                setTopLevelView('internal');
              }}
            >
              <FileBox className="w-4 h-4" />
              <span className="text-sm">{t('fileManager.allFiles')}</span>
            </div>

            {/* External (combined) — only shown when at least one external
                folder is linked. Single folder users don't need a combined
                view; clicking the individual folder is just as fast. */}
            {folders?.some((f) => f.is_external) && (
              <div
                className={`flex items-center gap-2 px-2 py-1.5 rounded cursor-pointer transition-colors ${
                  selectedFolderId === null && topLevelView === 'external'
                    ? 'bg-bambu-green/20 text-bambu-green'
                    : 'hover:bg-bambu-dark text-white'
                }`}
                onClick={() => {
                  setSelectedFolderId(null);
                  setTopLevelView('external');
                }}
              >
                <FolderSymlink className="w-4 h-4 text-purple-400" />
                <span className="text-sm">{t('fileManager.allExternal')}</span>
              </div>
            )}

            {/* Folder tree — re-key on the collapse toggle so flipping it
                remounts every FolderTreeItem, which re-reads defaultExpanded
                and makes the preference take effect immediately. */}
            {sortedFolders?.map((folder) => (
              <FolderTreeItem
                key={`${folder.id}-${collapseFoldersByDefault ? 'c' : 'e'}`}
                folder={folder}
                selectedFolderId={selectedFolderId}
                onSelect={setSelectedFolderId}
                onDelete={(id) => setDeleteConfirm({ type: 'folder', id })}
                onLink={setLinkFolder}
                onRename={(f) => setRenameItem({ type: 'folder', id: f.id, name: f.name })}
                wrapNames={wrapFolderNames}
                defaultExpanded={!collapseFoldersByDefault}
                hasPermission={hasPermission}
                t={t}
              />
            ))}
          </div>
        </div>

        {/* Files area */}
        <div className="flex-1 flex flex-col min-w-0 min-h-0">
          {/* Markdown description panel (#1268) — auto-hides if the folder
              has no README/description.md so non-users pay no UI cost. */}
          {selectedFolderId !== null && <FolderReadmePanel folderId={selectedFolderId} />}
          {/* Tag filter rail (#1268). Lists every catalog tag as a togglable
              chip — active chips are filled green and show an X, inactive
              chips are outlined and toggle ON when clicked. Clicking an active
              chip removes it from the filter. Hidden entirely when the
              catalog is empty so brand-new installs don't see a stray rail. */}
          {tagCatalog.length > 0 && (
            <div className="mb-3 flex flex-wrap items-center gap-2 p-2 sm:p-3 bg-bambu-dark-secondary rounded-lg border border-bambu-dark-tertiary">
              <span className="text-xs text-bambu-gray font-medium shrink-0">
                {t('fileManager.tags.filterLabel')}
              </span>
              {tagCatalog.map((tg) => {
                const active = selectedTagIds.includes(tg.id);
                return (
                  <button
                    key={tg.id}
                    type="button"
                    onClick={() => toggleTagFilter(tg.id)}
                    className={
                      active
                        ? 'inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs bg-bambu-green/20 text-bambu-green border border-bambu-green/40 hover:bg-bambu-green/30 transition-colors'
                        : 'inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs bg-bambu-dark text-bambu-gray border border-bambu-dark-tertiary hover:text-white hover:border-bambu-green/40 transition-colors'
                    }
                    title={tg.name}
                  >
                    <TagIcon className="w-3 h-3" />
                    <span>{tg.name}</span>
                    {active && <X className="w-3 h-3" />}
                  </button>
                );
              })}
              {selectedTagIds.length > 0 && (
                <button
                  type="button"
                  onClick={() => setSelectedTagIds([])}
                  className="ml-auto text-xs text-bambu-gray hover:text-white shrink-0"
                >
                  {t('fileManager.tags.clearAll')}
                </button>
              )}
            </div>
          )}
          {/* External folder info bar */}
          {selectedFolder?.is_external && (
            <div className="flex items-center gap-3 mb-4 p-3 bg-purple-500/10 border border-purple-500/30 rounded-lg">
              <FolderSymlink className="w-5 h-5 text-purple-400 flex-shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-purple-300">{t('fileManager.externalFolder')}</span>
                  {selectedFolder.external_readonly && (
                    <span className="text-xs px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-400 flex items-center gap-1">
                      <Lock className="w-3 h-3" />
                      {t('fileManager.readOnly')}
                    </span>
                  )}
                </div>
                <p className="text-xs text-bambu-gray truncate font-mono" title={selectedFolder.external_path || ''}>
                  {selectedFolder.external_path}
                </p>
              </div>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => selectedFolderId && scanExternalFolderMutation.mutate(selectedFolderId)}
                disabled={scanExternalFolderMutation.isPending}
                title={t('fileManager.scanFolder')}
              >
                {scanExternalFolderMutation.isPending ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <RefreshCw className="w-4 h-4" />
                )}
                <span className="ml-1.5">{t('fileManager.scanFolder')}</span>
              </Button>
            </div>
          )}
          {/* Search, Filter, Sort toolbar - sticky on mobile for easier access */}
          {files && files.length > 0 && (
            <div className="flex flex-wrap items-center gap-2 sm:gap-3 mb-4 p-2 sm:p-3 bg-bambu-dark-secondary rounded-lg border border-bambu-dark-tertiary sticky top-0 z-10 lg:static">
              {/* Search */}
              <div className="relative w-full sm:w-auto sm:flex-1 sm:max-w-xs">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray" />
                <input
                  type="text"
                  placeholder={t('fileManager.searchFiles')}
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="w-full pl-9 pr-3 py-1.5 bg-bambu-dark border border-bambu-dark-tertiary rounded text-sm text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green"
                />
                {searchExpandsSubfolders && (
                  <span
                    className="absolute -bottom-4 left-0 text-[10px] text-bambu-gray whitespace-nowrap"
                    title={t('fileManager.searchSubfoldersHint')}
                  >
                    {t('fileManager.searchSubfoldersHint')}
                  </span>
                )}
              </div>

              {/* Type filter */}
              <div className="flex items-center gap-2">
                <Filter className="w-4 h-4 text-bambu-gray hidden sm:block" />
                <select
                  value={filterType}
                  onChange={(e) => setFilterType(e.target.value)}
                  className="bg-bambu-dark border border-bambu-dark-tertiary rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-bambu-green"
                >
                  <option value="all">{t('fileManager.allTypes')}</option>
                  {fileTypes.map((type) => (
                    <option key={type} value={type}>
                      {type.toUpperCase()}
                    </option>
                  ))}
                </select>
              </div>

              {/* Username filter with autocomplete - only show when auth is enabled */}
              {authEnabled && (
                <div className="relative">
                  <input
                    type="text"
                    placeholder={t('fileManager.filterByUser', { defaultValue: 'Filter by user' })}
                    value={filterUsername}
                    onChange={(e) => setFilterUsername(e.target.value)}
                    list="usernames-list"
                    className={`w-32 sm:w-40 px-2 py-1.5 bg-bambu-dark border border-bambu-dark-tertiary rounded text-sm text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green ${filterUsername ? 'pr-7' : ''}`}
                    style={filterUsername ? { WebkitAppearance: 'none', MozAppearance: 'textfield' } : undefined}
                  />
                  {filterUsername && (
                    <button
                      onClick={() => setFilterUsername('')}
                      className="absolute right-2 top-1/2 -translate-y-1/2 text-bambu-gray hover:text-white z-10"
                    >
                      <X className="w-3 h-3" />
                    </button>
                  )}
                  <datalist id="usernames-list">
                    {users?.map((user) => (
                      <option key={user.id} value={user.username} />
                    ))}
                  </datalist>
                </div>
              )}

              {/* Sort */}
              <div className="flex items-center gap-2">
                <select
                  value={sortField}
                  onChange={(e) => {
                    const newField = e.target.value as SortField;
                    setSortField(newField);
                    localStorage.setItem('library-sort-field', newField);
                  }}
                  className="bg-bambu-dark border border-bambu-dark-tertiary rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-bambu-green"
                >
                  <option value="name">{t('common.name')}</option>
                  <option value="date">{t('common.date')}</option>
                  <option value="size">{t('fileManager.size')}</option>
                  <option value="type">{t('common.type')}</option>
                  <option value="prints">{t('fileManager.prints')}</option>
                </select>
                <button
                  onClick={() => setSortDirection((d) => {
                    const newDir = d === 'asc' ? 'desc' : 'asc';
                    localStorage.setItem('library-sort-direction', newDir);
                    return newDir;
                  })}
                  className="p-1.5 rounded bg-bambu-dark border border-bambu-dark-tertiary hover:border-bambu-green transition-colors"
                  title={sortDirection === 'asc' ? t('fileManager.ascending') : t('fileManager.descending')}
                >
                  {sortDirection === 'asc' ? (
                    <SortAsc className="w-4 h-4 text-white" />
                  ) : (
                    <SortDesc className="w-4 h-4 text-white" />
                  )}
                </button>
              </div>

              {/* Results count */}
              {(searchQuery || filterType !== 'all' || filterUsername) && (
                <span className="text-sm text-bambu-gray hidden sm:inline">
                  {t('fileManager.resultsCount', { showing: filteredAndSortedFiles.length, total: files.length })}
                </span>
              )}
            </div>
          )}

          {/* Selection toolbar - sticky on mobile below search bar */}
          {filteredAndSortedFiles.length > 0 && (
            <div className="flex flex-wrap items-center gap-2 mb-4 p-2 bg-bambu-dark-secondary rounded-lg border border-bambu-dark-tertiary sticky top-[52px] z-10 lg:static">
              {/* Select all / Deselect all */}
              {selectedFiles.length === filteredAndSortedFiles.length && selectedFiles.length > 0 ? (
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={handleDeselectAll}
                >
                  <Square className="w-4 h-4 sm:mr-1" />
                  <span className="hidden sm:inline">{t('fileManager.deselectAll')}</span>
                </Button>
              ) : (
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={handleSelectAll}
                >
                  <CheckSquare className="w-4 h-4 sm:mr-1" />
                  <span className="hidden sm:inline">{t('fileManager.selectAll')}</span>
                </Button>
              )}

              {selectedFiles.length > 0 && (
                <>
                  <span className="text-sm text-bambu-gray ml-2">
                    {t('fileManager.selected', { count: selectedFiles.length })}
                  </span>
                  <div className="hidden sm:block flex-1" />
                  <div className="w-full sm:w-auto flex flex-wrap items-center gap-2 mt-2 sm:mt-0">
                    {selectedSlicedFiles.length === 1 && (
                      <Button
                        variant="primary"
                        size="sm"
                        onClick={() => setPrintFile(selectedSlicedFiles[0])}
                        disabled={!hasPermission('queue:create')}
                        title={!hasPermission('queue:create') ? t('fileManager.noPermissionAddToQueue') : undefined}
                      >
                        <Printer className="w-4 h-4 sm:mr-1" />
                        <span className="hidden sm:inline">{t('common.print')}</span>
                      </Button>
                    )}
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => setShowMoveModal(true)}
                      disabled={!hasAnyPermission('library:update_own', 'library:update_all')}
                      title={!hasAnyPermission('library:update_own', 'library:update_all') ? t('fileManager.noPermissionMoveFiles') : undefined}
                    >
                      <MoveRight className="w-4 h-4 sm:mr-1" />
                      <span className="hidden sm:inline">{t('common.move')}</span>
                    </Button>
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => setShowBulkTagsModal(true)}
                      disabled={!hasAnyPermission('library:update_own', 'library:update_all')}
                      title={!hasAnyPermission('library:update_own', 'library:update_all') ? t('fileManager.tags.noPermission') : t('fileManager.tags.bulkTooltip')}
                    >
                      <TagIcon className="w-4 h-4 sm:mr-1" />
                      <span className="hidden sm:inline">{t('fileManager.tags.tagAction')}</span>
                    </Button>
                    <Button
                      variant="danger"
                      size="sm"
                      onClick={() => {
                        if (selectedFiles.length === 1) {
                          setDeleteConfirm({ type: 'file', id: selectedFiles[0] });
                        } else {
                          setDeleteConfirm({ type: 'bulk', id: 0, count: selectedFiles.length });
                        }
                      }}
                      disabled={!hasAnyPermission('library:delete_own', 'library:delete_all')}
                      title={!hasAnyPermission('library:delete_own', 'library:delete_all') ? t('fileManager.noPermissionDeleteFiles') : undefined}
                    >
                      <Trash2 className="w-4 h-4 sm:mr-1" />
                      <span className="hidden sm:inline">{t('common.delete')}</span>
                    </Button>
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={handleDeselectAll}
                    >
                      <X className="w-4 h-4 sm:mr-1" />
                      <span className="hidden sm:inline">{t('common.clear')}</span>
                    </Button>
                  </div>
                </>
              )}
            </div>
          )}

          {/* File grid/list */}
          {isLoading ? (
            <div className="flex-1 flex items-center justify-center">
              <div className="flex flex-col items-center gap-3">
                <Loader2 className="w-8 h-8 animate-spin text-bambu-green" />
                <p className="text-sm text-bambu-gray">{t('fileManager.loadingFiles')}</p>
              </div>
            </div>
          ) : files?.length === 0 ? (
            <div className="flex-1 flex flex-col items-center justify-center">
              <div className="p-4 bg-bambu-dark rounded-2xl mb-4">
                <FileBox className="w-12 h-12 text-bambu-gray/50" />
              </div>
              <h3 className="text-lg font-medium text-white mb-2">
                {selectedFolderId !== null
                  ? t('fileManager.folderIsEmpty')
                  : topLevelView === 'external'
                    ? t('fileManager.externalIsEmpty')
                    : t('fileManager.noFilesYet')}
              </h3>
              <p className="text-bambu-gray text-center max-w-md mb-6">
                {selectedFolderId !== null
                  ? t('fileManager.folderEmptyDescription')
                  : topLevelView === 'external'
                    ? t('fileManager.externalEmptyDescription')
                    : t('fileManager.noFilesDescription')}
              </p>
              <Button
                onClick={() => setShowUploadModal(true)}
                disabled={!hasPermission('library:upload')}
                title={!hasPermission('library:upload') ? t('fileManager.noPermissionUpload') : undefined}
              >
                <Plus className="w-4 h-4 mr-2" />
                {t('fileManager.uploadFiles')}
              </Button>
            </div>
          ) : filteredAndSortedFiles.length === 0 ? (
            <div className="flex-1 flex flex-col items-center justify-center">
              <div className="p-4 bg-bambu-dark rounded-2xl mb-4">
                <Search className="w-12 h-12 text-bambu-gray/50" />
              </div>
              <h3 className="text-lg font-medium text-white mb-2">{t('fileManager.noMatchingFiles')}</h3>
              <p className="text-bambu-gray text-center max-w-md mb-6">
                {t('fileManager.noMatchingFilesDescription')}
              </p>
              <Button variant="secondary" onClick={() => { setSearchQuery(''); setFilterType('all'); }}>
                {t('fileManager.clearFilters')}
              </Button>
            </div>
          ) : viewMode === 'grid' ? (
            <div className="flex-1 lg:overflow-y-auto">
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6 gap-4">
                {filteredAndSortedFiles.map((file) => (
                  <FileCard
                    key={file.id}
                    file={file}
                    isSelected={selectedFiles.includes(file.id)}
                    isMobile={isMobile}
                    t={t}
                    onSelect={handleFileSelect}
                    onDelete={(id) => setDeleteConfirm({ type: 'file', id })}
                    onDownload={handleDownload}
                    onPrint={setPrintFile}
                    onSlice={setSliceFile}
                    useSlicerApi={settings?.use_slicer_api ?? false}
                    onPreview3d={(f) => {
                      // Sliced files (.gcode / .gcode.3mf) open the same
                      // full-page gcode viewer the archive card uses, so
                      // the two paths feel consistent. STL / source 3MF
                      // continue to use the in-app 3D model viewer modal.
                      if (isSlicedFilename(f.filename)) {
                        navigate(`/gcode-viewer?library_file=${f.id}`);
                      } else {
                        setViewerFile(f);
                      }
                    }}
                    onRename={(f) => setRenameItem({ type: 'file', id: f.id, name: f.filename })}
                    onGenerateThumbnail={(f) => singleThumbnailMutation.mutate(f.id)}
                    onTagClick={toggleTagFilter}
                    thumbnailVersion={thumbnailVersions[file.id]}
                    hasPermission={hasPermission}
                    canModify={canModify}
                    authEnabled={authEnabled}
                  />
                ))}
              </div>
            </div>
          ) : (
            <div className="flex-1 lg:overflow-y-auto">
              {/* The wrapper has overflow-x-auto so a narrow viewport scrolls
                  horizontally instead of clipping the actions column off the
                  right edge. The previous `overflow-hidden` was there for the
                  rounded corners but also swallowed any content the actions
                  column couldn't fit (#1325 follow-up reported in chat). */}
              <div className="bg-bambu-dark-secondary rounded-lg border border-bambu-dark-tertiary overflow-x-auto">
                {/* List header - hidden on mobile, show simplified on small screens.
                    Trailing actions column is fixed at 220px (sliced 3MF = 7 icons
                    ~220px). It used to be `min-content`, but header + body are sibling
                    grids that compute `min-content` independently — the header's empty
                    trailing div resolved to 0px, leaving body columns shifted left of
                    their headers. Fixed width keeps header and body in lockstep. */}
                <div className={`hidden sm:grid ${authEnabled ? 'grid-cols-[auto_1fr_120px_100px_100px_100px_minmax(0,200px)_220px]' : 'grid-cols-[auto_1fr_100px_100px_100px_minmax(0,200px)_220px]'} gap-4 px-4 py-2 bg-bambu-dark-secondary border-b border-bambu-dark-tertiary text-xs text-bambu-gray font-medium`}>
                  <div className="w-6" />
                  <div>{t('common.name')}</div>
                  {authEnabled && <div>{t('fileManager.uploadedBy', { defaultValue: 'Uploaded By' })}</div>}
                  <div>{t('common.type')}</div>
                  <div>{t('fileManager.size')}</div>
                  <div>{t('fileManager.prints')}</div>
                  <div>{t('fileManager.tags.title')}</div>
                  <div />
                </div>
                {/* List rows */}
                {filteredAndSortedFiles.map((file) => (
                  <div
                    key={file.id}
                    className={`grid ${authEnabled ? 'grid-cols-[auto_1fr_120px_100px_100px_100px_minmax(0,200px)_220px]' : 'grid-cols-[auto_1fr_100px_100px_100px_minmax(0,200px)_220px]'} gap-4 px-4 py-3 items-center border-b border-bambu-dark-tertiary last:border-b-0 cursor-pointer hover:bg-bambu-dark/50 transition-colors ${
                      selectedFiles.includes(file.id) ? 'bg-bambu-green/10' : ''
                    }`}
                    onClick={() => handleFileSelect(file.id)}
                  >
                    {/* Checkbox */}
                    <div className={`w-5 h-5 rounded border-2 flex items-center justify-center ${
                      selectedFiles.includes(file.id)
                        ? 'bg-bambu-green border-bambu-green'
                        : 'border-bambu-gray/50'
                    }`}>
                      {selectedFiles.includes(file.id) && <div className="w-2 h-2 bg-white rounded-sm" />}
                    </div>
                    {/* Name with thumbnail */}
                    <div className="flex items-center gap-3 min-w-0">
                      <div className="relative group/thumb">
                        <div className="w-10 h-10 rounded bg-bambu-dark flex-shrink-0 overflow-hidden">
                          {file.thumbnail_path ? (
                            <img
                              src={`${api.getLibraryFileThumbnailUrl(file.id)}${thumbnailVersions[file.id] ? ((api.getLibraryFileThumbnailUrl(file.id).includes('?') ? '&' : '?') + `v=${thumbnailVersions[file.id]}`) : ''}`}
                              alt=""
                              className="w-full h-full object-cover"
                            />
                          ) : (
                            <div className="w-full h-full flex items-center justify-center">
                              <FileBox className="w-5 h-5 text-bambu-gray/50" />
                            </div>
                          )}
                        </div>
                        {/* Hover preview */}
                        {file.thumbnail_path && (
                          <div className="absolute left-0 top-full mt-2 z-50 hidden group-hover/thumb:block">
                            <div className="w-48 h-48 rounded-lg bg-bambu-dark-secondary border border-bambu-dark-tertiary shadow-xl overflow-hidden">
                              <img
                                src={`${api.getLibraryFileThumbnailUrl(file.id)}${thumbnailVersions[file.id] ? ((api.getLibraryFileThumbnailUrl(file.id).includes('?') ? '&' : '?') + `v=${thumbnailVersions[file.id]}`) : ''}`}
                                alt={file.filename}
                                className="w-full h-full object-contain"
                              />
                            </div>
                          </div>
                        )}
                      </div>
                      <div className="min-w-0">
                        <div className="text-sm text-white truncate">{file.print_name || file.filename}</div>
                      </div>
                    </div>
                    {/* Uploaded By - only show when auth is enabled */}
                    {authEnabled && (
                      <div className="text-sm text-bambu-gray flex items-center gap-1">
                        {file.created_by_username ? (
                          <>
                            <User className="w-3 h-3" />
                            <span className="truncate">{file.created_by_username}</span>
                          </>
                        ) : (
                          '-'
                        )}
                      </div>
                    )}
                    {/* Type */}
                    <div>
                      <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${
                        file.file_type === '3mf' ? 'bg-bambu-green/20 text-bambu-green'
                        : (file.file_type === 'gcode' || file.file_type === 'gcode.3mf') ? 'bg-blue-500/20 text-blue-400'
                        : file.file_type === 'stl' ? 'bg-purple-500/20 text-purple-400'
                        : 'bg-bambu-gray/20 text-bambu-gray'
                      }`}>
                        {file.file_type.toUpperCase()}
                      </span>
                    </div>
                    {/* Size */}
                    <div className="text-sm text-bambu-gray">{formatFileSize(file.file_size)}</div>
                    {/* Prints */}
                    <div className="text-sm text-bambu-gray">{file.print_count > 0 ? `${file.print_count}x` : '-'}</div>
                    {/* Tags (#1268) — clickable chips push into the active
                        filter; minmax(0,200px) on the column lets the cell
                        shrink/wrap on narrow viewports without pushing the
                        Actions cell off-screen. */}
                    <div className="min-w-0" onClick={(e) => e.stopPropagation()}>
                      {!file.tags || file.tags.length === 0 ? (
                        <span className="text-xs text-bambu-gray/50">-</span>
                      ) : (
                        <div className="flex flex-wrap gap-1">
                          {file.tags.map((tg) => (
                            <button
                              key={tg.id}
                              type="button"
                              onClick={() => toggleTagFilter(tg.id)}
                              className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded-full text-[10px] bg-bambu-green/10 text-bambu-green hover:bg-bambu-green/20 transition-colors max-w-full"
                              title={tg.name}
                            >
                              <TagIcon className="w-2.5 h-2.5 flex-shrink-0" />
                              <span className="truncate">{tg.name}</span>
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                    {/* Actions */}
                    <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                      {isSlicedFilename(file.filename) && (
                        <>
                          <button
                            onClick={() => hasPermission('queue:create') && setPrintFile(file)}
                            className={`p-1.5 rounded transition-colors ${
                              hasPermission('queue:create')
                                ? 'hover:bg-bambu-dark text-bambu-gray hover:text-bambu-green'
                                : 'text-bambu-gray/50 cursor-not-allowed'
                            }`}
                            title={hasPermission('queue:create') ? t('common.print') : t('fileManager.noPermissionAddToQueue')}
                            disabled={!hasPermission('queue:create')}
                          >
                            <Printer className="w-4 h-4" />
                          </button>
                        </>
                      )}
                      {(settings?.use_slicer_api ?? false) && isSliceableFilename(file.filename) && (
                        <button
                          onClick={() => hasPermission('library:upload') && setSliceFile(file)}
                          className={`p-1.5 rounded transition-colors ${
                            hasPermission('library:upload')
                              ? 'hover:bg-bambu-dark text-bambu-gray hover:text-bambu-green'
                              : 'text-bambu-gray/50 cursor-not-allowed'
                          }`}
                          title={hasPermission('library:upload') ? t('slice.action') : t('fileManager.noPermissionSlice')}
                          disabled={!hasPermission('library:upload')}
                        >
                          <Cog className="w-4 h-4" />
                        </button>
                      )}
                      {(file.file_type === '3mf' || file.file_type === 'gcode' || file.file_type === 'gcode.3mf' || file.file_type === 'stl') && (
                        <button
                          onClick={() => {
                            if (!hasPermission('library:read')) return;
                            if (isSlicedFilename(file.filename)) {
                              navigate(`/gcode-viewer?library_file=${file.id}`);
                            } else {
                              setViewerFile(file);
                            }
                          }}
                          className={`p-1.5 rounded transition-colors ${
                            hasPermission('library:read')
                              ? 'hover:bg-bambu-dark text-bambu-gray hover:text-bambu-green'
                              : 'text-bambu-gray/50 cursor-not-allowed'
                          }`}
                          title={hasPermission('library:read') ? '3D Preview' : 'You do not have permission to preview files'}
                          disabled={!hasPermission('library:read')}
                        >
                          <Box className="w-4 h-4" />
                        </button>
                      )}
                      <button
                        onClick={() => hasPermission('library:read') && handleDownload(file.id)}
                        className={`p-1.5 rounded transition-colors ${
                          hasPermission('library:read')
                            ? 'hover:bg-bambu-dark text-bambu-gray hover:text-white'
                            : 'text-bambu-gray/50 cursor-not-allowed'
                        }`}
                        title={hasPermission('library:read') ? t('common.download') : t('fileManager.noPermissionDownload')}
                        disabled={!hasPermission('library:read')}
                      >
                        <Download className="w-4 h-4" />
                      </button>
                      <button
                        onClick={() => canModify('library', 'update', file.created_by_id) && setRenameItem({ type: 'file', id: file.id, name: file.filename })}
                        className={`p-1.5 rounded transition-colors ${
                          canModify('library', 'update', file.created_by_id)
                            ? 'hover:bg-bambu-dark text-bambu-gray hover:text-white'
                            : 'text-bambu-gray/50 cursor-not-allowed'
                        }`}
                        title={canModify('library', 'update', file.created_by_id) ? t('common.rename') : t('fileManager.noPermissionRenameFile')}
                        disabled={!canModify('library', 'update', file.created_by_id)}
                      >
                        <Pencil className="w-4 h-4" />
                      </button>
                      {file.file_type === 'stl' && (
                        <button
                          onClick={() => canModify('library', 'update', file.created_by_id) && singleThumbnailMutation.mutate(file.id)}
                          className={`p-1.5 rounded transition-colors ${
                            canModify('library', 'update', file.created_by_id)
                              ? 'hover:bg-bambu-dark text-bambu-gray hover:text-bambu-green'
                              : 'text-bambu-gray/50 cursor-not-allowed'
                          }`}
                          title={canModify('library', 'update', file.created_by_id) ? t('fileManager.generateThumbnail') : t('fileManager.noPermissionGenerateThumbnail')}
                          disabled={singleThumbnailMutation.isPending || !canModify('library', 'update', file.created_by_id)}
                        >
                          <Image className="w-4 h-4" />
                        </button>
                      )}
                      <button
                        onClick={() => canModify('library', 'delete', file.created_by_id) && setDeleteConfirm({ type: 'file', id: file.id })}
                        className={`p-1.5 rounded transition-colors ${
                          canModify('library', 'delete', file.created_by_id)
                            ? 'hover:bg-bambu-dark text-bambu-gray hover:text-red-400'
                            : 'text-bambu-gray/50 cursor-not-allowed'
                        }`}
                        title={canModify('library', 'delete', file.created_by_id) ? t('common.delete') : t('fileManager.noPermissionDeleteFile')}
                        disabled={!canModify('library', 'delete', file.created_by_id)}
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Modals */}
      {showNewFolderModal && (
        <NewFolderModal
          parentId={selectedFolderId}
          onClose={() => setShowNewFolderModal(false)}
          onSave={(data) => createFolderMutation.mutate(data)}
          isLoading={createFolderMutation.isPending}
          t={t}
        />
      )}

      {showExternalFolderModal && (
        <ExternalFolderModal
          onClose={() => setShowExternalFolderModal(false)}
          onSave={(data) => createExternalFolderMutation.mutate(data)}
          isLoading={createExternalFolderMutation.isPending}
          t={t}
        />
      )}

      {showMoveModal && folders && (
        <MoveFilesModal
          folders={folders}
          selectedFiles={selectedFiles}
          currentFolderId={selectedFolderId}
          onClose={() => setShowMoveModal(false)}
          onMove={(folderId) => moveFilesMutation.mutate({ fileIds: selectedFiles, folderId })}
          isLoading={moveFilesMutation.isPending}
          t={t}
        />
      )}

      {showUploadModal && (
        <FileUploadModal
          folderId={selectedFolderId}
          onClose={() => {
            setShowUploadModal(false);
            setDroppedFiles([]);
          }}
          onUploadComplete={handleUploadComplete}
          initialFiles={droppedFiles.length > 0 ? droppedFiles : undefined}
        />
      )}

      {showPurgeModal && (
        <PurgeOldFilesModal onClose={() => setShowPurgeModal(false)} />
      )}

      <LibraryTagsModal
        open={showTagsModal}
        onClose={() => setShowTagsModal(false)}
        onPickTag={(tagId) => {
          if (!selectedTagIds.includes(tagId)) {
            setSelectedTagIds((prev) => [...prev, tagId]);
          }
        }}
      />

      <BulkTagsPickerModal
        open={showBulkTagsModal}
        fileIds={selectedFiles}
        onClose={() => setShowBulkTagsModal(false)}
      />

      {linkFolder && (
        <LinkFolderModal
          folder={linkFolder}
          onClose={() => setLinkFolder(null)}
          onLink={(data) => updateFolderMutation.mutate({ id: linkFolder.id, data })}
          isLoading={updateFolderMutation.isPending}
          t={t}
        />
      )}

      {deleteConfirm && (
        <ConfirmModal
          title={
            deleteConfirm.type === 'folder'
              ? t('fileManager.deleteFolder')
              : deleteConfirm.type === 'bulk'
              ? t('fileManager.deleteFilesCount', { count: deleteConfirm.count })
              : t('fileManager.deleteFile')
          }
          message={
            deleteConfirm.type === 'folder'
              ? t('fileManager.deleteFolderConfirm')
              : deleteConfirm.type === 'bulk'
              ? t('fileManager.deleteFilesConfirm', { count: deleteConfirm.count })
              : t('fileManager.deleteFileConfirm')
          }
          confirmText={t('common.delete')}
          variant="danger"
          isLoading={isDeleting}
          loadingText={t('fileManager.deleting')}
          onConfirm={handleDeleteConfirm}
          onCancel={() => setDeleteConfirm(null)}
        />
      )}

      {printFile && (
        <PrintModal
          mode="add-to-queue"
          libraryFileId={printFile.id}
          archiveName={printFile.print_name || printFile.filename}
          onClose={() => setPrintFile(null)}
          onSuccess={() => {
            setPrintFile(null);
            setSelectedFiles([]);
            queryClient.invalidateQueries({ queryKey: ['library-files'] });
            queryClient.invalidateQueries({ queryKey: ['queue'] });
            queryClient.invalidateQueries({ queryKey: ['archives'] });
          }}
        />
      )}

      {sliceFile && (
        <SliceModal
          source={{ kind: 'libraryFile', id: sliceFile.id, filename: sliceFile.filename }}
          onClose={() => setSliceFile(null)}
        />
      )}

      {viewerFile && (
        <ModelViewerModal
          libraryFileId={viewerFile.id}
          title={viewerFile.print_name || viewerFile.filename}
          fileType={viewerFile.file_type}
          onClose={() => setViewerFile(null)}
          onSliceWithBambuddy={
            // Only offer in-app slicing on files the SliceModal can actually
            // handle (matches the file-row Cog visibility check at :2127).
            isSliceableFilename(viewerFile.filename) && hasPermission('library:upload')
              ? () => {
                  const f = viewerFile;
                  setViewerFile(null);
                  setSliceFile(f);
                }
              : undefined
          }
        />
      )}

      {renameItem && (
        <RenameModal
          type={renameItem.type}
          currentName={renameItem.name}
          onClose={() => setRenameItem(null)}
          onSave={(newName) => {
            if (renameItem.type === 'file') {
              renameFileMutation.mutate({ id: renameItem.id, filename: newName });
            } else {
              renameFolderMutation.mutate({ id: renameItem.id, name: newName });
            }
          }}
          isLoading={renameFileMutation.isPending || renameFolderMutation.isPending}
          t={t}
        />
      )}
    </div>
  );
}
