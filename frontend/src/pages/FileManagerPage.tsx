import { useState, useRef, useCallback, useMemo, useEffect, type DragEvent } from 'react';
import { useSearchParams } from 'react-router-dom';
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
  CheckCircle,
  XCircle,
  Link2,
  Unlink,
  Archive as ArchiveIcon,
  Briefcase,
  Printer,
  Pencil,
  Play,
  Image,
  User,
  Box,
} from 'lucide-react';
import { api } from '../api/client';
import type {
  LibraryFolderTree,
  LibraryFileListItem,
  LibraryFolderCreate,
  LibraryFolderUpdate,
  AppSettings,
  Archive,
  Permission,
} from '../api/client';
import { Button } from '../components/Button';
import { ConfirmModal } from '../components/ConfirmModal';
import { PrintModal } from '../components/PrintModal';
import { ModelViewerModal } from '../components/ModelViewerModal';
import { useToast } from '../contexts/ToastContext';
import { useIsMobile } from '../hooks/useIsMobile';
import { useAuth } from '../contexts/AuthContext';

type SortField = 'name' | 'date' | 'size' | 'type' | 'prints';
type SortDirection = 'asc' | 'desc';
type TFunction = (key: string, options?: Record<string, unknown>) => string;

// Utility to format file size
function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

// Utility to format duration
function formatDuration(seconds: number | null): string {
  if (!seconds) return '-';
  const hours = Math.floor(seconds / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  if (hours > 0) return `${hours}h ${mins}m`;
  return `${mins}m`;
}

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
  const [name, setName] = useState(currentName);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (name.trim() && name.trim() !== currentName) {
      onSave(name.trim());
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
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full bg-bambu-dark border border-bambu-dark-tertiary rounded px-3 py-2 text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green"
              autoFocus
              required
            />
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="secondary" onClick={onClose}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" disabled={!name.trim() || name.trim() === currentName || isLoading}>
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

// Upload Modal with Drag & Drop
interface UploadModalProps {
  folderId: number | null;
  onClose: () => void;
  onUploadComplete: () => void;
  t: TFunction;
}

interface UploadFile {
  file: File;
  status: 'pending' | 'uploading' | 'success' | 'error';
  error?: string;
  isZip?: boolean;
  is3mf?: boolean;
  extractedCount?: number;
}

function UploadModal({ folderId, onClose, onUploadComplete, t }: UploadModalProps) {
  const [files, setFiles] = useState<UploadFile[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [preserveZipStructure, setPreserveZipStructure] = useState(true);
  const [createFolderFromZip, setCreateFolderFromZip] = useState(false);
  const [generateStlThumbnails, setGenerateStlThumbnails] = useState(true);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleDragOver = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragging(false);
  };

  const handleDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragging(false);
    const droppedFiles = Array.from(e.dataTransfer.files);
    addFiles(droppedFiles);
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      addFiles(Array.from(e.target.files));
    }
  };

  const addFiles = (newFiles: File[]) => {
    const uploadFiles: UploadFile[] = newFiles.map((file) => ({
      file,
      status: 'pending',
      isZip: file.name.toLowerCase().endsWith('.zip'),
      is3mf: file.name.toLowerCase().endsWith('.3mf'),
    }));
    setFiles((prev) => [...prev, ...uploadFiles]);
  };

  const removeFile = (index: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== index));
  };

  const hasZipFiles = files.some((f) => f.isZip && f.status === 'pending');
  const hasStlFiles = files.some((f) => f.file.name.toLowerCase().endsWith('.stl') && f.status === 'pending');
  const has3mfFiles = files.some((f) => f.is3mf && f.status === 'pending');

  const handleUpload = async () => {
    if (files.length === 0) return;

    setIsUploading(true);

    // Handle all files with library upload (ZIP and regular files including .3mf)
    for (let i = 0; i < files.length; i++) {
      if (files[i].status !== 'pending') continue;

      setFiles((prev) =>
        prev.map((f, idx) => (idx === i ? { ...f, status: 'uploading' } : f))
      );

      try {
        if (files[i].isZip) {
          // Extract ZIP file
          const result = await api.extractZipFile(files[i].file, folderId, preserveZipStructure, createFolderFromZip, generateStlThumbnails);
          setFiles((prev) =>
            prev.map((f, idx) =>
              idx === i
                ? {
                    ...f,
                    status: result.errors.length > 0 && result.extracted === 0 ? 'error' : 'success',
                    extractedCount: result.extracted,
                    error: result.errors.length > 0 ? `${result.errors.length} files failed` : undefined,
                  }
                : f
            )
          );
        } else {
          // Regular file upload (STL, .3mf, etc.) - .3mf files automatically get metadata extracted
          await api.uploadLibraryFile(files[i].file, folderId, generateStlThumbnails);
          setFiles((prev) =>
            prev.map((f, idx) => (idx === i ? { ...f, status: 'success' } : f))
          );
        }
      } catch (err) {
        setFiles((prev) =>
          prev.map((f, idx) =>
            idx === i
              ? { ...f, status: 'error', error: err instanceof Error ? err.message : 'Upload failed' }
              : f
          )
        );
      }
    }

    setIsUploading(false);
    onUploadComplete();
    // Auto-close modal after upload completes
    onClose();
  };

  const pendingCount = files.filter((f) => f.status === 'pending').length;
  const successCount = files.filter((f) => f.status === 'success').length;
  const errorCount = files.filter((f) => f.status === 'error').length;
  const allDone = files.length > 0 && pendingCount === 0 && !isUploading;

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
      <div className="bg-bambu-dark-secondary rounded-lg w-full max-w-lg border border-bambu-dark-tertiary">
        <div className="p-4 border-b border-bambu-dark-tertiary flex items-center justify-between">
          <h2 className="text-lg font-semibold text-white">{t('fileManager.uploadFiles')}</h2>
          <button onClick={onClose} className="p-1 hover:bg-bambu-dark rounded">
            <X className="w-5 h-5 text-bambu-gray" />
          </button>
        </div>

        <div className="p-4 space-y-4">
          {/* Drop Zone */}
          <div
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
            className={`border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors ${
              isDragging
                ? 'border-bambu-green bg-bambu-green/10'
                : 'border-bambu-dark-tertiary hover:border-bambu-green/50'
            }`}
          >
            <Upload className={`w-10 h-10 mx-auto mb-3 ${isDragging ? 'text-bambu-green' : 'text-bambu-gray'}`} />
            <p className="text-white font-medium">
              {isDragging ? t('fileManager.dropFilesHere') : t('fileManager.dragDropFiles')}
            </p>
            <p className="text-sm text-bambu-gray mt-1">{t('fileManager.orClickToBrowse')}</p>
            <p className="text-xs text-bambu-gray/70 mt-2">{t('fileManager.allFileTypesSupported')}</p>
          </div>

          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={handleFileSelect}
          />

          {/* ZIP Options */}
          {hasZipFiles && (
            <div className="p-3 bg-blue-500/10 border border-blue-500/30 rounded-lg">
              <div className="flex items-start gap-3">
                <ArchiveIcon className="w-5 h-5 text-blue-400 mt-0.5 flex-shrink-0" />
                <div className="flex-1">
                  <p className="text-sm text-blue-300 font-medium">{t('fileManager.zipFilesDetected')}</p>
                  <p className="text-xs text-blue-300/70 mt-1">
                    {t('fileManager.zipExtractOptions')}
                  </p>
                  <label className="flex items-center gap-2 mt-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={preserveZipStructure}
                      onChange={(e) => setPreserveZipStructure(e.target.checked)}
                      className="w-4 h-4 rounded border-bambu-dark-tertiary bg-bambu-dark text-bambu-green focus:ring-bambu-green"
                    />
                    <span className="text-sm text-white">{t('fileManager.preserveZipStructure')}</span>
                  </label>
                  <label className="flex items-center gap-2 mt-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={createFolderFromZip}
                      onChange={(e) => setCreateFolderFromZip(e.target.checked)}
                      className="w-4 h-4 rounded border-bambu-dark-tertiary bg-bambu-dark text-bambu-green focus:ring-bambu-green"
                    />
                    <span className="text-sm text-white">{t('fileManager.createFolderFromZip')}</span>
                  </label>
                </div>
              </div>
            </div>
          )}

          {/* 3MF File Info - Advanced Extraction */}
          {has3mfFiles && (
            <div className="p-3 bg-purple-500/10 border border-purple-500/30 rounded-lg">
              <div className="flex items-start gap-3">
                <Printer className="w-5 h-5 text-purple-400 mt-0.5 flex-shrink-0" />
                <div className="flex-1">
                  <p className="text-sm text-purple-300 font-medium">{t('fileManager.threemfDetected')}</p>
                  <p className="text-xs text-purple-300/70 mt-1">
                    {t('fileManager.threemfExtractionInfo')}
                  </p>
                </div>
              </div>
            </div>
          )}

          {/* STL Thumbnail Options - show for STL files or ZIP files (which may contain STLs) */}
          {(hasStlFiles || hasZipFiles) && (
            <div className="p-3 bg-bambu-green/10 border border-bambu-green/30 rounded-lg">
              <div className="flex items-start gap-3">
                <Image className="w-5 h-5 text-bambu-green mt-0.5 flex-shrink-0" />
                <div className="flex-1">
                  <p className="text-sm text-bambu-green font-medium">{t('fileManager.stlThumbnailGeneration')}</p>
                  <p className="text-xs text-bambu-green/70 mt-1">
                    {hasZipFiles && !hasStlFiles
                      ? t('fileManager.zipMayContainStl')
                      : t('fileManager.thumbnailsCanBeGenerated')}
                  </p>
                  <label className="flex items-center gap-2 mt-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={generateStlThumbnails}
                      onChange={(e) => setGenerateStlThumbnails(e.target.checked)}
                      className="w-4 h-4 rounded border-bambu-dark-tertiary bg-bambu-dark text-bambu-green focus:ring-bambu-green"
                    />
                    <span className="text-sm text-white">{t('fileManager.generateThumbnailsForStl')}</span>
                  </label>
                </div>
              </div>
            </div>
          )}

          {/* File List */}
          {files.length > 0 && (
            <div className="max-h-48 overflow-y-auto space-y-2">
              {files.map((uploadFile, index) => (
                <div
                  key={index}
                  className="flex items-center gap-3 p-2 bg-bambu-dark rounded-lg"
                >
                  {uploadFile.isZip ? (
                    <ArchiveIcon className="w-4 h-4 text-blue-400 flex-shrink-0" />
                  ) : (
                    <File className="w-4 h-4 text-bambu-gray flex-shrink-0" />
                  )}
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-white truncate">{uploadFile.file.name}</p>
                    <p className="text-xs text-bambu-gray">
                      {(uploadFile.file.size / 1024 / 1024).toFixed(2)} MB
                      {uploadFile.isZip && uploadFile.status === 'pending' && (
                        <span className="text-blue-400 ml-2">• {t('fileManager.willBeExtracted')}</span>
                      )}
                      {uploadFile.extractedCount !== undefined && (
                        <span className="text-green-400 ml-2">• {t('fileManager.filesExtracted', { count: uploadFile.extractedCount })}</span>
                      )}
                    </p>
                  </div>
                  {uploadFile.status === 'pending' && (
                    <button
                      onClick={() => removeFile(index)}
                      className="p-1 hover:bg-bambu-dark-tertiary rounded"
                    >
                      <X className="w-4 h-4 text-bambu-gray" />
                    </button>
                  )}
                  {uploadFile.status === 'uploading' && (
                    <Loader2 className="w-4 h-4 text-bambu-green animate-spin" />
                  )}
                  {uploadFile.status === 'success' && (
                    <CheckCircle className="w-4 h-4 text-green-500" />
                  )}
                  {uploadFile.status === 'error' && (
                    <span title={uploadFile.error}>
                      <XCircle className="w-4 h-4 text-red-500" />
                    </span>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Summary */}
          {allDone && (
            <div className="p-3 bg-bambu-dark rounded-lg">
              <p className="text-sm text-white">
                {t('fileManager.uploadComplete', { succeeded: successCount })}
                {errorCount > 0 && <span className="text-red-400">, {t('fileManager.uploadFailed', { count: errorCount })}</span>}
              </p>
            </div>
          )}
        </div>

        <div className="p-4 border-t border-bambu-dark-tertiary flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>
            {allDone ? t('common.close') : t('common.cancel')}
          </Button>
          {!allDone && (
            <Button
              onClick={handleUpload}
              disabled={pendingCount === 0 || isUploading}
            >
              {isUploading ? (
                <>
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  {t('fileManager.uploading')}
                </>
              ) : (
                <>
                  <Upload className="w-4 h-4 mr-2" />
                  {t('common.upload')} {pendingCount > 0 ? `(${pendingCount})` : ''}
                </>
              )}
            </Button>
          )}
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
  hasPermission: (permission: Permission) => boolean;
  t: TFunction;
}

function FolderTreeItem({ folder, selectedFolderId, onSelect, onDelete, onLink, onRename, depth = 0, wrapNames = false, hasPermission, t }: FolderTreeItemProps) {
  const [expanded, setExpanded] = useState(true);
  const [showActions, setShowActions] = useState(false);
  const hasChildren = folder.children.length > 0;
  const isLinked = folder.project_id || folder.archive_id;

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
        <FolderOpen className="w-4 h-4 text-bambu-green flex-shrink-0" />
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
        {folder.file_count > 0 && (
          <span className="flex-shrink-0 text-xs text-bambu-gray">{folder.file_count}</span>
        )}
        {/* Quick link button - always visible for unlinked folders */}
        {!isLinked && (
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
  return lower.endsWith('.gcode') || lower.includes('.gcode.');
}

// File Card
interface FileCardProps {
  file: LibraryFileListItem;
  isSelected: boolean;
  isMobile: boolean;
  onSelect: (id: number) => void;
  onDelete: (id: number) => void;
  onDownload: (id: number) => void;
  onAddToQueue?: (id: number) => void;
  onPrint?: (file: LibraryFileListItem) => void;
  onPreview3d?: (file: LibraryFileListItem) => void;
  onRename?: (file: LibraryFileListItem) => void;
  onGenerateThumbnail?: (file: LibraryFileListItem) => void;
  thumbnailVersion?: number;
  hasPermission: (permission: Permission) => boolean;
  canModify: (resource: 'queue' | 'archives' | 'library', action: 'update' | 'delete' | 'reprint', createdById: number | null | undefined) => boolean;
  authEnabled: boolean;
  t: TFunction;
}

function FileCard({ file, isSelected, isMobile, onSelect, onDelete, onDownload, onAddToQueue, onPrint, onPreview3d, onRename, onGenerateThumbnail, thumbnailVersion, hasPermission, canModify, authEnabled, t }: FileCardProps) {
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
            src={`${api.getLibraryFileThumbnailUrl(file.id)}${thumbnailVersion ? `?v=${thumbnailVersion}` : ''}`}
            alt={file.filename}
            className="w-full h-full object-cover"
          />
        ) : (
          <FileBox className="w-12 h-12 text-bambu-gray/30" />
        )}
        {/* File type badge */}
        <div className={`absolute top-2 right-2 text-xs px-1.5 py-0.5 rounded font-medium ${
          file.file_type === '3mf' ? 'bg-bambu-green/90 text-white'
          : file.file_type === 'gcode' ? 'bg-blue-500/90 text-white'
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
                    hasPermission('printers:control') ? 'text-bambu-green hover:bg-bambu-dark' : 'text-bambu-gray cursor-not-allowed'
                  }`}
                  onClick={() => { if (hasPermission('printers:control')) { onPrint(file); setShowActions(false); } }}
                  disabled={!hasPermission('printers:control')}
                  title={!hasPermission('printers:control') ? t('fileManager.noPermissionPrint') : undefined}
                >
                  <Printer className="w-3.5 h-3.5" />
                  {t('common.print')}
                </button>
              )}
              {onAddToQueue && isSlicedFilename(file.filename) && (
                <button
                  className={`w-full px-3 py-1.5 text-left text-sm flex items-center gap-2 ${
                    hasPermission('queue:create') ? 'text-white hover:bg-bambu-dark' : 'text-bambu-gray cursor-not-allowed'
                  }`}
                  onClick={() => { if (hasPermission('queue:create')) { onAddToQueue(file.id); setShowActions(false); } }}
                  disabled={!hasPermission('queue:create')}
                  title={!hasPermission('queue:create') ? t('fileManager.noPermissionAddToQueue') : undefined}
                >
                  <Clock className="w-3.5 h-3.5" />
                  {t('fileManager.schedulePrint')}
                </button>
              )}
              {onPreview3d && (file.file_type === '3mf' || file.file_type === 'gcode' || file.file_type === 'stl') && (
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

  // Read folder ID from URL query parameter
  const folderIdFromUrl = searchParams.get('folder');
  const initialFolderId = folderIdFromUrl ? parseInt(folderIdFromUrl, 10) : null;

  // State
  const [selectedFolderId, setSelectedFolderId] = useState<number | null>(initialFolderId);
  const [selectedFiles, setSelectedFiles] = useState<number[]>([]);
  const [showNewFolderModal, setShowNewFolderModal] = useState(false);
  const [showMoveModal, setShowMoveModal] = useState(false);
  const [showUploadModal, setShowUploadModal] = useState(false);
  const [linkFolder, setLinkFolder] = useState<LibraryFolderTree | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState<{ type: 'file' | 'folder' | 'bulk'; id: number; count?: number } | null>(null);
  const [printFile, setPrintFile] = useState<LibraryFileListItem | null>(null);
  const [printMultiFile, setPrintMultiFile] = useState<LibraryFileListItem | null>(null);
  const [scheduleFile, setScheduleFile] = useState<LibraryFileListItem | null>(null);
  const [renameItem, setRenameItem] = useState<{ type: 'file' | 'folder'; id: number; name: string } | null>(null);
  const [thumbnailVersions, setThumbnailVersions] = useState<Record<number, number>>({});
  const [viewerFile, setViewerFile] = useState<LibraryFileListItem | null>(null);
  const [viewMode, setViewMode] = useState<'grid' | 'list'>(() => {
    return (localStorage.getItem('library-view-mode') as 'grid' | 'list') || 'grid';
  });
  const [wrapFolderNames, setWrapFolderNames] = useState(() => {
    return localStorage.getItem('library-wrap-folders') === 'true';
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

  const { data: files, isLoading: filesLoading } = useQuery({
    queryKey: ['library-files', selectedFolderId],
    queryFn: () => api.getLibraryFiles(selectedFolderId, selectedFolderId === null),
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
          comparison = new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
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

  return (
    <div className="p-4 md:p-8 min-h-[calc(100vh-64px)] lg:h-[calc(100vh-64px)] flex flex-col">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-3">
            <div className="p-2.5 bg-bambu-green/10 rounded-xl">
              <FolderOpen className="w-6 h-6 text-bambu-green" />
            </div>
            {t('fileManager.title')}
          </h1>
          <p className="text-sm text-bambu-gray mt-2 ml-14">
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
            onClick={() => setShowNewFolderModal(true)}
            disabled={!hasPermission('library:upload')}
            title={!hasPermission('library:upload') ? t('fileManager.noPermissionCreateFolder') : undefined}
          >
            <FolderPlus className="w-4 h-4 mr-2" />
            {t('fileManager.newFolder')}
          </Button>
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
            value={selectedFolderId ?? ''}
            onChange={(e) => setSelectedFolderId(e.target.value ? parseInt(e.target.value, 10) : null)}
            className="w-full bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg px-3 py-2.5 text-white focus:outline-none focus:border-bambu-green"
          >
            <option value="">📁 {t('fileManager.allFiles')}</option>
            {folders && (() => {
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
              return flattenFolders(folders).map((folder) => (
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
          <div className="flex-1 overflow-y-auto p-2">
            {/* All Files (root) */}
            <div
              className={`flex items-center gap-2 px-2 py-1.5 rounded cursor-pointer transition-colors ${
                selectedFolderId === null
                  ? 'bg-bambu-green/20 text-bambu-green'
                  : 'hover:bg-bambu-dark text-white'
              }`}
              onClick={() => setSelectedFolderId(null)}
            >
              <FileBox className="w-4 h-4" />
              <span className="text-sm">{t('fileManager.allFiles')}</span>
            </div>

            {/* Folder tree */}
            {folders?.map((folder) => (
              <FolderTreeItem
                key={folder.id}
                folder={folder}
                selectedFolderId={selectedFolderId}
                onSelect={setSelectedFolderId}
                onDelete={(id) => setDeleteConfirm({ type: 'folder', id })}
                onLink={setLinkFolder}
                onRename={(f) => setRenameItem({ type: 'folder', id: f.id, name: f.name })}
                wrapNames={wrapFolderNames}
                hasPermission={hasPermission}
                t={t}
              />
            ))}
          </div>
        </div>

        {/* Files area */}
        <div className="flex-1 flex flex-col min-w-0 min-h-0">
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
                        onClick={() => setPrintMultiFile(selectedSlicedFiles[0])}
                        disabled={!hasPermission('printers:control')}
                        title={!hasPermission('printers:control') ? t('fileManager.noPermissionPrint') : undefined}
                      >
                        <Play className="w-4 h-4 sm:mr-1" />
                        <span className="hidden sm:inline">{t('common.print')}</span>
                      </Button>
                    )}
                    {selectedSlicedFiles.length === 1 && (
                      <Button
                        variant="secondary"
                        size="sm"
                        // Note: Schedule dialog (PrintModal) is designed for single file at a time
                        // but supports scheduling to multiple printers. This provides more control
                        // over scheduling options compared to the previous bulk queue mutation.
                        onClick={() => setScheduleFile(selectedSlicedFiles[0])}
                        disabled={!hasPermission('queue:create')}
                        title={!hasPermission('queue:create') ? t('fileManager.noPermissionAddToQueue') : undefined}
                      >
                        <Clock className="w-4 h-4 sm:mr-1" />
                        <span className="hidden sm:inline">{t('fileManager.schedulePrint')}</span>
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
                {selectedFolderId !== null ? t('fileManager.folderIsEmpty') : t('fileManager.noFilesYet')}
              </h3>
              <p className="text-bambu-gray text-center max-w-md mb-6">
                {selectedFolderId !== null
                  ? t('fileManager.folderEmptyDescription')
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
                    onAddToQueue={(id) => {
                      const file = files?.find(f => f.id === id);
                      if (file) setScheduleFile(file);
                    }}
                    onPrint={setPrintFile}
                    onPreview3d={setViewerFile}
                    onRename={(f) => setRenameItem({ type: 'file', id: f.id, name: f.filename })}
                    onGenerateThumbnail={(f) => singleThumbnailMutation.mutate(f.id)}
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
              <div className="bg-bambu-dark-secondary rounded-lg border border-bambu-dark-tertiary overflow-hidden">
                {/* List header - hidden on mobile, show simplified on small screens */}
                <div className={`hidden sm:grid ${authEnabled ? 'grid-cols-[auto_1fr_120px_100px_100px_100px_80px]' : 'grid-cols-[auto_1fr_100px_100px_100px_80px]'} gap-4 px-4 py-2 bg-bambu-dark-secondary border-b border-bambu-dark-tertiary text-xs text-bambu-gray font-medium`}>
                  <div className="w-6" />
                  <div>{t('common.name')}</div>
                  {authEnabled && <div>{t('fileManager.uploadedBy', { defaultValue: 'Uploaded By' })}</div>}
                  <div>{t('common.type')}</div>
                  <div>{t('fileManager.size')}</div>
                  <div>{t('fileManager.prints')}</div>
                  <div />
                </div>
                {/* List rows */}
                {filteredAndSortedFiles.map((file) => (
                  <div
                    key={file.id}
                    className={`grid ${authEnabled ? 'grid-cols-[auto_1fr_120px_100px_100px_100px_80px]' : 'grid-cols-[auto_1fr_100px_100px_100px_80px]'} gap-4 px-4 py-3 items-center border-b border-bambu-dark-tertiary last:border-b-0 cursor-pointer hover:bg-bambu-dark/50 transition-colors ${
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
                              src={`${api.getLibraryFileThumbnailUrl(file.id)}${thumbnailVersions[file.id] ? `?v=${thumbnailVersions[file.id]}` : ''}`}
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
                                src={`${api.getLibraryFileThumbnailUrl(file.id)}${thumbnailVersions[file.id] ? `?v=${thumbnailVersions[file.id]}` : ''}`}
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
                        : file.file_type === 'gcode' ? 'bg-blue-500/20 text-blue-400'
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
                    {/* Actions */}
                    <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                      {isSlicedFilename(file.filename) && (
                        <>
                          <button
                            onClick={() => hasPermission('printers:control') && setPrintFile(file)}
                            className={`p-1.5 rounded transition-colors ${
                              hasPermission('printers:control')
                                ? 'hover:bg-bambu-dark text-bambu-gray hover:text-bambu-green'
                                : 'text-bambu-gray/50 cursor-not-allowed'
                            }`}
                            title={hasPermission('printers:control') ? t('common.print') : t('fileManager.noPermissionPrint')}
                            disabled={!hasPermission('printers:control')}
                          >
                            <Printer className="w-4 h-4" />
                          </button>
                          <button
                            onClick={() => {
                              if (hasPermission('queue:create')) {
                                setScheduleFile(file);
                              }
                            }}
                            className={`p-1.5 rounded transition-colors ${
                              hasPermission('queue:create')
                                ? 'hover:bg-bambu-dark text-bambu-gray hover:text-white'
                                : 'text-bambu-gray/50 cursor-not-allowed'
                            }`}
                            title={hasPermission('queue:create') ? t('fileManager.schedulePrint') : t('fileManager.noPermissionAddToQueue')}
                            disabled={!hasPermission('queue:create')}
                          >
                            <Clock className="w-4 h-4" />
                          </button>
                        </>
                      )}
                      {(file.file_type === '3mf' || file.file_type === 'gcode' || file.file_type === 'stl') && (
                        <button
                          onClick={() => hasPermission('library:read') && setViewerFile(file)}
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
        <UploadModal
          folderId={selectedFolderId}
          onClose={() => setShowUploadModal(false)}
          onUploadComplete={handleUploadComplete}
          t={t}
        />
      )}

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
          mode="reprint"
          libraryFileId={printFile.id}
          archiveName={printFile.print_name || printFile.filename}
          onClose={() => setPrintFile(null)}
          onSuccess={() => {
            setPrintFile(null);
            queryClient.invalidateQueries({ queryKey: ['library-files'] });
            queryClient.invalidateQueries({ queryKey: ['archives'] });
          }}
        />
      )}

      {printMultiFile && (
        <PrintModal
          mode="reprint"
          libraryFileId={printMultiFile.id}
          archiveName={printMultiFile.print_name || printMultiFile.filename}
          onClose={() => setPrintMultiFile(null)}
          onSuccess={() => {
            setPrintMultiFile(null);
            setSelectedFiles([]);
            queryClient.invalidateQueries({ queryKey: ['library-files'] });
            queryClient.invalidateQueries({ queryKey: ['archives'] });
          }}
        />
      )}

      {scheduleFile && (
        <PrintModal
          mode="add-to-queue"
          libraryFileId={scheduleFile.id}
          archiveName={scheduleFile.print_name || scheduleFile.filename}
          onClose={() => setScheduleFile(null)}
          onSuccess={() => {
            setScheduleFile(null);
            setSelectedFiles([]);
            queryClient.invalidateQueries({ queryKey: ['library-files'] });
            queryClient.invalidateQueries({ queryKey: ['queue'] });
            queryClient.invalidateQueries({ queryKey: ['archives'] });
          }}
        />
      )}

      {viewerFile && (
        <ModelViewerModal
          libraryFileId={viewerFile.id}
          title={viewerFile.print_name || viewerFile.filename}
          fileType={viewerFile.file_type}
          onClose={() => setViewerFile(null)}
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
