import type { ReactNode, MouseEvent } from 'react';

interface CardProps {
  children: ReactNode;
  className?: string;
  onClick?: (e: MouseEvent) => void;
  onContextMenu?: (e: MouseEvent) => void;
}

export function Card({ children, className = '', onClick, onContextMenu }: CardProps) {
  return (
    <div
      className={`bg-bambu-dark-secondary rounded-xl border border-bambu-dark-tertiary ${className}`}
      onClick={onClick}
      onContextMenu={onContextMenu}
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

export function CardContent({ children, className = '' }: CardProps) {
  return <div className={`p-6 ${className}`}>{children}</div>;
}
