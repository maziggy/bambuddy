import { useEffect, useRef, useState, useLayoutEffect } from 'react';
import { ChevronRight, Search } from 'lucide-react';

export interface ContextMenuItem {
  label: string;
  icon?: React.ReactNode;
  onClick: () => void;
  danger?: boolean;
  disabled?: boolean;
  divider?: boolean;
  submenu?: ContextMenuItem[];
  // When set on an item with a submenu, render a search input above the
  // submenu items that filters by label (case-insensitive).
  submenuSearchPlaceholder?: string;
  title?: string;
}

interface ContextMenuProps {
  x: number;
  y: number;
  items: ContextMenuItem[];
  onClose: () => void;
}

interface SubmenuPanelProps {
  items: ContextMenuItem[];
  searchPlaceholder?: string;
  onClose: () => void;
  className: string;
  onMouseEnter: () => void;
  onMouseLeave: () => void;
}

function SubmenuPanel({
  items,
  searchPlaceholder,
  onClose,
  className,
  onMouseEnter,
  onMouseLeave,
}: SubmenuPanelProps) {
  const [query, setQuery] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (searchPlaceholder) {
      // Defer focus so it survives the mouse event that opened the submenu.
      const id = window.setTimeout(() => inputRef.current?.focus(), 0);
      return () => window.clearTimeout(id);
    }
  }, [searchPlaceholder]);

  const trimmed = query.trim().toLowerCase();
  const filteredItems = searchPlaceholder && trimmed
    ? items.filter((i) => i.label.toLowerCase().includes(trimmed))
    : items;

  return (
    <div
      className={className}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
    >
      {searchPlaceholder && (
        <div className="sticky top-0 z-[1] px-2 py-1.5 bg-bambu-dark-secondary border-b border-bambu-dark-tertiary">
          <div className="relative">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-bambu-gray pointer-events-none" />
            <input
              ref={inputRef}
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  const first = filteredItems.find((i) => !i.disabled);
                  if (first) {
                    first.onClick();
                    onClose();
                  }
                }
              }}
              placeholder={searchPlaceholder}
              className="w-full pl-7 pr-2 py-1 text-sm bg-bambu-dark border border-bambu-dark-tertiary rounded text-white placeholder-bambu-gray focus:border-bambu-green focus:outline-none"
            />
          </div>
        </div>
      )}
      {filteredItems.map((subItem, subIndex) => (
        <button
          key={subIndex}
          onClick={() => {
            if (!subItem.disabled) {
              subItem.onClick();
              onClose();
            }
          }}
          disabled={subItem.disabled}
          className={`w-full flex items-center gap-2 px-3 py-2 text-sm text-left transition-colors ${
            subItem.disabled
              ? 'text-bambu-gray cursor-not-allowed'
              : subItem.danger
              ? 'text-red-400 hover:bg-red-400/10'
              : 'text-white hover:bg-bambu-dark-tertiary'
          }`}
        >
          {subItem.icon && <span className="w-4 h-4 flex-shrink-0 flex items-center justify-center">{subItem.icon}</span>}
          <span className="flex-1 truncate">{subItem.label}</span>
        </button>
      ))}
      {searchPlaceholder && filteredItems.length === 0 && (
        <div className="px-3 py-2 text-sm text-bambu-gray text-center italic">—</div>
      )}
    </div>
  );
}

export function ContextMenu({ x, y, items, onClose }: ContextMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null);
  const [activeSubmenu, setActiveSubmenu] = useState<number | null>(null);
  const submenuTimeoutRef = useRef<number | null>(null);
  const [position, setPosition] = useState({ x, y, visible: false });
  const [openSubmenuLeft, setOpenSubmenuLeft] = useState(false);
  const [submenuPositions, setSubmenuPositions] = useState<Record<number, 'top' | 'bottom'>>({});

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        onClose();
      }
    };

    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };

    const handleScroll = (e: Event) => {
      // Internal submenu scroll (overflow-y-auto on the submenu panel) must
      // not dismiss the menu — only close on scroll outside our own subtree.
      if (menuRef.current && menuRef.current.contains(e.target as Node)) {
        return;
      }
      onClose();
    };

    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleEscape);
    document.addEventListener('scroll', handleScroll, true);

    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleEscape);
      document.removeEventListener('scroll', handleScroll, true);
      if (submenuTimeoutRef.current) {
        clearTimeout(submenuTimeoutRef.current);
      }
    };
  }, [onClose]);

  // Adjust position to keep menu in viewport - use useLayoutEffect for synchronous measurement
  useLayoutEffect(() => {
    if (menuRef.current) {
      // Force a reflow to get accurate measurements
      menuRef.current.style.visibility = 'hidden';
      menuRef.current.style.display = 'block';

      const rect = menuRef.current.getBoundingClientRect();
      const viewportWidth = window.innerWidth;
      const viewportHeight = window.innerHeight;
      const padding = 8;

      let adjustedX = x;
      let adjustedY = y;

      // Adjust horizontal position - if menu would overflow right, shift left
      if (x + rect.width > viewportWidth - padding) {
        adjustedX = Math.max(padding, viewportWidth - rect.width - padding);
      }
      // Also check if starting position is negative
      if (adjustedX < padding) {
        adjustedX = padding;
      }

      // Adjust vertical position - if menu would overflow bottom, shift up
      if (y + rect.height > viewportHeight - padding) {
        adjustedY = Math.max(padding, viewportHeight - rect.height - padding);
      }
      // Also check if starting position is negative
      if (adjustedY < padding) {
        adjustedY = padding;
      }

      // Check if submenus should open to the left (more space on left than right)
      const submenuWidth = 180;
      const spaceOnRight = viewportWidth - adjustedX - rect.width;
      const spaceOnLeft = adjustedX;
      // Only open left if there's not enough space on right AND there's enough space on left
      setOpenSubmenuLeft(spaceOnRight < submenuWidth && spaceOnLeft > submenuWidth);

      setPosition({ x: adjustedX, y: adjustedY, visible: true });
    }
  }, [x, y]);

  const handleMouseEnterSubmenu = (index: number, element: HTMLElement) => {
    if (submenuTimeoutRef.current) {
      clearTimeout(submenuTimeoutRef.current);
      submenuTimeoutRef.current = null;
    }

    // Calculate if submenu should open upward or downward
    const rect = element.getBoundingClientRect();
    const viewportHeight = window.innerHeight;
    const submenuMaxHeight = 300; // matches max-h-[300px]
    const padding = 8;

    // Check if there's enough space below for the submenu
    const spaceBelow = viewportHeight - rect.top - padding;
    const shouldOpenUpward = spaceBelow < submenuMaxHeight && rect.top > submenuMaxHeight;

    setSubmenuPositions(prev => ({ ...prev, [index]: shouldOpenUpward ? 'bottom' : 'top' }));
    setActiveSubmenu(index);
  };

  const handleMouseLeaveSubmenu = () => {
    submenuTimeoutRef.current = window.setTimeout(() => {
      setActiveSubmenu(null);
    }, 150);
  };

  return (
    <div
      ref={menuRef}
      className="fixed z-50 min-w-[180px] max-w-[280px] bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-xl py-1"
      style={{
        left: position.x,
        top: position.y,
        visibility: position.visible ? 'visible' : 'hidden'
      }}
    >
      {items.map((item, index) => {
        if (item.divider) {
          return <div key={index} className="my-1 border-t border-bambu-dark-tertiary" />;
        }

        const hasSubmenu = item.submenu && item.submenu.length > 0;

        return (
          <div
            key={index}
            className="relative"
            onMouseEnter={(e) => hasSubmenu && handleMouseEnterSubmenu(index, e.currentTarget)}
            onMouseLeave={() => hasSubmenu && handleMouseLeaveSubmenu()}
          >
            <button
              onMouseEnter={(e) => hasSubmenu && handleMouseEnterSubmenu(index, e.currentTarget.parentElement!)}
              onClick={() => {
                if (hasSubmenu) {
                  // Toggle submenu on click as well
                  setActiveSubmenu(activeSubmenu === index ? null : index);
                } else if (!item.disabled) {
                  item.onClick();
                  onClose();
                }
              }}
              disabled={item.disabled}
              title={item.title}
              className={`w-full flex items-center gap-2 px-3 py-2 text-sm text-left transition-colors ${
                item.disabled
                  ? 'text-bambu-gray cursor-not-allowed'
                  : item.danger
                  ? 'text-red-400 hover:bg-red-400/10'
                  : 'text-white hover:bg-bambu-dark-tertiary'
              } ${hasSubmenu && activeSubmenu === index ? 'bg-bambu-dark-tertiary' : ''}`}
            >
              {item.icon && <span className="w-4 h-4 flex-shrink-0 flex items-center justify-center">{item.icon}</span>}
              <span className="flex-1">{item.label}</span>
              {hasSubmenu && <ChevronRight className="w-4 h-4 text-bambu-gray" />}
            </button>
            {/* Submenu */}
            {hasSubmenu && activeSubmenu === index && (
              <SubmenuPanel
                items={item.submenu!}
                searchPlaceholder={item.submenuSearchPlaceholder}
                onClose={onClose}
                className={`absolute min-w-[200px] bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-xl py-1 overflow-hidden max-h-[300px] overflow-y-auto z-[60] ${
                  openSubmenuLeft ? 'right-full mr-1' : 'left-full ml-1'
                } ${submenuPositions[index] === 'bottom' ? 'bottom-0' : 'top-0'}`}
                onMouseEnter={() => {
                  if (submenuTimeoutRef.current) {
                    clearTimeout(submenuTimeoutRef.current);
                    submenuTimeoutRef.current = null;
                  }
                }}
                onMouseLeave={() => handleMouseLeaveSubmenu()}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}
