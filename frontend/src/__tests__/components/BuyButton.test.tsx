/**
 * The Buy button for a spool or product (#2540).
 */
import { describe, it, expect, vi } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '../utils';
import { BuyButton } from '../../components/BuyButton';
import type { BuyOption } from '../../utils/productLinks';

const option = (supplier_id: number, over: Partial<BuyOption> = {}): BuyOption => ({
  supplier_id,
  supplier_name: `Shop ${supplier_id}`,
  product_url: `https://shop${supplier_id}.example/p`,
  supplier_article_number: null,
  quoted_price_per_kg: null,
  is_purchase_source: false,
  ...over,
});

describe('BuyButton', () => {
  it('shows nothing without a page to open', () => {
    render(<BuyButton options={[]} />);

    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Buy' })).not.toBeInTheDocument();
  });

  it('is a link to the one page, opened apart, without opening what it sits in', async () => {
    const onRowClick = vi.fn();
    render(
      <div onClick={onRowClick}>
        <BuyButton options={[option(1)]} />
      </div>,
    );

    const link = screen.getByRole('link', { name: 'Buy at Shop 1' });
    expect(link).toHaveAttribute('href', 'https://shop1.example/p');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    link.addEventListener('click', (e) => e.preventDefault());
    await userEvent.setup().click(link);
    expect(onRowClick).not.toHaveBeenCalled();
  });

  it('lists several pages in a menu, with their quoted price', async () => {
    const onRowClick = vi.fn();
    const user = userEvent.setup();
    render(
      <div onClick={onRowClick}>
        <BuyButton options={[option(1, { is_purchase_source: true }), option(2, { quoted_price_per_kg: 19.5 })]} currencySymbol="€" />
      </div>,
    );

    await user.click(screen.getByRole('button', { name: 'Buy' }));

    const items = screen.getAllByRole('menuitem');
    expect(items.map((a) => a.getAttribute('href'))).toEqual(['https://shop1.example/p', 'https://shop2.example/p']);
    expect(items[1]).toHaveTextContent('€19.50/kg');
    expect(onRowClick).not.toHaveBeenCalled();
  });

  it('renders the menu outside a clipping table, fixed under its button', async () => {
    const user = userEvent.setup();
    render(
      <div data-testid="clip" style={{ overflowX: 'auto' }}>
        <BuyButton options={[option(1), option(2)]} />
      </div>,
    );

    await user.click(screen.getByRole('button', { name: 'Buy' }));

    const menu = screen.getByRole('menu');
    expect(screen.getByTestId('clip')).not.toContainElement(menu);
    expect(menu.parentElement).toBe(document.body);
    expect(menu.style.position).toBe('fixed');
  });

  it('closes on an outside click, and on a click on a page', async () => {
    const user = userEvent.setup();
    render(
      <div>
        <BuyButton options={[option(1), option(2)]} />
        <p>elsewhere</p>
      </div>,
    );

    await user.click(screen.getByRole('button', { name: 'Buy' }));
    expect(screen.getByRole('menu')).toBeInTheDocument();
    await user.click(screen.getByText('elsewhere'));
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });
});
