import { describe, expect, it } from 'vitest'
import { displayProductName, humanizeProductId, platformCardTitle } from '../productDisplay'

describe('displayProductName', () => {
  it('prefers blueprint.product_name over a generic product_id slug', () => {
    expect(
      displayProductName({ productName: 'FinanceOps', productId: 'product' }),
    ).toBe('FinanceOps')
  })

  it('trims product_name and ignores a blank name', () => {
    expect(
      displayProductName({ productName: '  Product Platform  ', productId: 'product' }),
    ).toBe('Product Platform')
    expect(displayProductName({ productName: '   ', productId: 'product' })).toBe('Product')
  })

  it('uses another honest design name when product_name is missing', () => {
    expect(
      displayProductName({ altName: 'FinanceOps Desk', productId: 'product' }),
    ).toBe('FinanceOps Desk')
  })

  it('humanizes a slug product_id when no name is known', () => {
    expect(displayProductName({ productId: 'residential-lettings' })).toBe(
      'Residential Lettings',
    )
    expect(displayProductName({ productId: 'veterinary_care' })).toBe('Veterinary Care')
  })

  it('falls back to Untitled platform when nothing is known', () => {
    expect(displayProductName({})).toBe('Untitled platform')
  })
})

describe('platformCardTitle', () => {
  it('matches the Platforms card contract from #436', () => {
    expect(platformCardTitle(undefined, 'product')).toBe('Product')
    expect(platformCardTitle('  ', 'residential-lettings')).toBe('Residential Lettings')
    expect(platformCardTitle('FinanceOps', 'product')).toBe('FinanceOps')
    expect(platformCardTitle(null, null)).toBe('')
  })
})

describe('humanizeProductId', () => {
  it('title-cases hyphen and underscore slugs', () => {
    expect(humanizeProductId('product')).toBe('Product')
    expect(humanizeProductId('finance-ops')).toBe('Finance Ops')
  })
})
