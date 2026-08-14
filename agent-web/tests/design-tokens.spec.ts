import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const stylesheet = readFileSync(
  fileURLToPath(new URL('../src/styles/index.css', import.meta.url)),
  'utf8',
)

describe('design tokens', () => {
  it('defines the approved A-palette semantic tokens', () => {
    expect(stylesheet).toContain('--color-primary-500: #635bff;')
    expect(stylesheet).toContain('--color-accent-500: #36d6b0;')
    expect(stylesheet).toContain('--color-bg-sidebar: #171725;')
    expect(stylesheet).toContain('--color-bg-canvas: #f8f8fc;')
  })

  it('defines motion tokens and honors reduced-motion preferences', () => {
    expect(stylesheet).toContain('--duration-fast: 120ms;')
    expect(stylesheet).toContain('--ease-standard: cubic-bezier(.2, .8, .2, 1);')
    expect(stylesheet).toContain('@media (prefers-reduced-motion: reduce)')
  })
})
