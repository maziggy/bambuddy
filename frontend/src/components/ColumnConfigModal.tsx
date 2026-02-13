import { useState, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { GripVertical, Eye, EyeOff, ChevronUp, ChevronDown, RotateCcw } from 'lucide-react';
import { Card, CardContent } from './Card';
import { Button } from './Button';

export interface ColumnConfig {
  id: string;
  label: string;
  visible: boolean;
}

interface ColumnConfigModalProps {
  isOpen: boolean;
  onClose: () => void;
  columns: ColumnConfig[];
  defaultColumns: ColumnConfig[];
  onSave: (columns: ColumnConfig[]) => void;
}

export function ColumnConfigModal({ isOpen, onClose, columns, defaultColumns, onSave }: ColumnConfigModalProps) {
  const { t } = useTranslation();
  const [localColumns, setLocalColumns] = useState<ColumnConfig[]>(columns);
  const [draggedIndex, setDraggedIndex] = useState<number | null>(null);
  const draggedIndexRef = useRef<number | null>(null);

  useEffect(() => {
    if (isOpen) {
      setLocalColumns(columns.map((c) => ({ ...c })));
    }
  }, [isOpen, columns]);

  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const toggleVisibility = (index: number) => {
    setLocalColumns((prev) =>
      prev.map((col, i) => (i === index ? { ...col, visible: !col.visible } : col))
    );
  };

  const moveColumn = (fromIndex: number, toIndex: number) => {
    if (toIndex < 0 || toIndex >= localColumns.length) return;
    setLocalColumns((prev) => {
      const newColumns = [...prev];
      const [moved] = newColumns.splice(fromIndex, 1);
      newColumns.splice(toIndex, 0, moved);
      return newColumns;
    });
  };

  const handleDragStart = (e: React.DragEvent, index: number) => {
    draggedIndexRef.current = index;
    setDraggedIndex(index);
    e.dataTransfer.effectAllowed = 'move';
  };

  const handleDragOver = (e: React.DragEvent, index: number) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    const from = draggedIndexRef.current;
    if (from !== null && from !== index) {
      moveColumn(from, index);
      draggedIndexRef.current = index;
      setDraggedIndex(index);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
  };

  const handleDragEnd = () => {
    draggedIndexRef.current = null;
    setDraggedIndex(null);
  };

  const resetToDefaults = () => {
    setLocalColumns(defaultColumns.map((c) => ({ ...c })));
  };

  const handleSave = () => {
    onSave(localColumns);
    onClose();
  };

  const visibleCount = localColumns.filter((c) => c.visible).length;

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <Card className="w-full max-w-md max-h-[80vh] flex flex-col" onClick={(e: React.MouseEvent) => e.stopPropagation()}>
        <CardContent className="p-6 flex flex-col min-h-0">
          {/* Header */}
          <h3 className="text-lg font-semibold text-white mb-2">{t('inventory.configureColumns')}</h3>
          <p className="text-sm text-bambu-gray mb-4">
            {t('inventory.configureColumnsDesc')}
            <span className="ml-2 text-bambu-gray/60">
              ({visibleCount} {t('inventory.of')} {localColumns.length} {t('inventory.visible')})
            </span>
          </p>

          {/* Column list */}
          <div className="space-y-1 overflow-y-auto flex-1 min-h-0 pr-1">
            {localColumns.map((column, index) => (
              <div
                key={column.id}
                className={`flex items-center gap-2 p-2 rounded-lg border transition-colors ${
                  draggedIndex === index
                    ? 'border-bambu-green bg-bambu-green/10'
                    : 'border-bambu-dark-tertiary bg-bambu-dark-tertiary/50'
                } ${!column.visible ? 'opacity-50' : ''}`}
                draggable
                onDragStart={(e) => handleDragStart(e, index)}
                onDragOver={(e) => handleDragOver(e, index)}
                onDrop={handleDrop}
                onDragEnd={handleDragEnd}
              >
                {/* Drag Handle */}
                <div className="cursor-grab text-bambu-gray/50 hover:text-bambu-gray">
                  <GripVertical className="w-4 h-4" />
                </div>

                {/* Column Name */}
                <span className="flex-1 font-medium text-sm text-white">{column.label}</span>

                {/* Move Buttons */}
                <div className="flex items-center gap-0.5">
                  <button
                    onClick={() => moveColumn(index, index - 1)}
                    disabled={index === 0}
                    className="p-1 rounded text-bambu-gray hover:bg-bambu-dark-secondary disabled:opacity-30 disabled:cursor-not-allowed"
                    title={t('inventory.moveUp')}
                  >
                    <ChevronUp className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => moveColumn(index, index + 1)}
                    disabled={index === localColumns.length - 1}
                    className="p-1 rounded text-bambu-gray hover:bg-bambu-dark-secondary disabled:opacity-30 disabled:cursor-not-allowed"
                    title={t('inventory.moveDown')}
                  >
                    <ChevronDown className="w-4 h-4" />
                  </button>
                </div>

                {/* Visibility Toggle */}
                <button
                  onClick={() => toggleVisibility(index)}
                  className={`p-1.5 rounded transition-colors ${
                    column.visible
                      ? 'text-bambu-green hover:bg-bambu-green/10'
                      : 'text-bambu-gray/50 hover:bg-bambu-dark-secondary'
                  }`}
                  title={column.visible ? t('inventory.hideColumn') : t('inventory.showColumn')}
                >
                  {column.visible ? <Eye className="w-4 h-4" /> : <EyeOff className="w-4 h-4" />}
                </button>
              </div>
            ))}
          </div>

          {/* Footer */}
          <div className="flex items-center gap-3 mt-4 pt-4 border-t border-bambu-dark-tertiary">
            <Button variant="secondary" onClick={resetToDefaults} className="mr-auto">
              <RotateCcw className="w-4 h-4" />
              {t('inventory.reset')}
            </Button>
            <Button variant="secondary" onClick={onClose}>
              {t('inventory.cancel')}
            </Button>
            <Button onClick={handleSave}>
              {t('inventory.applyChanges')}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
