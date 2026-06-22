/**
 * Tests for usePageFileDrop. Each "cancel path" gets its own case so a future
 * regression on any of the three (drag-out-of-window, Escape, dragend) is
 * pinned independently — #1510 reported the Archives overlay sticking after
 * cancel, and these cases enforce the document-level reset.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, act, createEvent } from '@testing-library/react';
import { usePageFileDrop } from '../../hooks/usePageFileDrop';

function makeFile(name: string, size = 1024): File {
  return new File(['x'.repeat(size)], name, { type: 'application/octet-stream' });
}

function Harness(props: {
  onFiles: (f: File[]) => void;
  onRejected?: () => void;
  extensions?: string[];
  disabled?: boolean;
}) {
  const { isDraggingOver, dragHandlers } = usePageFileDrop(props);
  return (
    <div data-testid="wrapper" {...dragHandlers}>
      {isDraggingOver && <div data-testid="overlay">overlay</div>}
      <div data-testid="child">child</div>
    </div>
  );
}

describe('usePageFileDrop', () => {
  it('shows the overlay on dragenter with files', () => {
    render(<Harness onFiles={vi.fn()} />);
    const wrapper = screen.getByTestId('wrapper');
    fireEvent.dragEnter(wrapper, { dataTransfer: { types: ['Files'], files: [] } });
    expect(screen.getByTestId('overlay')).toBeInTheDocument();
  });

  it('ignores dragenter for non-file payloads (text selection, dnd-kit)', () => {
    render(<Harness onFiles={vi.fn()} />);
    const wrapper = screen.getByTestId('wrapper');
    fireEvent.dragEnter(wrapper, { dataTransfer: { types: ['text/plain'], files: [] } });
    expect(screen.queryByTestId('overlay')).not.toBeInTheDocument();
  });

  // JSDOM doesn't propagate relatedTarget through fireEvent.dragLeave(elem, {...}),
  // so these three cases build the DragEvent manually and defineProperty the
  // field before dispatching.
  function dispatchDragLeave(wrapper: HTMLElement, related: Node | null) {
    const ev = createEvent.dragLeave(wrapper);
    Object.defineProperty(ev, 'relatedTarget', { value: related, configurable: true });
    fireEvent(wrapper, ev);
  }

  it('keeps the overlay when dragging over a child (relatedTarget inside wrapper)', () => {
    render(<Harness onFiles={vi.fn()} />);
    const wrapper = screen.getByTestId('wrapper');
    const child = screen.getByTestId('child');
    fireEvent.dragEnter(wrapper, { dataTransfer: { types: ['Files'], files: [] } });
    expect(screen.getByTestId('overlay')).toBeInTheDocument();

    dispatchDragLeave(wrapper, child);
    expect(screen.getByTestId('overlay')).toBeInTheDocument();
  });

  it('hides the overlay when dragLeave targets something outside the wrapper', () => {
    render(<Harness onFiles={vi.fn()} />);
    const wrapper = screen.getByTestId('wrapper');
    fireEvent.dragEnter(wrapper, { dataTransfer: { types: ['Files'], files: [] } });
    expect(screen.getByTestId('overlay')).toBeInTheDocument();

    const outside = document.createElement('div');
    document.body.appendChild(outside);
    dispatchDragLeave(wrapper, outside);
    expect(screen.queryByTestId('overlay')).not.toBeInTheDocument();
    document.body.removeChild(outside);
  });

  it('hides the overlay when relatedTarget is null (cursor left the window)', () => {
    render(<Harness onFiles={vi.fn()} />);
    const wrapper = screen.getByTestId('wrapper');
    fireEvent.dragEnter(wrapper, { dataTransfer: { types: ['Files'], files: [] } });
    expect(screen.getByTestId('overlay')).toBeInTheDocument();

    dispatchDragLeave(wrapper, null);
    expect(screen.queryByTestId('overlay')).not.toBeInTheDocument();
  });

  it('resets on document drop (cancel path: release outside any drop target)', () => {
    render(<Harness onFiles={vi.fn()} />);
    const wrapper = screen.getByTestId('wrapper');
    fireEvent.dragEnter(wrapper, { dataTransfer: { types: ['Files'], files: [] } });
    expect(screen.getByTestId('overlay')).toBeInTheDocument();

    act(() => {
      document.dispatchEvent(new Event('drop'));
    });
    expect(screen.queryByTestId('overlay')).not.toBeInTheDocument();
  });

  it('resets on document dragend (cancel path: drag aborted)', () => {
    render(<Harness onFiles={vi.fn()} />);
    const wrapper = screen.getByTestId('wrapper');
    fireEvent.dragEnter(wrapper, { dataTransfer: { types: ['Files'], files: [] } });
    expect(screen.getByTestId('overlay')).toBeInTheDocument();

    act(() => {
      document.dispatchEvent(new Event('dragend'));
    });
    expect(screen.queryByTestId('overlay')).not.toBeInTheDocument();
  });

  it('resets on Escape (cancel path: user aborts mid-drag)', () => {
    render(<Harness onFiles={vi.fn()} />);
    const wrapper = screen.getByTestId('wrapper');
    fireEvent.dragEnter(wrapper, { dataTransfer: { types: ['Files'], files: [] } });
    expect(screen.getByTestId('overlay')).toBeInTheDocument();

    act(() => {
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    });
    expect(screen.queryByTestId('overlay')).not.toBeInTheDocument();
  });

  it('passes dropped files to onFiles', () => {
    const onFiles = vi.fn();
    render(<Harness onFiles={onFiles} />);
    const wrapper = screen.getByTestId('wrapper');
    const file = makeFile('model.3mf');
    fireEvent.drop(wrapper, { dataTransfer: { files: [file] } });
    expect(onFiles).toHaveBeenCalledWith([file]);
  });

  it('filters by extensions and calls onRejected when nothing matches', () => {
    const onFiles = vi.fn();
    const onRejected = vi.fn();
    render(<Harness onFiles={onFiles} onRejected={onRejected} extensions={['.3mf']} />);
    const wrapper = screen.getByTestId('wrapper');
    const file = makeFile('image.png');
    fireEvent.drop(wrapper, { dataTransfer: { files: [file] } });
    expect(onFiles).not.toHaveBeenCalled();
    expect(onRejected).toHaveBeenCalled();
  });

  it('only passes matched files through when extensions filter mixed types', () => {
    const onFiles = vi.fn();
    render(<Harness onFiles={onFiles} extensions={['.3mf']} />);
    const wrapper = screen.getByTestId('wrapper');
    const a = makeFile('a.3mf');
    const b = makeFile('b.txt');
    fireEvent.drop(wrapper, { dataTransfer: { files: [a, b] } });
    expect(onFiles).toHaveBeenCalledWith([a]);
  });

  it('clears the overlay on a successful drop', () => {
    render(<Harness onFiles={vi.fn()} />);
    const wrapper = screen.getByTestId('wrapper');
    fireEvent.dragEnter(wrapper, { dataTransfer: { types: ['Files'], files: [] } });
    expect(screen.getByTestId('overlay')).toBeInTheDocument();
    fireEvent.drop(wrapper, { dataTransfer: { files: [makeFile('a.3mf')] } });
    expect(screen.queryByTestId('overlay')).not.toBeInTheDocument();
  });

  it('is a no-op when disabled', () => {
    const onFiles = vi.fn();
    render(<Harness onFiles={onFiles} disabled />);
    const wrapper = screen.getByTestId('wrapper');
    fireEvent.dragEnter(wrapper, { dataTransfer: { types: ['Files'], files: [] } });
    expect(screen.queryByTestId('overlay')).not.toBeInTheDocument();
    fireEvent.drop(wrapper, { dataTransfer: { files: [makeFile('a.3mf')] } });
    expect(onFiles).not.toHaveBeenCalled();
  });
});
