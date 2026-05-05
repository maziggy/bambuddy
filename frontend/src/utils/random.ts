const FNV1A_32_OFFSET_BASIS = 0x811c9dc5;
const FNV1A_32_PRIME = 0x01000193;

/**
 * Computes a fast 32-bit FNV-1a hash for deterministic, non-security tasks.
 * Accepts any number of string/nullable-string inputs, takes measurements 
 * to avoid collisions, and combines them into a 32-bit hash.
 * Not cryptographically secure; use only for non-security-related use cases.
*/
export function hash_fnv1a32(...input: Array<string | null | undefined>): number {
    let hash = FNV1A_32_OFFSET_BASIS;
    const textEncoder = new TextEncoder();
    const emptyElement = textEncoder.encode('__|');
    for (const element of input) {
        if (typeof element === 'string') {
            hash = fnv1a32_update(hash, textEncoder.encode(element+'|'));
        } else if (element === null || element === undefined) {
            hash = fnv1a32_update(hash, emptyElement);
        }
    }
    return hash >>> 0;
}


function fnv1a32_update(hash: number, value: Uint8Array): number {
    for (const byte of value) {
        hash ^= byte;
        hash = Math.imul(hash, FNV1A_32_PRIME) >>> 0;
    }
    return hash;
}

export interface Mulberry32Sequence {
    next(): number;
    intBetween(from: number, to: number): number;
    floatBetween(from: number, to: number): number;
}

/**
 * Creates a fast deterministic PRNG sequence using Mulberry32.
 * Same seed will always produce the same sequence. 
 * Not cryptographically secure; use only for non-security-related use cases.
 */
export function random_mulberry32(seed: number): Mulberry32Sequence {
    const nextUint32 = (): number => {
        seed |= 0;
        seed = seed + 0x6D2B79F5 | 0;
        let imul = Math.imul(seed ^ seed >>> 15, 1 | seed);
        imul = imul + Math.imul(imul ^ imul >>> 7, 61 | imul) ^ imul;
        return (imul ^ imul >>> 14) >>> 0;
    };

    const nextNormalized = (from: number, to: number): number => {
        if (!Number.isFinite(from) || !Number.isFinite(to)) {
            throw new RangeError('from and to must be finite numbers');
        }
        if (from > to) {
            throw new RangeError('from must be less than or equal to to');
        }
        if (from === to) {
            return from;
        }
        return from + nextUint32() / 0xFFFFFFFF * (to - from);
    };

    return {
        next: () => {
            return nextUint32() / 0xFFFFFFFF;
        },
        floatBetween: (from: number, to: number): number => {
            return nextNormalized(from, to);
        },
        intBetween: (from: number, to: number): number => {
            if (!Number.isInteger(from) || !Number.isInteger(to)) {
                throw new RangeError('from and to must be integers');
            }
            return Math.round(nextNormalized(from, to));
        },
    };
}
