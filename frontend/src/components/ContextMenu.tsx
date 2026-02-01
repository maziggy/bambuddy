import { useEffect, useRef, useState, useLayoutEffect } from 'react';
import { ChevronRight } from 'lucide-react';

export interface ContextMenuItem {
  label: string;
  icon?: React.ReactNode;
  onClick: () => void;
  danger?: boolean;
  disabled?: boolean;
  divider?: boolean;
  submenu?: ContextMenuItem[];
  title?: string;
}

interface ContextMenuProps {
  x: number;
  y: number;
  items: ContextMenuItem[];
  onClose: () => void;
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

    const handleScroll = () => {
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
              <div
                className={`absolute min-w-[160px] bg-bambu-dark-secondary border border-bambu-dark-tertiary rounded-lg shadow-xl py-1 overflow-hidden max-h-[300px] overflow-y-auto z-[60] ${
                  openSubmenuLeft ? 'right-full mr-1' : 'left-full ml-1'
                } ${submenuPositions[index] === 'bottom' ? 'bottom-0' : 'top-0'}`}
                onMouseEnter={() => {
                  if (submenuTimeoutRef.current) {
                    clearTimeout(submenuTimeoutRef.current);
                    submenuTimeoutRef.current = null;
                  }
                }}
                onMouseLeave={() => handleMouseLeaveSubmenu()}
              >
                {item.submenu!.map((subItem, subIndex) => (
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
                    {subItem.label}
                  </button>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
