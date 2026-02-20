import type { ReactNode, MouseEvent, HTMLAttributes, Ref } from 'react';

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode;
  className?: string;
  onClick?: (e: MouseEvent) => void;
  onContextMenu?: (e: MouseEvent) => void;
  ref?: Ref<HTMLDivElement>;
}

export function Card({ children, className = '', onClick, onContextMenu, ref, ...rest }: CardProps) {
  return (
    <div
      ref={ref}
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

export function CardContent({ children, className = '' }: CardProps) {
  return <div className={`p-6 ${className}`}>{children}</div>;
}
