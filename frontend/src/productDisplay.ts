/** Human-facing platform title. Never prefer a slug like `product` over a real name. */

export function humanizeProductId(id: string): string {
  return id
    .replace(/[-_]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/\b\w/g, (ch) => ch.toUpperCase())
}

export function firstHonestName(...candidates: unknown[]): string | undefined {
  for (const raw of candidates) {
    if (typeof raw !== 'string') continue
    const named = raw.trim()
    if (named) return named
  }
  return undefined
}

/** Prefer blueprint product_name, then a humanized product_id, then the raw id. */
export function platformCardTitle(
  productName?: string | null,
  productId?: string | null,
): string {
  const named = firstHonestName(productName)
  if (named) return named
  const id = typeof productId === 'string' ? productId.trim() : ''
  if (!id) return ''
  return humanizeProductId(id) || id
}

/**
 * Prefer blueprint.product_name (or another honest session/design name),
 * then a humanized product_id, then the raw id.
 */
export function displayProductName(input: {
  productName?: unknown
  altName?: unknown
  productId?: unknown
}): string {
  const named = firstHonestName(input.productName, input.altName)
  if (named) return named
  const id = typeof input.productId === 'string' ? input.productId : null
  return platformCardTitle(null, id) || 'Untitled platform'
}
