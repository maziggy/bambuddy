import { useState, useEffect, type ReactNode } from 'react';
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core';
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  rectSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { GripVertical, Eye, EyeOff, RotateCcw, Maximize2, Minimize2 } from 'lucide-react';
import { Button } from './Button';

export interface DashboardWidget {
  id: string;
  title: string;
  component: ReactNode;
  defaultVisible?: boolean;
  defaultSize?: 1 | 2 | 4; // 1 = quarter, 2 = half, 4 = full width (default)
}

interface DashboardProps {
  widgets: DashboardWidget[];
  storageKey: string;
  columns?: number;
}

interface LayoutState {
  order: string[];
  hidden: string[];
  sizes: Record<string, 1 | 2 | 4>;
}

function SortableWidget({
  id,
  title,
  children,
  isHidden,
  size,
  onToggleVisibility,
  onToggleSize,
}: {
  id: string;
  title: string;
  children: ReactNode;
  isHidden: boolean;
  size: 1 | 2 | 4;
  onToggleVisibility: () => void;
  onToggleSize: () => void;
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  };

  if (isHidden) return null;

  return (
    <div
      ref={setNodeRef}
      style={{
        ...style,
        gridColumn: `span ${size}`,
      }}
      className={`bg-bambu-dark-secondary rounded-xl border border-bambu-dark-tertiary overflow-hidden ${
        isDragging ? 'ring-2 ring-bambu-green shadow-lg' : ''
      }`}
    >
      {/* Widget Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-bambu-dark-tertiary bg-bambu-dark/30">
        <div className="flex items-center gap-2">
          <button
            {...attributes}
            {...listeners}
            className="cursor-grab active:cursor-grabbing p-1 hover:bg-bambu-dark-tertiary rounded transition-colors"
            title="Drag to reorder"
          >
            <GripVertical className="w-6 h-6 md:w-4 md:h-4 text-bambu-gray" />
          </button>
          <h3 className="text-sm font-medium text-white">{title}</h3>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={onToggleSize}
            className="p-1 hover:bg-bambu-dark-tertiary rounded transition-colors"
            title={`Size: ${size === 1 ? '1/4' : size === 2 ? '1/2' : 'Full'} - Click to cycle`}
          >
            {size === 4 ? (
              <Minimize2 className="w-4 h-4 text-bambu-gray hover:text-white" />
            ) : (
              <Maximize2 className="w-4 h-4 text-bambu-gray hover:text-white" />
            )}
          </button>
          <button
            onClick={onToggleVisibility}
            className="p-1 hover:bg-bambu-dark-tertiary rounded transition-colors"
            title="Hide widget"
          >
            <EyeOff className="w-4 h-4 text-bambu-gray hover:text-white" />
          </button>
        </div>
      </div>
      {/* Widget Content */}
      <div className="p-4">{children}</div>
    </div>
  );
}

export function Dashboard({ widgets, storageKey, columns = 4 }: DashboardProps) {
  // Build default sizes from widget definitions
  const getDefaultSizes = () => {
    const sizes: Record<string, 1 | 2 | 4> = {};
    widgets.forEach((w) => {
      sizes[w.id] = w.defaultSize || 4;
    });
    return sizes;
  };

  const [layout, setLayout] = useState<LayoutState>(() => {
    // Load saved layout from localStorage
    const saved = localStorage.getItem(storageKey);
    if (saved) {
      try {
        const parsed = JSON.parse(saved);
        // Ensure sizes exist (for backwards compatibility)
        if (!parsed.sizes) {
          parsed.sizes = getDefaultSizes();
        }
        return parsed;
      } catch {
        // Invalid JSON, use default
      }
    }
    // Default layout: all widgets visible in original order
    return {
      order: widgets.map((w) => w.id),
      hidden: widgets.filter((w) => w.defaultVisible === false).map((w) => w.id),
      sizes: getDefaultSizes(),
    };
  });

  const [showHiddenPanel, setShowHiddenPanel] = useState(false);

  // Save layout to localStorage whenever it changes
  useEffect(() => {
    localStorage.setItem(storageKey, JSON.stringify(layout));
  }, [layout, storageKey]);

  // Ensure all widget IDs are in the order array (for newly added widgets)
  useEffect(() => {
    const allIds = widgets.map((w) => w.id);
    const missingIds = allIds.filter((id) => !layout.order.includes(id));
    if (missingIds.length > 0) {
      setLayout((prev) => ({
        ...prev,
        order: [...prev.order, ...missingIds],
      }));
    }
  }, [widgets, layout.order]);

  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: {
        distance: 8,
      },
    }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    })
  );

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;

    if (over && active.id !== over.id) {
      setLayout((prev) => {
        const oldIndex = prev.order.indexOf(active.id as string);
        const newIndex = prev.order.indexOf(over.id as string);
        return {
          ...prev,
          order: arrayMove(prev.order, oldIndex, newIndex),
        };
      });
    }
  };

  const toggleVisibility = (id: string) => {
    setLayout((prev) => ({
      ...prev,
      hidden: prev.hidden.includes(id)
        ? prev.hidden.filter((h) => h !== id)
        : [...prev.hidden, id],
    }));
  };

  const toggleSize = (id: string) => {
    setLayout((prev) => {
      const currentSize = prev.sizes[id] || 4;
      // Cycle: 1 → 2 → 4 → 1
      const nextSize = currentSize === 1 ? 2 : currentSize === 2 ? 4 : 1;
      return {
        ...prev,
        sizes: {
          ...prev.sizes,
          [id]: nextSize as 1 | 2 | 4,
        },
      };
    });
  };

  const resetLayout = () => {
    const defaultLayout = {
      order: widgets.map((w) => w.id),
      hidden: widgets.filter((w) => w.defaultVisible === false).map((w) => w.id),
      sizes: getDefaultSizes(),
    };
    setLayout(defaultLayout);
  };

  // Get ordered widgets
  const orderedWidgets = layout.order
    .map((id) => widgets.find((w) => w.id === id))
    .filter(Boolean) as DashboardWidget[];

  const visibleWidgets = orderedWidgets.filter((w) => !layout.hidden.includes(w.id));
  const hiddenWidgets = orderedWidgets.filter((w) => layout.hidden.includes(w.id));

  return (
    <div className="space-y-4">
      {/* Dashboard Controls */}
      <div className="flex items-center justify-end gap-2">
        {hiddenWidgets.length > 0 && (
          <Button
            variant="secondary"
            size="sm"
            onClick={() => setShowHiddenPanel(!showHiddenPanel)}
          >
            <Eye className="w-4 h-4" />
            {hiddenWidgets.length} Hidden
          </Button>
        )}
        <Button variant="secondary" size="sm" onClick={resetLayout}>
          <RotateCcw className="w-4 h-4" />
          Reset Layout
        </Button>
      </div>

      {/* Hidden Widgets Panel */}
      {showHiddenPanel && hiddenWidgets.length > 0 && (
        <div className="p-4 bg-bambu-dark rounded-xl border border-bambu-dark-tertiary">
          <p className="text-sm text-bambu-gray mb-3">Hidden widgets (click to show):</p>
          <div className="flex flex-wrap gap-2">
            {hiddenWidgets.map((widget) => (
              <button
                key={widget.id}
                onClick={() => toggleVisibility(widget.id)}
                className="px-3 py-1.5 bg-bambu-dark-tertiary hover:bg-bambu-green/20 rounded-lg text-sm text-white transition-colors flex items-center gap-2"
              >
                <Eye className="w-3 h-3" />
                {widget.title}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Draggable Widgets Grid */}
      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragEnd={handleDragEnd}
      >
        <SortableContext items={visibleWidgets.map((w) => w.id)} strategy={rectSortingStrategy}>
          <div
            className="grid gap-6"
            style={{
              gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
            }}
          >
            {visibleWidgets.map((widget) => (
              <SortableWidget
                key={widget.id}
                id={widget.id}
                title={widget.title}
                isHidden={layout.hidden.includes(widget.id)}
                size={layout.sizes[widget.id] || 2}
                onToggleVisibility={() => toggleVisibility(widget.id)}
                onToggleSize={() => toggleSize(widget.id)}
              >
                {widget.component}
              </SortableWidget>
            ))}
          </div>
        </SortableContext>
      </DndContext>

      {visibleWidgets.length === 0 && (
        <div className="text-center py-12 text-bambu-gray">
          <p>All widgets are hidden.</p>
          <Button className="mt-4" onClick={resetLayout}>
            Reset Layout
          </Button>
        </div>
      )}
    </div>
  );
}
