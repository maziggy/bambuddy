import { createContext, useContext } from 'react';
import type { ReactNode, MouseEvent, HTMLAttributes } from 'react';

type CardDensity = 'normal' | 'dense';

const CardDensityContext = createContext<CardDensity>('normal');

export function CardDensityProvider({ density, children }: { density: CardDensity; children: ReactNode }) {
  return <CardDensityContext.Provider value={density}>{children}</CardDensityContext.Provider>;
}

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode;
  className?: string;
  onClick?: (e: MouseEvent) => void;
  onContextMenu?: (e: MouseEvent) => void;
}

interface CardSectionProps {
  children: ReactNode;
  className?: string;
  dense?: boolean;
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

export function CardHeader({ children, className = '', dense }: CardSectionProps) {
  const ctxDense = useContext(CardDensityContext) === 'dense';
  const isDense = dense ?? ctxDense;
  const padding = isDense ? 'px-4 py-2.5' : 'px-6 py-4';
  return (
    <div className={`${padding} border-b border-bambu-dark-tertiary ${className}`}>
      {children}
    </div>
  );
}

export function CardContent({ children, className = '', dense }: CardSectionProps) {
  const ctxDense = useContext(CardDensityContext) === 'dense';
  const isDense = dense ?? ctxDense;
  const padding = isDense ? 'p-4' : 'p-6';
  return <div className={`${padding} ${className}`}>{children}</div>;
}
