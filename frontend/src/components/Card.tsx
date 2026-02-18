import type { ReactNode, MouseEvent, HTMLAttributes } from 'react';

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode;
  className?: string;
  onClick?: (e: MouseEvent) => void;
  onContextMenu?: (e: MouseEvent) => void;
}

export function Card({ children, className = '', onClick, onContextMenu, ...rest }: CardProps) {
  return (
    <div
      className={`bg-bambu-dark-secondary rounded-xl border border-bambu-dark-tertiary card-shadow ${className}`}
      onClick={onClick}
      onContextMenu={onContextMenu}
      {...rest}
    >
      {children}
    </div>
  );
}

export function CardHeader({ children, className = '' }: CardProps) {
  return (
    <div className={`px-6 py-4 border-b border-bambu-dark-tertiary ${className}`}>
      {children}
    </div>
  );
}

export function CardContent({ children, className = 'p-6' }: CardProps) {
  return <div className={className}>{children}</div>;
}
