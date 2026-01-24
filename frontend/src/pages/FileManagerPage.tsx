import { useState, useRef, useCallback, useMemo, useEffect, type DragEvent } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
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
} from 'lucide-react';
import { api } from '../api/client';
import type {
  LibraryFolderTree,
  LibraryFileListItem,
  LibraryFolderCreate,
  LibraryFolderUpdate,
  AppSettings,
  Archive,
} from '../api/client';
import { Button } from '../components/Button';
import { ConfirmModal } from '../components/ConfirmModal';
import { PrintModal } from '../components/PrintModal';
import { ExternalFolderModal } from '../components/ExternalFolderModal';
import { useToast } from '../contexts/ToastContext';

type SortField = 'name' | 'date' | 'size' | 'type' | 'prints';
type SortDirection = 'asc' | 'desc';

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
}

function NewFolderModal({ parentId, onClose, onSave, isLoading }: NewFolderModalProps) {
  const [name, setName] = useState('');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSave({ name: name.trim(), parent_id: parentId });
  };

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
      <div className="bg-bambu-dark-secondary rounded-lg w-full max-w-sm border border-bambu-dark-tertiary">
        <div className="p-4 border-b border-bambu-dark-tertiary">
          <h2 className="text-lg font-semibold text-white">New Folder</h2>
        </div>
        <form onSubmit={handleSubmit} className="p-4 space-y-4">
          <div>
            <label className="block text-sm font-medium text-white mb-1">
              Folder Name
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full bg-bambu-dark border border-bambu-dark-tertiary rounded px-3 py-2 text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green"
              placeholder="e.g., Functional Parts"
              autoFocus
              required
            />
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="secondary" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={!name.trim() || isLoading}>
              {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Create'}
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
}

function RenameModal({ type, currentName, onClose, onSave, isLoading }: RenameModalProps) {
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
          <h2 className="text-lg font-semibold text-white">Rename {type === 'file' ? 'File' : 'Folder'}</h2>
        </div>
        <form onSubmit={handleSubmit} className="p-4 space-y-4">
          <div>
            <label className="block text-sm font-medium text-white mb-1">
              Name
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
              Cancel
            </Button>
            <Button type="submit" disabled={!name.trim() || name.trim() === currentName || isLoading}>
              {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Rename'}
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
}

function MoveFilesModal({ folders, selectedFiles, currentFolderId, onClose, onMove, isLoading }: MoveFilesModalProps) {
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

  const flatFolders = [{ id: null, name: 'Root (No Folder)', depth: 0 }, ...flattenFolders(folders)];

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
      <div className="bg-bambu-dark-secondary rounded-lg w-full max-w-sm border border-bambu-dark-tertiary">
        <div className="p-4 border-b border-bambu-dark-tertiary">
          <h2 className="text-lg font-semibold text-white">Move {selectedFiles.length} File(s)</h2>
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
                {folder.id === currentFolderId && <span className="text-xs text-bambu-gray ml-auto">(current)</span>}
              </button>
            ))}
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="secondary" onClick={onClose}>
              Cancel
            </Button>
            <Button onClick={() => onMove(targetFolder)} disabled={isLoading}>
              {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Move'}
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
}

function LinkFolderModal({ folder, onClose, onLink, isLoading }: LinkFolderModalProps) {
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
            Link Folder
          </h2>
          <button onClick={onClose} className="p-1 hover:bg-bambu-dark rounded">
            <X className="w-5 h-5 text-bambu-gray" />
          </button>
        </div>

        <div className="p-4 space-y-4">
          <p className="text-sm text-bambu-gray">
            Link "<span className="text-white">{folder.name}</span>" to a project or archive for quick access.
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
              Project
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
              Archive
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
                <p className="text-sm text-bambu-gray text-center py-4">No projects found</p>
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
                <p className="text-sm text-bambu-gray text-center py-4">No archives found</p>
              )
            )}
          </div>
        </div>

        <div className="p-4 border-t border-bambu-dark-tertiary flex justify-between">
          {isLinked && (
            <Button variant="danger" onClick={handleUnlink} disabled={isLoading}>
              <Unlink className="w-4 h-4 mr-2" />
              Unlink
            </Button>
          )}
          <div className={`flex gap-2 ${!isLinked ? 'ml-auto' : ''}`}>
            <Button variant="secondary" onClick={onClose}>
              Cancel
            </Button>
            <Button onClick={handleSave} disabled={!selectedId || isLoading}>
              {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Link'}
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
}

interface UploadFile {
  file: File;
  status: 'pending' | 'uploading' | 'success' | 'error';
  error?: string;
  isZip?: boolean;
  extractedCount?: number;
}

function UploadModal({ folderId, onClose, onUploadComplete }: UploadModalProps) {
  const [files, setFiles] = useState<UploadFile[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [preserveZipStructure, setPreserveZipStructure] = useState(true);
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
    }));
    setFiles((prev) => [...prev, ...uploadFiles]);
  };

  const removeFile = (index: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== index));
  };

  const hasZipFiles = files.some((f) => f.isZip && f.status === 'pending');

  const handleUpload = async () => {
    if (files.length === 0) return;

    setIsUploading(true);

    for (let i = 0; i < files.length; i++) {
      if (files[i].status !== 'pending') continue;

      setFiles((prev) =>
        prev.map((f, idx) => (idx === i ? { ...f, status: 'uploading' } : f))
      );

      try {
        if (files[i].isZip) {
          // Extract ZIP file
          const result = await api.extractZipFile(files[i].file, folderId, preserveZipStructure);
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
          // Regular file upload
          await api.uploadLibraryFile(files[i].file, folderId);
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
          <h2 className="text-lg font-semibold text-white">Upload Files</h2>
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
              {isDragging ? 'Drop files here' : 'Drag & drop files here'}
            </p>
            <p className="text-sm text-bambu-gray mt-1">or click to browse</p>
            <p className="text-xs text-bambu-gray/70 mt-2">ZIP files will be automatically extracted</p>
          </div>

          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept="*/*,.zip"
            className="hidden"
            onChange={handleFileSelect}
          />

          {/* ZIP Options */}
          {hasZipFiles && (
            <div className="p-3 bg-blue-500/10 border border-blue-500/30 rounded-lg">
              <div className="flex items-start gap-3">
                <ArchiveIcon className="w-5 h-5 text-blue-400 mt-0.5 flex-shrink-0" />
                <div className="flex-1">
                  <p className="text-sm text-blue-300 font-medium">ZIP files detected</p>
                  <p className="text-xs text-blue-300/70 mt-1">
                    ZIP files will be extracted. Choose how to handle folder structure:
                  </p>
                  <label className="flex items-center gap-2 mt-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={preserveZipStructure}
                      onChange={(e) => setPreserveZipStructure(e.target.checked)}
                      className="w-4 h-4 rounded border-bambu-dark-tertiary bg-bambu-dark text-bambu-green focus:ring-bambu-green"
                    />
                    <span className="text-sm text-white">Preserve folder structure from ZIP</span>
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
                        <span className="text-blue-400 ml-2">• Will be extracted</span>
                      )}
                      {uploadFile.extractedCount !== undefined && (
                        <span className="text-green-400 ml-2">• {uploadFile.extractedCount} files extracted</span>
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
                Upload complete: {successCount} succeeded
                {errorCount > 0 && <span className="text-red-400">, {errorCount} failed</span>}
              </p>
            </div>
          )}
        </div>

        <div className="p-4 border-t border-bambu-dark-tertiary flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>
            {allDone ? 'Close' : 'Cancel'}
          </Button>
          {!allDone && (
            <Button
              onClick={handleUpload}
              disabled={pendingCount === 0 || isUploading}
            >
              {isUploading ? (
                <>
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  Uploading...
                </>
              ) : (
                <>
                  <Upload className="w-4 h-4 mr-2" />
                  Upload {pendingCount > 0 ? `(${pendingCount})` : ''}
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
}

function FolderTreeItem({ folder, selectedFolderId, onSelect, onDelete, onLink, onRename, depth = 0 }: FolderTreeItemProps) {
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
        {folder.is_external ? (
          <Link2 className="w-4 h-4 text-blue-400 flex-shrink-0" title="External folder" />
        ) : (
          <FolderOpen className="w-4 h-4 text-bambu-green flex-shrink-0" />
        )}
        <span className="text-sm truncate flex-1">{folder.name}</span>
        {/* External folder indicators */}
        {folder.is_external && folder.external_readonly && (
          <div className="w-3 h-3 text-gray-400 flex-shrink-0" title="Read-only">
            🔒
          </div>
        )}
        {folder.is_external && !folder.external_accessible && (
          <AlertTriangle className="w-3 h-3 text-red-500 flex-shrink-0" title="Path not accessible" />
        )}
        {/* Link indicator - clickable to change link */}
        {isLinked && (
          <button
            onClick={(e) => { e.stopPropagation(); onLink(folder); }}
            className="flex items-center gap-1 text-xs px-1.5 py-0.5 rounded bg-blue-500/20 text-blue-400 hover:bg-blue-500/30 transition-colors"
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
          <span className="text-xs text-bambu-gray">{folder.file_count}</span>
        )}
        {/* Quick link button - always visible for unlinked folders */}
        {!isLinked && (
          <button
            onClick={(e) => { e.stopPropagation(); onLink(folder); }}
            className="p-1 rounded hover:bg-bambu-dark-tertiary"
            title="Link to project or archive"
          >
            <Link2 className="w-3.5 h-3.5 text-bambu-gray hover:text-bambu-green" />
          </button>
        )}
        <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity" onClick={(e) => e.stopPropagation()}>
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
                  className="w-full px-3 py-1.5 text-left text-sm text-white hover:bg-bambu-dark flex items-center gap-2"
                  onClick={() => { onRename(folder); setShowActions(false); }}
                >
                  <Pencil className="w-3.5 h-3.5" />
                  Rename
                </button>
                <button
                  className="w-full px-3 py-1.5 text-left text-sm text-white hover:bg-bambu-dark flex items-center gap-2"
                  onClick={() => { onLink(folder); setShowActions(false); }}
                >
                  <Link2 className="w-3.5 h-3.5" />
                  {isLinked ? 'Change Link...' : 'Link to...'}
                </button>
                <button
                  className="w-full px-3 py-1.5 text-left text-sm text-red-400 hover:bg-bambu-dark flex items-center gap-2"
                  onClick={() => { onDelete(folder.id); setShowActions(false); }}
                >
                  <Trash2 className="w-3.5 h-3.5" />
                  Delete
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
  onSelect: (id: number) => void;
  onDelete: (id: number) => void;
  onDownload: (id: number) => void;
  onAddToQueue?: (id: number) => void;
  onPrint?: (file: LibraryFileListItem) => void;
  onRename?: (file: LibraryFileListItem) => void;
}

function FileCard({ file, isSelected, onSelect, onDelete, onDownload, onAddToQueue, onPrint, onRename }: FileCardProps) {
  const [showActions, setShowActions] = useState(false);

  return (
    <div
      className={`group relative bg-bambu-card rounded-lg border transition-all cursor-pointer overflow-hidden ${
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
            src={api.getLibraryFileThumbnailUrl(file.id)}
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
        {file.print_count > 0 && (
          <div className="mt-1 text-xs text-bambu-green">
            Printed {file.print_count}x
          </div>
        )}
      </div>

      {/* Actions - always visible on mobile, hover on desktop */}
      <div className="absolute bottom-2 right-2 opacity-100 md:opacity-0 md:group-hover:opacity-100 transition-opacity" onClick={(e) => e.stopPropagation()}>
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
                  className="w-full px-3 py-1.5 text-left text-sm text-bambu-green hover:bg-bambu-dark flex items-center gap-2"
                  onClick={() => { onPrint(file); setShowActions(false); }}
                >
                  <Printer className="w-3.5 h-3.5" />
                  Print
                </button>
              )}
              {onAddToQueue && isSlicedFilename(file.filename) && (
                <button
                  className="w-full px-3 py-1.5 text-left text-sm text-white hover:bg-bambu-dark flex items-center gap-2"
                  onClick={() => { onAddToQueue(file.id); setShowActions(false); }}
                >
                  <Clock className="w-3.5 h-3.5" />
                  Add to Queue
                </button>
              )}
              <button
                className="w-full px-3 py-1.5 text-left text-sm text-white hover:bg-bambu-dark flex items-center gap-2"
                onClick={() => { onDownload(file.id); setShowActions(false); }}
              >
                <Download className="w-3.5 h-3.5" />
                Download
              </button>
              {onRename && (
                <button
                  className="w-full px-3 py-1.5 text-left text-sm text-white hover:bg-bambu-dark flex items-center gap-2"
                  onClick={() => { onRename(file); setShowActions(false); }}
                >
                  <Pencil className="w-3.5 h-3.5" />
                  Rename
                </button>
              )}
              <button
                className="w-full px-3 py-1.5 text-left text-sm text-red-400 hover:bg-bambu-dark flex items-center gap-2"
                onClick={() => { onDelete(file.id); setShowActions(false); }}
              >
                <Trash2 className="w-3.5 h-3.5" />
                Delete
              </button>
            </div>
          </>
        )}
      </div>

      {/* Selection checkbox - always visible on mobile, hover on desktop */}
      <div className={`absolute top-2 left-2 w-5 h-5 rounded border-2 flex items-center justify-center transition-all ${
        isSelected
          ? 'bg-bambu-green border-bambu-green'
          : 'border-white/30 bg-black/30 opacity-100 md:opacity-0 md:group-hover:opacity-100'
      }`}>
        {isSelected && <div className="w-2 h-2 bg-white rounded-sm" />}
      </div>
    </div>
  );
}

export function FileManagerPage() {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const [searchParams] = useSearchParams();

  // Read folder ID from URL query parameter
  const folderIdFromUrl = searchParams.get('folder');
  const initialFolderId = folderIdFromUrl ? parseInt(folderIdFromUrl, 10) : null;

  // State
  const [selectedFolderId, setSelectedFolderId] = useState<number | null>(initialFolderId);
  const [selectedFiles, setSelectedFiles] = useState<number[]>([]);
  const [showNewFolderModal, setShowNewFolderModal] = useState(false);
  const [showExternalFolderModal, setShowExternalFolderModal] = useState(false);
  const [showMoveModal, setShowMoveModal] = useState(false);
  const [showUploadModal, setShowUploadModal] = useState(false);
  const [linkFolder, setLinkFolder] = useState<LibraryFolderTree | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState<{ type: 'file' | 'folder' | 'bulk'; id: number; count?: number } | null>(null);
  const [printFile, setPrintFile] = useState<LibraryFileListItem | null>(null);
  const [printMultiFile, setPrintMultiFile] = useState<LibraryFileListItem | null>(null);
  const [renameItem, setRenameItem] = useState<{ type: 'file' | 'folder'; id: number; name: string } | null>(null);
  const [viewMode, setViewMode] = useState<'grid' | 'list'>(() => {
    return (localStorage.getItem('library-view-mode') as 'grid' | 'list') || 'grid';
  });

  // Filter and sort state
  const [searchQuery, setSearchQuery] = useState('');
  const [filterType, setFilterType] = useState<string>('all');
  const [sortField, setSortField] = useState<SortField>('date');
  const [sortDirection, setSortDirection] = useState<SortDirection>('desc');

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
  }, [files, searchQuery, filterType, sortField, sortDirection]);

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
      showToast('Folder created', 'success');
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
      showToast('Folder deleted', 'success');
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
      showToast('File deleted', 'success');
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
      showToast(`Deleted ${fileIds.length} files`, 'success');
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
      showToast('Files moved', 'success');
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
      showToast(isUnlink ? 'Folder unlinked' : 'Folder linked', 'success');
    },
    onError: (error: Error) => showToast(error.message, 'error'),
  });

  const addToQueueMutation = useMutation({
    mutationFn: (fileIds: number[]) => api.addLibraryFilesToQueue(fileIds),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['library-files'] });
      queryClient.invalidateQueries({ queryKey: ['queue'] });
      queryClient.invalidateQueries({ queryKey: ['archives'] }); // Archives are created when adding to queue
      setSelectedFiles([]);

      if (result.added.length > 0 && result.errors.length === 0) {
        showToast(
          `Added ${result.added.length} file${result.added.length > 1 ? 's' : ''} to queue`,
          'success'
        );
      } else if (result.added.length > 0 && result.errors.length > 0) {
        showToast(
          `Added ${result.added.length} file${result.added.length > 1 ? 's' : ''}, ${result.errors.length} failed`,
          'success'
        );
      } else {
        showToast(`Failed to add files: ${result.errors[0]?.error || 'Unknown error'}`, 'error');
      }
    },
    onError: (error: Error) => showToast(error.message, 'error'),
  });

  const renameFileMutation = useMutation({
    mutationFn: ({ id, filename }: { id: number; filename: string }) =>
      api.updateLibraryFile(id, { filename }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['library-files'] });
      setRenameItem(null);
      showToast('File renamed', 'success');
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
      showToast('Folder renamed', 'success');
    },
    onError: (error: Error) => {
      setRenameItem(null);
      showToast(error.message, 'error');
    },
  });

  const rescanFolderMutation = useMutation({
    mutationFn: (folderId: number) =>
      api.scanExternalFolder(folderId, false),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['library-files'] });
      showToast(
        `Scan complete: +${result.added}, ~${result.updated}, -${result.removed}`,
        'success'
      );
    },
    onError: (error: Error) => {
      showToast(error.message, 'error');
    },
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
    window.open(api.getLibraryFileDownloadUrl(id), '_blank');
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

  // Check if selected folder is readonly external
  const selectedFolderData = useMemo(() => {
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

  const isSelectedFolderReadonly = selectedFolderData?.is_external && selectedFolderData?.external_readonly;

  return (
    <div className="p-4 md:p-8 h-[calc(100vh-64px)] flex flex-col">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-3">
            <div className="p-2.5 bg-bambu-green/10 rounded-xl">
              <FolderOpen className="w-6 h-6 text-bambu-green" />
            </div>
            File Manager
          </h1>
          <p className="text-sm text-bambu-gray mt-2 ml-14">
            Organize and manage your print files
          </p>
        </div>
        <div className="flex items-center gap-2">
          {/* View mode toggle */}
          <div className="flex items-center bg-bambu-dark rounded-lg p-1">
            <button
              onClick={() => handleViewModeChange('grid')}
              className={`p-1.5 rounded transition-colors ${
                viewMode === 'grid' ? 'bg-bambu-card text-white' : 'text-bambu-gray hover:text-white'
              }`}
              title="Grid view"
            >
              <LayoutGrid className="w-4 h-4" />
            </button>
            <button
              onClick={() => handleViewModeChange('list')}
              className={`p-1.5 rounded transition-colors ${
                viewMode === 'list' ? 'bg-bambu-card text-white' : 'text-bambu-gray hover:text-white'
              }`}
              title="List view"
            >
              <List className="w-4 h-4" />
            </button>
          </div>
          <Button variant="secondary" onClick={() => setShowNewFolderModal(true)}>
            <FolderPlus className="w-4 h-4 mr-2" />
            New Folder
          </Button>
          <Button variant="secondary" onClick={() => setShowExternalFolderModal(true)}>
            <Link2 className="w-4 h-4 mr-2" />
            External Folder
          </Button>
          <Button
            onClick={() => setShowUploadModal(true)}
            disabled={isSelectedFolderReadonly}
            title={isSelectedFolderReadonly ? "Cannot upload to read-only external folder" : ""}
          >
            <Upload className="w-4 h-4 mr-2" />
            Upload
          </Button>
        </div>
      </div>

      {/* Disk space warning */}
      {isDiskSpaceLow && stats && settings && (
        <div className="flex items-center gap-3 mb-4 p-3 bg-amber-500/10 border border-amber-500/30 rounded-lg">
          <AlertTriangle className="w-5 h-5 text-amber-500 flex-shrink-0" />
          <div className="flex-1">
            <p className="text-sm text-amber-500 font-medium">Low disk space warning</p>
            <p className="text-xs text-amber-500/80">
              Only {formatFileSize(stats.disk_free_bytes)} free of {formatFileSize(stats.disk_total_bytes)} total.
              Threshold is set to {settings.library_disk_warning_gb} GB in settings.
            </p>
          </div>
        </div>
      )}

      {/* Stats bar */}
      {stats && (
        <div className="flex items-center gap-6 mb-6 p-3 bg-bambu-card rounded-lg border border-bambu-dark-tertiary">
          <div className="flex items-center gap-2 text-sm">
            <File className="w-4 h-4 text-bambu-green" />
            <span className="text-bambu-gray">Files:</span>
            <span className="text-white font-medium">{stats.total_files}</span>
          </div>
          <div className="flex items-center gap-2 text-sm">
            <FolderOpen className="w-4 h-4 text-blue-400" />
            <span className="text-bambu-gray">Folders:</span>
            <span className="text-white font-medium">{stats.total_folders}</span>
          </div>
          <div className="flex items-center gap-2 text-sm">
            <HardDrive className="w-4 h-4 text-amber-400" />
            <span className="text-bambu-gray">Size:</span>
            <span className="text-white font-medium">{formatFileSize(stats.total_size_bytes)}</span>
          </div>
          <div className="flex items-center gap-2 text-sm ml-auto">
            <span className="text-bambu-gray">Free:</span>
            <span className={`font-medium ${isDiskSpaceLow ? 'text-amber-500' : 'text-white'}`}>
              {formatFileSize(stats.disk_free_bytes)}
            </span>
          </div>
        </div>
      )}

      {/* Main content */}
      <div className="flex-1 flex gap-6 min-h-0">
        {/* Folder sidebar */}
        <div className="w-64 flex-shrink-0 bg-bambu-card rounded-lg border border-bambu-dark-tertiary overflow-hidden flex flex-col">
          <div className="p-3 border-b border-bambu-dark-tertiary">
            <h2 className="text-sm font-medium text-white">Folders</h2>
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
              <span className="text-sm">All Files</span>
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
              />
            ))}
          </div>
        </div>

        {/* Files area */}
        <div className="flex-1 flex flex-col min-w-0">
          {/* Search, Filter, Sort toolbar */}
          {files && files.length > 0 && (
            <div className="flex items-center gap-3 mb-4 p-3 bg-bambu-card rounded-lg border border-bambu-dark-tertiary">
              {/* Search */}
              <div className="relative flex-1 max-w-xs">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-bambu-gray" />
                <input
                  type="text"
                  placeholder="Search files..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="w-full pl-9 pr-3 py-1.5 bg-bambu-dark border border-bambu-dark-tertiary rounded text-sm text-white placeholder-bambu-gray focus:outline-none focus:border-bambu-green"
                />
              </div>

              {/* Type filter */}
              <div className="flex items-center gap-2">
                <Filter className="w-4 h-4 text-bambu-gray" />
                <select
                  value={filterType}
                  onChange={(e) => setFilterType(e.target.value)}
                  className="bg-bambu-dark border border-bambu-dark-tertiary rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-bambu-green"
                >
                  <option value="all">All types</option>
                  {fileTypes.map((type) => (
                    <option key={type} value={type}>
                      {type.toUpperCase()}
                    </option>
                  ))}
                </select>
              </div>

              {/* Sort */}
              <div className="flex items-center gap-2">
                <select
                  value={sortField}
                  onChange={(e) => setSortField(e.target.value as SortField)}
                  className="bg-bambu-dark border border-bambu-dark-tertiary rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-bambu-green"
                >
                  <option value="date">Date</option>
                  <option value="name">Name</option>
                  <option value="size">Size</option>
                  <option value="type">Type</option>
                  <option value="prints">Prints</option>
                </select>
                <button
                  onClick={() => setSortDirection((d) => (d === 'asc' ? 'desc' : 'asc'))}
                  className="p-1.5 rounded bg-bambu-dark border border-bambu-dark-tertiary hover:border-bambu-green transition-colors"
                  title={sortDirection === 'asc' ? 'Ascending' : 'Descending'}
                >
                  {sortDirection === 'asc' ? (
                    <SortAsc className="w-4 h-4 text-white" />
                  ) : (
                    <SortDesc className="w-4 h-4 text-white" />
                  )}
                </button>
              </div>

              {/* Results count */}
              {(searchQuery || filterType !== 'all') && (
                <span className="text-sm text-bambu-gray">
                  {filteredAndSortedFiles.length} of {files.length} files
                </span>
              )}
            </div>
          )}

          {/* External folder rescan button */}
          {selectedFolderId !== null && folders && (
            (() => {
              const selectedFolder = folders.find(f => {
                const findInTree = (folder: any): any => {
                  if (folder.id === selectedFolderId) return folder;
                  return folder.children?.find((child: any) => findInTree(child));
                };
                return findInTree(f);
              });
              return selectedFolder?.is_external ? (
                <div className="flex items-center gap-2 mb-4 p-2 bg-blue-500/10 border border-blue-500/30 rounded-lg">
                  <span className="text-sm text-blue-400">External folder</span>
                  <div className="flex-1" />
                  {selectedFolder.external_last_scan && (
                    <span className="text-xs text-gray-400">
                      Last scanned: {new Date(selectedFolder.external_last_scan).toLocaleString()}
                    </span>
                  )}
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => selectedFolderId && rescanFolderMutation.mutate(selectedFolderId)}
                    disabled={rescanFolderMutation.isPending}
                  >
                    {rescanFolderMutation.isPending && <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" />}
                    Rescan
                  </Button>
                </div>
              ) : null;
            })()
          )}

          {/* Selection toolbar */}
          {filteredAndSortedFiles.length > 0 && (
            <div className="flex items-center gap-2 mb-4 p-2 bg-bambu-card rounded-lg border border-bambu-dark-tertiary">
              {/* Select all / Deselect all */}
              {selectedFiles.length === filteredAndSortedFiles.length && selectedFiles.length > 0 ? (
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={handleDeselectAll}
                >
                  <Square className="w-4 h-4 mr-1" />
                  Deselect All
                </Button>
              ) : (
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={handleSelectAll}
                >
                  <CheckSquare className="w-4 h-4 mr-1" />
                  Select All
                </Button>
              )}

              {selectedFiles.length > 0 && (
                <>
                  <span className="text-sm text-bambu-gray ml-2">
                    {selectedFiles.length} selected
                  </span>
                  <div className="flex-1" />
                  {selectedSlicedFiles.length === 1 && (
                    <Button
                      variant="primary"
                      size="sm"
                      onClick={() => setPrintMultiFile(selectedSlicedFiles[0])}
                    >
                      <Play className="w-4 h-4 mr-1" />
                      Print
                    </Button>
                  )}
                  {selectedSlicedFiles.length > 0 && (
                    <Button
                      variant={selectedSlicedFiles.length === 1 ? 'secondary' : 'primary'}
                      size="sm"
                      onClick={() => addToQueueMutation.mutate(selectedSlicedFiles.map(f => f.id))}
                      disabled={addToQueueMutation.isPending}
                    >
                      <Clock className="w-4 h-4 mr-1" />
                      {addToQueueMutation.isPending ? 'Adding...' : `Add to Queue${selectedSlicedFiles.length < selectedFiles.length ? ` (${selectedSlicedFiles.length})` : ''}`}
                    </Button>
                  )}
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => setShowMoveModal(true)}
                  >
                    <MoveRight className="w-4 h-4 mr-1" />
                    Move
                  </Button>
                  <Button
                    variant="danger"
                    size="sm"
                    disabled={isSelectedFolderReadonly}
                    onClick={() => {
                      if (selectedFiles.length === 1) {
                        setDeleteConfirm({ type: 'file', id: selectedFiles[0] });
                      } else {
                        setDeleteConfirm({ type: 'bulk', id: 0, count: selectedFiles.length });
                      }
                    }}
                    title={isSelectedFolderReadonly ? "Cannot delete from read-only external folder" : ""}
                  >
                    <Trash2 className="w-4 h-4 mr-1" />
                    Delete
                  </Button>
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={handleDeselectAll}
                  >
                    Clear
                  </Button>
                </>
              )}
            </div>
          )}

          {/* File grid/list */}
          {isLoading ? (
            <div className="flex-1 flex items-center justify-center">
              <div className="flex flex-col items-center gap-3">
                <Loader2 className="w-8 h-8 animate-spin text-bambu-green" />
                <p className="text-sm text-bambu-gray">Loading files...</p>
              </div>
            </div>
          ) : files?.length === 0 ? (
            <div className="flex-1 flex flex-col items-center justify-center">
              <div className="p-4 bg-bambu-dark rounded-2xl mb-4">
                <FileBox className="w-12 h-12 text-bambu-gray/50" />
              </div>
              <h3 className="text-lg font-medium text-white mb-2">
                {selectedFolderId !== null ? 'Folder is empty' : 'No files yet'}
              </h3>
              <p className="text-bambu-gray text-center max-w-md mb-6">
                {selectedFolderId !== null
                  ? 'Upload files or move files into this folder to get started.'
                  : 'Upload files to start organizing your print-related files.'}
              </p>
              <Button onClick={() => setShowUploadModal(true)}>
                <Plus className="w-4 h-4 mr-2" />
                Upload Files
              </Button>
            </div>
          ) : filteredAndSortedFiles.length === 0 ? (
            <div className="flex-1 flex flex-col items-center justify-center">
              <div className="p-4 bg-bambu-dark rounded-2xl mb-4">
                <Search className="w-12 h-12 text-bambu-gray/50" />
              </div>
              <h3 className="text-lg font-medium text-white mb-2">No matching files</h3>
              <p className="text-bambu-gray text-center max-w-md mb-6">
                No files match your current search or filter criteria.
              </p>
              <Button variant="secondary" onClick={() => { setSearchQuery(''); setFilterType('all'); }}>
                Clear filters
              </Button>
            </div>
          ) : viewMode === 'grid' ? (
            <div className="flex-1 overflow-y-auto">
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6 gap-4">
                {filteredAndSortedFiles.map((file) => (
                  <FileCard
                    key={file.id}
                    file={file}
                    isSelected={selectedFiles.includes(file.id)}
                    onSelect={handleFileSelect}
                    onDelete={(id) => setDeleteConfirm({ type: 'file', id })}
                    onDownload={handleDownload}
                    onAddToQueue={(id) => addToQueueMutation.mutate([id])}
                    onPrint={setPrintFile}
                    onRename={(f) => setRenameItem({ type: 'file', id: f.id, name: f.filename })}
                  />
                ))}
              </div>
            </div>
          ) : (
            <div className="flex-1 overflow-y-auto">
              <div className="bg-bambu-card rounded-lg border border-bambu-dark-tertiary overflow-hidden">
                {/* List header */}
                <div className="grid grid-cols-[auto_1fr_100px_100px_100px_80px] gap-4 px-4 py-2 bg-bambu-dark-secondary border-b border-bambu-dark-tertiary text-xs text-bambu-gray font-medium">
                  <div className="w-6" />
                  <div>Name</div>
                  <div>Type</div>
                  <div>Size</div>
                  <div>Prints</div>
                  <div />
                </div>
                {/* List rows */}
                {filteredAndSortedFiles.map((file) => (
                  <div
                    key={file.id}
                    className={`grid grid-cols-[auto_1fr_100px_100px_100px_80px] gap-4 px-4 py-3 items-center border-b border-bambu-dark-tertiary last:border-b-0 cursor-pointer hover:bg-bambu-dark/50 transition-colors ${
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
                              src={api.getLibraryFileThumbnailUrl(file.id)}
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
                                src={api.getLibraryFileThumbnailUrl(file.id)}
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
                            onClick={() => setPrintFile(file)}
                            className="p-1.5 rounded hover:bg-bambu-dark text-bambu-gray hover:text-bambu-green transition-colors"
                            title="Print"
                          >
                            <Printer className="w-4 h-4" />
                          </button>
                          <button
                            onClick={() => addToQueueMutation.mutate([file.id])}
                            className="p-1.5 rounded hover:bg-bambu-dark text-bambu-gray hover:text-white transition-colors"
                            title="Add to Queue"
                            disabled={addToQueueMutation.isPending}
                          >
                            <Clock className="w-4 h-4" />
                          </button>
                        </>
                      )}
                      <button
                        onClick={() => handleDownload(file.id)}
                        className="p-1.5 rounded hover:bg-bambu-dark text-bambu-gray hover:text-white transition-colors"
                        title="Download"
                      >
                        <Download className="w-4 h-4" />
                      </button>
                      <button
                        onClick={() => setRenameItem({ type: 'file', id: file.id, name: file.filename })}
                        className="p-1.5 rounded hover:bg-bambu-dark text-bambu-gray hover:text-white transition-colors"
                        title="Rename"
                      >
                        <Pencil className="w-4 h-4" />
                      </button>
                      <button
                        onClick={() => setDeleteConfirm({ type: 'file', id: file.id })}
                        className="p-1.5 rounded hover:bg-bambu-dark text-bambu-gray hover:text-red-400 transition-colors"
                        title="Delete"
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
        />
      )}

      {showExternalFolderModal && (
        <ExternalFolderModal
          parentId={selectedFolderId}
          onClose={() => setShowExternalFolderModal(false)}
          onSuccess={() => {
            setShowExternalFolderModal(false);
            // Refresh folder list
            queryClient.invalidateQueries({ queryKey: ['library-folders'] });
          }}
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
        />
      )}

      {showUploadModal && (
        <UploadModal
          folderId={selectedFolderId}
          onClose={() => setShowUploadModal(false)}
          onUploadComplete={handleUploadComplete}
        />
      )}

      {linkFolder && (
        <LinkFolderModal
          folder={linkFolder}
          onClose={() => setLinkFolder(null)}
          onLink={(data) => updateFolderMutation.mutate({ id: linkFolder.id, data })}
          isLoading={updateFolderMutation.isPending}
        />
      )}

      {deleteConfirm && (
        <ConfirmModal
          title={
            deleteConfirm.type === 'folder'
              ? 'Delete Folder'
              : deleteConfirm.type === 'bulk'
              ? `Delete ${deleteConfirm.count} Files`
              : 'Delete File'
          }
          message={
            deleteConfirm.type === 'folder'
              ? 'Are you sure you want to delete this folder? All files inside will also be deleted.'
              : deleteConfirm.type === 'bulk'
              ? `Are you sure you want to delete ${deleteConfirm.count} selected files? This action cannot be undone.`
              : 'Are you sure you want to delete this file?'
          }
          confirmText="Delete"
          variant="danger"
          isLoading={isDeleting}
          loadingText="Deleting..."
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
        />
      )}
    </div>
  );
}
