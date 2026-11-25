/**
 * Records every camera stream src given to an <img>, and whether that img was
 * in the document at the moment (#3431).
 *
 * React sets props while it renders, before it commits anything, so a src
 * passed as a prop lands on an img that is not on the page yet -- and on the
 * img of every render React later throws away, which the browser opens all
 * the same. A stream src must only ever be set on an attached img.
 */
export interface StreamSrcRecord {
  img: HTMLImageElement;
  src: string;
  attached: boolean;
}

export function spyOnStreamSrc(match = /\/camera\/(stream|snapshot)/) {
  const records: StreamSrcRecord[] = [];
  const proto = HTMLImageElement.prototype;
  const descriptor = Object.getOwnPropertyDescriptor(proto, 'src')!;
  const setAttribute = Element.prototype.setAttribute;
  const note = (img: HTMLImageElement, value: unknown) => {
    const src = String(value);
    if (match.test(src)) records.push({ img, src, attached: img.isConnected });
  };
  Object.defineProperty(proto, 'src', {
    ...descriptor,
    set(this: HTMLImageElement, value: string) {
      note(this, value);
      descriptor.set!.call(this, value);
    },
  });
  Element.prototype.setAttribute = function (this: Element, name: string, value: string) {
    if (this instanceof HTMLImageElement && name === 'src') note(this, value);
    return setAttribute.call(this, name, value);
  };
  return {
    records,
    /** Stream srcs set on an img that was not in the document. */
    detached: () => records.filter(r => !r.attached),
    /** Imgs outside the document that still hold a stream src: open connections. */
    leaked: () =>
      [...new Set(records.map(r => r.img))].filter(img => !img.isConnected && match.test(img.getAttribute('src') ?? '')),
    restore: () => {
      Object.defineProperty(proto, 'src', descriptor);
      Element.prototype.setAttribute = setAttribute;
    },
  };
}
